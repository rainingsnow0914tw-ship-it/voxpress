"""Hook adapter + watchdog tests.

None of these install a real hook. Following the non-live boundary in
`CONTRIBUTING.md`, `HotkeyHook._on_raw_event` is driven directly with fake
event objects shaped like the `keyboard` library's ``KeyboardEvent``.
"""

from __future__ import annotations

import ctypes
import queue
from dataclasses import dataclass

import pytest

from voxpress.commands import CommandType, WorkerEvent
from voxpress.hotkey_hook import CALLBACK_BUDGET_MS, HotkeyHook
from voxpress.hotkey_state import HotkeyConfig, State
from voxpress.keyevents import KeyId
from voxpress.watchdog import CONFIRMATIONS_REQUIRED, StuckModifierWatchdog
from voxpress.winkeys import (
    _INPUT,
    VK_LCONTROL,
    VK_LWIN,
    VK_RWIN,
    ModifierSnapshot,
    vk_for_key,
)

CONFIG = HotkeyConfig(
    main_scan_codes=frozenset({91, 92}),
    modifier_scan_codes=frozenset({29}),
)


@dataclass
class FakeKeyboardEvent:
    """Shaped like keyboard.KeyboardEvent — only the fields we read."""

    scan_code: int
    name: str
    event_type: str  # "down" / "up"


class Clock:
    def __init__(self) -> None:
        self.now = 500.0

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += ms / 1000.0


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def _record(self, event, **fields):
        self.events.append((event, fields))

    info = warn = error = debug = diagnostic = _record

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


@pytest.fixture
def hook():
    clock = Clock()
    logger = RecordingLogger()
    h = HotkeyHook(CONFIG, logger=logger, clock=clock)
    h.test_clock = clock  # type: ignore[attr-defined]
    h.test_logger = logger  # type: ignore[attr-defined]
    return h


def drain(hook: HotkeyHook) -> list[CommandType]:
    out = []
    while True:
        try:
            out.append(hook.commands.get_nowait().type)
        except queue.Empty:
            return out


def press_hotkey(hook: HotkeyHook, hold_ms: float) -> list[bool]:
    """Full Ctrl+Win cycle.  Returns the boolean each callback returned."""
    clock = hook.test_clock  # type: ignore[attr-defined]
    returns = [
        hook._on_raw_event(FakeKeyboardEvent(29, "left ctrl", "down")),
        hook._on_raw_event(FakeKeyboardEvent(91, "left windows", "down")),
    ]
    clock.advance_ms(hold_ms)
    returns.append(hook._on_raw_event(FakeKeyboardEvent(91, "left windows", "up")))
    returns.append(hook._on_raw_event(FakeKeyboardEvent(29, "left ctrl", "up")))
    return returns


# --- callback contract -------------------------------------------------


def test_callback_returns_false_only_for_the_windows_key(hook: HotkeyHook) -> None:
    returns = press_hotkey(hook, hold_ms=400)
    # ctrl down passes, win down swallowed, win up swallowed, ctrl up passes
    assert returns == [True, False, False, True]


def test_short_and_long_press_produce_the_right_commands(hook: HotkeyHook) -> None:
    press_hotkey(hook, hold_ms=120)
    assert drain(hook) == [CommandType.START_RECORDING]
    assert hook.machine.state is State.TOGGLE_RECORDING

    press_hotkey(hook, hold_ms=80)
    assert drain(hook) == [CommandType.STOP_RECORDING]


def test_callback_stays_inside_its_latency_budget(hook: HotkeyHook) -> None:
    """Callback latency is measured, not merely asserted in prose.

    The old implementation opened a PortAudio stream here — hundreds of
    milliseconds, past LowLevelHooksTimeout.  Everything on this path is now
    dict/set work plus a queue put.
    """
    for _ in range(500):
        press_hotkey(hook, hold_ms=50)
        hook.acknowledge(WorkerEvent.RECORDING_STOPPED, hook.machine.generation)
        hook.acknowledge(WorkerEvent.TRANSCRIPTION_FINISHED, hook.machine.generation)
        drain(hook)

    assert hook.total_events == 2000
    assert hook.max_latency_ms < CALLBACK_BUDGET_MS, (
        f"worst callback {hook.max_latency_ms:.3f} ms exceeds the {CALLBACK_BUDGET_MS} ms budget"
    )


