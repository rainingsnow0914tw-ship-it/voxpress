"""Deterministic Ctrl+Win sequence tests — the hotkey safety contract.

Every test drives the state machine through an explicit event sequence and
asserts four things:

1. the disposition applied to each event;
2. that exactly the expected START/STOP commands were emitted;
3. the resulting state;
4. **that the OS is left with no key held down** — checked by
   :class:`OsKeyboardModel`, which replays the dispositions the way Windows
   would and refuses to let a test pass with a dangling key-down.

No test sleeps.  No test installs a hook.  Time is injected.
"""

from __future__ import annotations

import pytest

from voxpress.commands import CommandType, WorkerEvent
from voxpress.hotkey_state import (
    DEFAULT_HOLD_THRESHOLD_SEC,
    Decision,
    HotkeyConfig,
    HotkeyStateMachine,
    State,
)
from voxpress.keyevents import Disposition, EventType, KeyEvent, KeyId

LCTRL = KeyId(29, "left ctrl")
RCTRL = KeyId(29, "right ctrl")
LWIN = KeyId(91, "left windows")
RWIN = KeyId(92, "right windows")
LETTER_A = KeyId(30, "a")
LETTER_J = KeyId(36, "j")
LSHIFT = KeyId(42, "left shift")
RSHIFT = KeyId(54, "right shift")

CONFIG = HotkeyConfig(
    main_scan_codes=frozenset({91, 92}),
    modifier_scan_codes=frozenset({29}),
    hold_threshold_sec=DEFAULT_HOLD_THRESHOLD_SEC,
)


class OsKeyboardModel:
    """What Windows believes is held down, given the dispositions we applied.

    This is the whole point of the P0 repair: a suppressed DOWN followed by a
    forwarded UP (or the reverse) is exactly how ``LWin`` got stuck.  Any test
    that produces that pattern fails here rather than silently passing.
    """

    def __init__(self) -> None:
        self.down: set[KeyId] = set()
        self.orphan_ups: list[KeyId] = []

    def apply(self, event: KeyEvent, decision: Decision) -> None:
        if decision.disposition is Disposition.SUPPRESS:
            return
        if event.is_down:
            self.down.add(event.key)
        else:
            if event.key in self.down:
                self.down.discard(event.key)
            else:
                # Harmless (Windows opens the Start menu on a *matched* Win
                # press), but recorded so tests can assert on it.
                self.orphan_ups.append(event.key)

    def assert_nothing_held(self) -> None:
        assert not self.down, f"OS left holding {[str(k) for k in self.down]}"


class Driver:
    """Feeds events to the machine while maintaining the OS model."""

    def __init__(self, config: HotkeyConfig = CONFIG, start: float = 1000.0) -> None:
        self.machine = HotkeyStateMachine(config)
        self.os = OsKeyboardModel()
        self.now = start
        self.commands: list = []
        self.decisions: list[tuple[KeyEvent, Decision]] = []

    def advance_ms(self, milliseconds: float) -> None:
        self.now += milliseconds / 1000.0

    def send(self, key: KeyId, event_type: EventType, *, injected: bool = False) -> Decision:
        event = KeyEvent(key, event_type, self.now, injected=injected)
        decision = self.machine.handle_key_event(event)
        self.os.apply(event, decision)
        self.commands.extend(decision.commands)
        self.decisions.append((event, decision))
        return decision

    def down(self, key: KeyId, **kw) -> Decision:
        return self.send(key, EventType.DOWN, **kw)

    def up(self, key: KeyId, **kw) -> Decision:
        return self.send(key, EventType.UP, **kw)

    def command_types(self) -> list[CommandType]:
        return [c.type for c in self.commands]

    def finish_recording(self) -> None:
        """Walk the worker acknowledgements for a completed stop."""
        gen = self.machine.generation
        self.machine.handle_worker_event(WorkerEvent.RECORDING_STOPPED, gen)
        self.machine.handle_worker_event(WorkerEvent.TRANSCRIPTION_FINISHED, gen)

    def ack_started(self) -> None:
        self.machine.handle_worker_event(WorkerEvent.RECORDING_STARTED, self.machine.generation)