def test_unrelated_keys_are_always_forwarded(hook: HotkeyHook) -> None:
    for scan, name in ((30, "a"), (57, "space"), (28, "enter")):
        assert hook._on_raw_event(FakeKeyboardEvent(scan, name, "down")) is True
        assert hook._on_raw_event(FakeKeyboardEvent(scan, name, "up")) is True
    assert drain(hook) == []


def test_hook_exception_fails_open(hook: HotkeyHook, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("hook blew up")

    monkeypatch.setattr(hook.machine, "handle_key_event", boom)
    released: list[int] = []
    monkeypatch.setattr("voxpress.winkeys.send_key_up", lambda vk: released.append(vk) or True)

    assert hook._on_raw_event(FakeKeyboardEvent(91, "left windows", "down")) is True
    assert hook.exception_count == 1
    assert "hotkey_hook_exception" in hook.test_logger.names()  # type: ignore[attr-defined]


def test_queue_overflow_fails_open_and_recovers(hook: HotkeyHook, monkeypatch) -> None:
    released: list[int] = []
    monkeypatch.setattr("voxpress.winkeys.send_key_up", lambda vk: released.append(vk) or True)
    # Fill the queue so the next command cannot be enqueued.
    while True:
        try:
            hook.commands.put_nowait(object())  # type: ignore[arg-type]
        except queue.Full:
            break

    hook._on_raw_event(FakeKeyboardEvent(29, "left ctrl", "down"))
    hook._on_raw_event(FakeKeyboardEvent(91, "left windows", "down"))

    assert hook.overflow_count == 1
    assert hook.machine.state is State.IDLE, "did not reset after overflow"
    assert "hotkey_command_queue_overflow" in hook.test_logger.names()  # type: ignore[attr-defined]


def test_recover_releases_every_swallowed_key(hook: HotkeyHook, monkeypatch) -> None:
    released: list[int] = []
    monkeypatch.setattr("voxpress.winkeys.send_key_up", lambda vk: released.append(vk) or True)

    hook._on_raw_event(FakeKeyboardEvent(29, "left ctrl", "down"))
    hook._on_raw_event(FakeKeyboardEvent(91, "left windows", "down"))
    assert hook.machine.pending_suppressed_keys() == (KeyId(91, "left windows"),)

    hook.recover("shutdown")

    assert released == [VK_LWIN], "a swallowed key-down was not released"
    assert hook.machine.pending_suppressed_keys() == ()


def test_send_input_layout_matches_native_windows_abi() -> None:
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(_INPUT) == expected


def test_generic_right_windows_name_uses_physical_scan_code() -> None:
    assert vk_for_key(91, "windows") == VK_LWIN
    assert vk_for_key(92, "windows") == VK_RWIN
    assert vk_for_key(36, "j") == ord("J")


def test_recovery_releases_generic_right_windows_key(monkeypatch) -> None:
    released: list[int] = []
    monkeypatch.setattr("voxpress.winkeys.send_key_up", lambda vk: released.append(vk) or True)
    hook = HotkeyHook(CONFIG)
    hook.release_keys((KeyId(92, "windows"),), reason="synthetic_test")
    assert released == [VK_RWIN]


def test_self_injection_guard_prevents_recursion(hook: HotkeyHook) -> None:
    """Our own Ctrl+V must not be read as a hotkey press."""
    hook.begin_self_injection()
    hook._on_raw_event(FakeKeyboardEvent(29, "left ctrl", "down"))
    assert hook._on_raw_event(FakeKeyboardEvent(91, "left windows", "down")) is True
    assert drain(hook) == []
    hook.end_self_injection()


def test_snapshot_exposes_what_status_needs(hook: HotkeyHook) -> None:
    press_hotkey(hook, hold_ms=50)
    snap = hook.snapshot()
    assert snap["state"] == State.TOGGLE_RECORDING.value
    assert snap["pending_suppressed"] == []
    assert snap["events"] == 4
    assert snap["stats"]["starts"] == 1


# --- watchdog ----------------------------------------------------------


class FakeHook:
    def __init__(self, state=State.IDLE.value, pending=(), candidates=()):
        self._state = state
        self._pending = list(pending)
        self._candidates = tuple(candidates)
        self.injection_calls = 0

    def snapshot(self):
        return {"state": self._state, "pending_suppressed": self._pending}

    def begin_self_injection(self):
        self.injection_calls += 1

    def end_self_injection(self):
        pass

    def watchdog_recovery_candidates(self):
        return self._candidates


def make_watchdog(down: set[int], hook=None, auto_release: bool = True, candidates=()):
    released: list[int] = []
    logger = RecordingLogger()
    wd = StuckModifierWatchdog(
        hook or FakeHook(candidates=candidates),
        logger=logger,
        auto_release=auto_release,
        snapshot_fn=lambda: ModifierSnapshot(frozenset(down)),
        release_fn=lambda vk: released.append(vk) or True,
    )
    return wd, released, logger


def test_watchdog_needs_two_confirmations_before_acting() -> None:
    wd, released, _ = make_watchdog({VK_LWIN}, candidates={VK_LWIN})
    assert wd.check_once() == ()  # first observation: suspicion only
    assert released == []
    assert wd.check_once() == (VK_LWIN,)
    assert released == [VK_LWIN]


def test_watchdog_ignores_a_key_that_is_genuinely_part_of_a_live_cycle() -> None:
    hook = FakeHook(
        state=State.ARMED_RECORDING.value,
        pending=["left windows#91"],
        candidates={VK_LWIN},
    )
    wd, released, _ = make_watchdog({VK_LWIN}, hook=hook)
    for _ in range(10):
        assert wd.check_once() == ()
    assert released == [], "released a key that was legitimately held"


def test_watchdog_does_not_auto_release_ctrl() -> None:
    """Ctrl is reported but never force-released — it is far likelier to be real."""
    wd, released, logger = make_watchdog({VK_LCONTROL}, candidates={VK_LCONTROL})
    for _ in range(CONFIRMATIONS_REQUIRED):
        wd.check_once()
    assert released == []
    assert "stuck_modifier_detected" in logger.names()
    assert wd.detections == 1
    assert wd.recoveries == 0


def test_watchdog_forgets_a_key_that_clears_itself() -> None:
    down: set[int] = {VK_LWIN}
    wd, released, _ = make_watchdog(down, candidates={VK_LWIN})
    wd.check_once()
    down.clear()
    assert wd.check_once() == ()
    assert released == []


def test_watchdog_auto_release_can_be_disabled() -> None:
    wd, released, logger = make_watchdog({VK_LWIN}, auto_release=False, candidates={VK_LWIN})
    for _ in range(CONFIRMATIONS_REQUIRED):
        wd.check_once()
    assert released == []
    assert wd.detections == 1
    assert wd.health()["auto_release"] is False


def test_watchdog_never_releases_a_real_key_without_suppression_provenance() -> None:
    wd, released, logger = make_watchdog({VK_LWIN})
    for _ in range(CONFIRMATIONS_REQUIRED + 3):
        assert wd.check_once() == ()
    assert released == []
    assert "stuck_modifier_detected" not in logger.names()


def test_completed_suppressed_pair_is_a_short_lived_recovery_candidate(
    hook: HotkeyHook,
) -> None:
    press_hotkey(hook, hold_ms=400)
    assert hook.watchdog_recovery_candidates() == (VK_LWIN,)
    hook.test_clock.advance_ms(2100)  # type: ignore[attr-defined]
    assert hook.watchdog_recovery_candidates() == ()