# =====================================================================
# 4.1  Short press / toggle
# =====================================================================


@pytest.mark.parametrize("press_ms", [0.0, 1.0, 100.0, 247.0, 250.0, 299.0, 299.999])
def test_short_press_latches_toggle_recording(press_ms: float) -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(press_ms)
    d.up(LWIN)
    d.up(LCTRL)

    assert d.command_types() == [CommandType.START_RECORDING]
    assert d.machine.state is State.TOGGLE_RECORDING
    assert d.machine.recording is True
    d.os.assert_nothing_held()


def test_second_short_press_stops_exactly_once() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(120)
    d.up(LWIN)
    d.up(LCTRL)
    d.ack_started()

    d.advance_ms(4000)
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(90)
    d.up(LWIN)
    d.up(LCTRL)

    assert d.command_types() == [CommandType.START_RECORDING, CommandType.STOP_RECORDING]
    assert d.machine.state is State.STOPPING
    d.finish_recording()
    assert d.machine.state is State.IDLE
    d.os.assert_nothing_held()


def test_ctrl_released_before_win() -> None:
    """The order that used to strand LWin: modifier leaves first."""
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(150)
    d.up(LCTRL)  # modifier first
    d.up(LWIN)

    assert d.command_types() == [CommandType.START_RECORDING]
    assert d.machine.state is State.TOGGLE_RECORDING
    d.os.assert_nothing_held()


def test_win_released_before_ctrl() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(150)
    d.up(LWIN)
    d.up(LCTRL)
    assert d.command_types() == [CommandType.START_RECORDING]
    d.os.assert_nothing_held()


def test_right_ctrl_and_right_win_variants() -> None:
    d = Driver()
    d.down(RCTRL)
    d.down(RWIN)
    d.advance_ms(100)
    d.up(RWIN)
    d.up(RCTRL)
    assert d.command_types() == [CommandType.START_RECORDING]
    assert d.machine.state is State.TOGGLE_RECORDING
    d.os.assert_nothing_held()


def test_win_first_then_ctrl_does_not_trigger_and_stays_paired() -> None:
    """Documented behaviour: the modifier must lead.

    the user's roll is pinky-Ctrl then thumb-Win, so this is the unnatural order.
    What matters is that it degrades to *ordinary Windows behaviour* — the
    Start menu opens, nothing records, and no key is stranded.
    """
    d = Driver()
    d.down(LWIN)  # bare Win: forwarded
    d.down(LCTRL)
    d.advance_ms(150)
    d.up(LWIN)
    d.up(LCTRL)

    assert d.command_types() == []
    assert d.machine.state is State.IDLE
    d.os.assert_nothing_held()
    assert d.os.orphan_ups == []


def test_repeated_win_down_before_up_emits_one_start() -> None:
    """Auto-repeat inside a *short* press must not multiply the START."""
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    for _ in range(5):  # Windows auto-repeat, 5 x 30 ms = 150 ms => still short
        d.advance_ms(30)
        d.down(LWIN)
    d.up(LWIN)
    d.up(LCTRL)

    assert d.command_types() == [CommandType.START_RECORDING]
    assert d.machine.stats["repeats_absorbed"] == 5
    assert d.machine.state is State.TOGGLE_RECORDING
    d.os.assert_nothing_held()


def test_duplicate_down_up_from_two_endpoints() -> None:
    """Two keyboards (or a KVM) echoing the same key must not unbalance it."""
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.down(LWIN)  # echo
    d.advance_ms(120)
    d.up(LWIN)
    d.up(LWIN)  # echo
    d.up(LCTRL)

    assert d.command_types() == [CommandType.START_RECORDING]
    d.os.assert_nothing_held()
    assert d.os.orphan_ups == [], "duplicate UP reached the OS unpaired"


def test_bounce_up_reuses_paired_disposition() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(50)
    first = d.up(LWIN)
    d.advance_ms(5)
    bounce = d.up(LWIN)
    assert first.disposition is bounce.disposition is Disposition.SUPPRESS
    d.up(LCTRL)
    d.os.assert_nothing_held()


# =====================================================================
# 4.2  Long press / hold
# =====================================================================


@pytest.mark.parametrize("press_ms", [300.0, 300.001, 301.0, 1000.0, 5000.0, 300_000.0])
def test_long_press_records_while_held_and_stops_on_release(press_ms: float) -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(press_ms)
    d.up(LWIN)
    d.up(LCTRL)

    assert d.command_types() == [CommandType.START_RECORDING, CommandType.STOP_RECORDING]
    assert d.machine.state is State.STOPPING
    d.os.assert_nothing_held()


def test_exact_threshold_is_long_press() -> None:
    """The boundary has one definition and it is asserted, not assumed."""
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(DEFAULT_HOLD_THRESHOLD_SEC * 1000)
    d.up(LWIN)
    d.up(LCTRL)
    assert CommandType.STOP_RECORDING in d.command_types()


def test_repeats_during_hold_emit_no_extra_start() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    for _ in range(100):
        d.advance_ms(30)
        d.down(LWIN)
    d.up(LWIN)
    d.up(LCTRL)
    assert d.command_types() == [CommandType.START_RECORDING, CommandType.STOP_RECORDING]
    d.os.assert_nothing_held()


def test_modifier_released_before_main_key_during_long_hold() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(500)
    d.up(LCTRL)
    d.advance_ms(500)
    d.up(LWIN)
    assert d.command_types() == [CommandType.START_RECORDING, CommandType.STOP_RECORDING]
    d.os.assert_nothing_held()


# =====================================================================
# Regression: repeat after modifier release must not strand Windows
# =====================================================================


def test_short_press_repeat_after_ctrl_release_never_strands_win() -> None:
    """247 ms Ctrl+Win must not strand LWin.  This is the reported incident.

    The previous flaw: a repeat DOWN arriving after Ctrl was released
    took its ``return True`` branch and reached Windows, while the stale
    ``swallowed_down`` flag caused the following UP to be swallowed.  Windows
    kept LWin down until the user pressed and released the physical key.

    Here the repeat reuses the DOWN's recorded disposition, so both halves of
    the pair agree no matter what the modifiers did in between.
    """
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(120)
    d.up(LCTRL)  # Ctrl leaves early — the trigger condition
    d.advance_ms(60)
    d.down(LWIN)  # auto-repeat with no modifier held
    d.advance_ms(67)
    d.up(LWIN)  # total 247 ms

    assert d.command_types() == [CommandType.START_RECORDING]
    d.os.assert_nothing_held()
    assert d.os.orphan_ups == []

    # And the recording is still usable afterwards, not wedged.
    assert d.machine.state is State.TOGGLE_RECORDING


def test_regression_left_and_right_win_do_not_share_pairing_state() -> None:
    """A single boolean cannot safely represent both Windows keys.

    Interleaving them under that design leaves one of the two unbalanced.
    """
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(40)
    d.down(RWIN)  # both Windows keys held at once
    d.advance_ms(40)
    d.up(LWIN)
    d.advance_ms(40)
    d.up(RWIN)
    d.up(LCTRL)

    assert d.command_types() == [CommandType.START_RECORDING]
    d.os.assert_nothing_held()


def test_regression_hook_timeout_cannot_desync_pairing() -> None:
    """If Windows ignores a suppression, the pair still agrees.

    We cannot stop Windows from delivering an event whose callback overran
    ``LowLevelHooksTimeout``.  What we can guarantee is that our *decision* for
    the UP equals our decision for the DOWN, so the OS either sees both or
    neither — never a lone DOWN.
    """
    d = Driver()
    d.down(LCTRL)
    down_decision = d.down(LWIN)
    d.advance_ms(150)
    up_decision = d.up(LWIN)
    d.up(LCTRL)

    assert down_decision.disposition is up_decision.disposition
    d.os.assert_nothing_held()


# =====================================================================
# 4.3  Busy / error sequences
# =====================================================================


def test_press_while_transcribing_gives_a_cue_and_stays_paired() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(400)
    d.up(LWIN)
    d.up(LCTRL)
    d.machine.handle_worker_event(WorkerEvent.RECORDING_STOPPED, d.machine.generation)
    assert d.machine.state is State.TRANSCRIBING

    d.advance_ms(100)
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(80)
    d.up(LWIN)
    d.up(LCTRL)

    assert d.command_types()[-1] is CommandType.BUSY_CUE
    assert d.machine.state is State.TRANSCRIBING
    d.os.assert_nothing_held()


def test_start_failure_returns_to_idle_and_release_emits_no_stop() -> None:
    """Microphone never opened.  The pending release must not stop nothing."""
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.machine.handle_worker_event(
        WorkerEvent.RECORDING_START_FAILED, d.machine.generation, "AUDIO_NO_DEVICE"
    )
    assert d.machine.state is State.IDLE

    d.advance_ms(500)
    d.up(LWIN)
    d.up(LCTRL)

    assert d.command_types() == [CommandType.START_RECORDING]
    assert d.machine.state is State.IDLE
    assert d.machine.last_error == "AUDIO_NO_DEVICE"
    d.os.assert_nothing_held()


def test_stale_worker_result_cannot_stop_a_newer_recording() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(400)
    d.up(LWIN)
    d.up(LCTRL)
    stale_generation = d.machine.generation
    d.finish_recording()
    assert d.machine.state is State.IDLE

    d.advance_ms(1000)
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(50)
    d.up(LWIN)
    d.up(LCTRL)
    assert d.machine.state is State.TOGGLE_RECORDING

    d.machine.handle_worker_event(WorkerEvent.TRANSCRIPTION_FINISHED, stale_generation)

    assert d.machine.state is State.TOGGLE_RECORDING, "stale result killed a live recording"
    assert d.machine.stats["stale_worker_events"] == 1


def test_rapid_start_stop_start() -> None:
    d = Driver()
    for index in range(3):
        d.down(LCTRL)
        d.down(LWIN)
        d.advance_ms(400)
        d.up(LWIN)
        d.up(LCTRL)
        d.finish_recording()
        d.advance_ms(20)
    assert d.command_types().count(CommandType.START_RECORDING) == 3
    assert d.command_types().count(CommandType.STOP_RECORDING) == 3
    d.os.assert_nothing_held()


def test_force_reset_reports_keys_needing_synthetic_release() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    assert d.machine.pending_suppressed_keys() == (LWIN,)

    commands = d.machine.force_reset("hook_exception")
    assert [c.type for c in commands] == [CommandType.CANCEL_RECORDING]
    assert d.machine.state is State.IDLE
    assert d.machine.pending_suppressed_keys() == ()

    # After a reset the stranded UP is unowned and must fail open.
    decision = d.up(LWIN)
    assert decision.disposition is Disposition.PASS


def test_shutdown_while_holding_reports_pending_key() -> None:
    d = Driver()
    d.down(LCTRL)
    d.down(LWIN)
    d.advance_ms(2000)
    assert d.machine.pending_suppressed_keys() == (LWIN,)
    assert d.machine.state is State.ARMED_RECORDING


# =====================================================================
# 4.4  Suppression safety
# =====================================================================


def test_ordinary_typing_is_untouched() -> None:
    d = Driver()
    for _ in range(50):
        assert d.down(LETTER_A).disposition is Disposition.PASS
        assert d.up(LETTER_A).disposition is Disposition.PASS
    d.os.assert_nothing_held()


def test_modifiers_are_never_suppressed() -> None:
    """A stuck Ctrl is impossible by construction, not by careful bookkeeping."""
    d = Driver()
    for key in (LCTRL, RCTRL):
        assert d.down(key).disposition is Disposition.PASS
        assert d.up(key).disposition is Disposition.PASS
    d.os.assert_nothing_held()


def test_single_win_without_ctrl_keeps_the_start_menu() -> None:
    d = Driver()
    assert d.down(LWIN).disposition is Disposition.PASS
    d.advance_ms(80)
    assert d.up(LWIN).disposition is Disposition.PASS
    assert d.command_types() == []
    d.os.assert_nothing_held()


def test_injected_events_do_not_recurse_into_the_feature() -> None:
    """Our own Ctrl+V must not look like a hotkey press."""
    d = Driver()
    d.down(LCTRL)
    decision = d.down(LWIN, injected=True)
    assert decision.disposition is Disposition.PASS
    assert d.command_types() == []
    d.up(LWIN, injected=True)
    d.up(LCTRL)
    d.os.assert_nothing_held()


def test_pairing_survives_the_event_name_changing_mid_cycle() -> None:
    """The `keyboard` library derives a name from a modifier-sensitive table.

    If the DOWN is reported as "left windows" and the UP as something else, a
    name-sensitive identity would fail to pair them and strand the key.  KeyId
    compares on scan code only, so the pair still resolves.
    """
    d = Driver()
    d.down(LCTRL)
    down = d.down(KeyId(91, "left windows"))
    d.advance_ms(150)
    up = d.up(KeyId(91, "windows"))  # same physical key, different reported name
    d.up(LCTRL)

    assert down.disposition is up.disposition is Disposition.SUPPRESS
    assert up.note == "hotkey_up", "the pair was not recognised"
    d.os.assert_nothing_held()


def test_left_and_right_win_remain_distinct_despite_name_insensitivity() -> None:
    assert KeyId(91, "left windows") == KeyId(91, "anything else")
    assert KeyId(91, "left windows") != KeyId(92, "left windows")
    assert hash(KeyId(91, "a")) == hash(KeyId(91, "b"))


def test_unpaired_up_after_late_hook_install_fails_open() -> None:
    """Hook installed while the user already held the key."""
    d = Driver()
    decision = d.up(LWIN)
    assert decision.disposition is Disposition.PASS
    assert d.machine.stats["unpaired_ups"] == 1


def test_every_suppressed_down_has_a_matching_up_over_a_long_random_walk() -> None:
    """Property check: 2000 mixed events, never a stranded key."""
    import random

    rng = random.Random(20260805)
    d = Driver()
    keys = [LCTRL, RCTRL, LWIN, RWIN, LETTER_A]
    for _ in range(2000):
        key = rng.choice(keys)
        d.advance_ms(rng.choice([1, 5, 30, 120, 310, 700]))
        d.send(key, EventType.DOWN if rng.random() < 0.5 else EventType.UP)
        if rng.random() < 0.05:
            d.machine.handle_worker_event(
                rng.choice(list(WorkerEvent)), d.machine.generation, "synthetic"
            )

    # Release whatever the walk left held, in both orders.
    for key in (LWIN, RWIN, LCTRL, RCTRL, LETTER_A):
        d.advance_ms(400)
        d.send(key, EventType.UP)

    d.os.assert_nothing_held()


def test_non_suppressing_config_still_pairs() -> None:
    """An ordinary main key such as `z` is forwarded but still tracked."""
    config = HotkeyConfig(
        main_scan_codes=frozenset({44}),
        modifier_scan_codes=frozenset({56}),
        suppress_main_key=False,
    )
    d = Driver(config)
    alt = KeyId(56, "left alt")
    z = KeyId(44, "z")
    d.down(alt)
    assert d.down(z).disposition is Disposition.PASS
    d.advance_ms(500)
    assert d.up(z).disposition is Disposition.PASS
    d.up(alt)
    assert d.command_types() == [CommandType.START_RECORDING, CommandType.STOP_RECORDING]
    d.os.assert_nothing_held()


def test_every_logical_modifier_group_is_required() -> None:
    config = HotkeyConfig(
        main_scan_codes=frozenset({36}),
        modifier_scan_codes=frozenset({29, 3613, 42, 54}),
        suppress_main_key=False,
        modifier_scan_code_groups=(
            frozenset({29, 3613}),
            frozenset({42, 54}),
        ),
    )
    d = Driver(config)

    d.down(LCTRL)
    assert d.down(LETTER_J).note == "bare_main_key"
    d.up(LETTER_J)
    d.up(LCTRL)
    assert d.command_types() == []

    d.down(LCTRL)
    d.down(RSHIFT)
    assert d.down(LETTER_J).note == "hotkey_down"
    d.advance_ms(400)
    d.up(LETTER_J)
    d.up(RSHIFT)
    d.up(LCTRL)
    assert d.command_types() == [CommandType.START_RECORDING, CommandType.STOP_RECORDING]
    d.os.assert_nothing_held()
