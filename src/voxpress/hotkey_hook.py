"""The global hook adapter: classify, decide, enqueue, return.  Nothing else.

the hotkey safety contract requires the low-level callback to do no heavy
work.  The previous implementation opened and closed a PortAudio stream inside
this callback, which is both the latency source and one of the two routes to a
stranded Windows key (see :mod:`voxpress.hotkey_state`).

What happens here now, per event, is: two set lookups, a dict lookup, a small
state transition, and a non-blocking queue put.  :attr:`HotkeyHook.max_latency_ms`
records the worst observed callback so the claim is measured, not asserted.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from voxpress import winkeys
from voxpress.commands import Command, WorkerEvent
from voxpress.hotkey_state import HotkeyConfig, HotkeyStateMachine
from voxpress.keyevents import EventType, KeyEvent, KeyId

#: Bounded so a runaway producer cannot exhaust memory.  A human pressing keys
#: cannot realistically fill this; overflow is treated as a fault, not as
#: back-pressure, and resolves by failing open.
COMMAND_QUEUE_MAXSIZE = 512

#: A callback slower than this is a warning sign: Windows stops honouring the
#: return value of a low-level hook that overruns LowLevelHooksTimeout.
CALLBACK_BUDGET_MS = 5.0

#: While we are synthesising input (Ctrl+V, a recovery key-up), events are
#: treated as injected so the feature cannot trigger itself.
SELF_INJECTION_WINDOW_SEC = 0.75
WATCHDOG_RECOVERY_WINDOW_SEC = 2.0


class HotkeyHook:
    """Owns the state machine and the command queue; installs the OS hook."""

    def __init__(
        self,
        config: HotkeyConfig,
        *,
        logger=None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.machine = HotkeyStateMachine(config)
        self.commands: queue.Queue[Command] = queue.Queue(COMMAND_QUEUE_MAXSIZE)
        self._lock = threading.Lock()
        self._logger = logger
        self._clock = clock
        self._installed_handler: Any = None
        self._self_injection_until = 0.0

        self.max_latency_ms = 0.0
        self.total_events = 0
        self.overflow_count = 0
        self.exception_count = 0

    # -- installation ----------------------------------------------------

    def install(self) -> None:
        """Install the suppressing global hook.  Windows only."""
        import keyboard  # imported late: unit tests never need it

        self._installed_handler = keyboard.hook(self._on_raw_event, suppress=True)
        if self._logger:
            self._logger.info(
                "hotkey_hook_installed",
                main_scan_codes=sorted(self.config.main_scan_codes),
                modifier_scan_codes=sorted(self.config.modifier_scan_codes),
                threshold_ms=round(self.config.hold_threshold_sec * 1000),
                suppress=self.config.suppress_main_key,
            )

    def uninstall(self) -> None:
        if self._installed_handler is None:
            return
        try:
            import keyboard

            keyboard.unhook(self._installed_handler)
        except Exception:
            pass
        finally:
            self._installed_handler = None
        if self._logger:
            self._logger.info("hotkey_hook_removed")

    # -- self-injection guard --------------------------------------------

    def begin_self_injection(self) -> None:
        """Mark the start of input we generate ourselves (paste, recovery)."""
        self._self_injection_until = self._clock() + SELF_INJECTION_WINDOW_SEC

    def end_self_injection(self) -> None:
        self._self_injection_until = 0.0

    @property
    def _self_injecting(self) -> bool:
        return self._clock() < self._self_injection_until

    # -- the callback -----------------------------------------------------

    def _on_raw_event(self, event: Any) -> bool:
        """Return True to let Windows see the event, False to swallow it.

        Any failure returns True.  Failing open means the worst case is a
        hotkey that stops working, never a keyboard the user cannot use.
        """
        started = time.perf_counter()
        try:
            decision = self._decide(event)
        except Exception as exc:  # pragma: no cover - defensive
            self.exception_count += 1
            self._handle_hook_exception(exc)
            return True
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if elapsed_ms > self.max_latency_ms:
                self.max_latency_ms = elapsed_ms
            if elapsed_ms > CALLBACK_BUDGET_MS and self._logger:
                self._logger.warn("hotkey_callback_slow", duration_ms=elapsed_ms)

        return not decision.suppress

    def _decide(self, event: Any):
        self.total_events += 1
        key_event = self._translate(event)

        with self._lock:
            decision = self.machine.handle_key_event(key_event)
            commands = decision.commands

        for command in commands:
            self._enqueue(command)

        if self._logger and decision.note in ("hotkey_down", "hotkey_up", "repeat"):
            # Diagnostic only: scan codes and dispositions, never key contents.
            self._logger.diagnostic(
                "hotkey_raw_event",
                key=str(key_event.key),
                type=key_event.event_type.value,
                disposition=decision.disposition.value,
                note=decision.note,
                state=self.machine.state.value,
            )
        return decision

    def _translate(self, event: Any) -> KeyEvent:
        scan_code = getattr(event, "scan_code", None) or 0
        name = (getattr(event, "name", None) or "").lower()
        raw_type = getattr(event, "event_type", None)
        event_type = EventType.DOWN if raw_type == "down" else EventType.UP
        injected = self._self_injecting
        return KeyEvent(
            key=KeyId(scan_code=int(scan_code), name=name),
            event_type=event_type,
            timestamp=self._clock(),
            injected=injected,
        )

    def _enqueue(self, command: Command) -> None:
        try:
            self.commands.put_nowait(command)
        except queue.Full:
            # Fail open: something downstream has stopped consuming, so stop
            # holding the keyboard hostage rather than queueing further.
            self.overflow_count += 1
            if self._logger:
                self._logger.error("hotkey_command_queue_overflow", dropped=command.type.value)
            self.recover("command_queue_overflow")

    def _handle_hook_exception(self, exc: Exception) -> None:
        if self._logger:
            self._logger.error("hotkey_hook_exception", error=type(exc).__name__)
        self.recover("hook_exception")

    # -- recovery ---------------------------------------------------------

    def recover(self, reason: str) -> tuple[Command, ...]:
        """Reset to a known-safe state and release anything we swallowed.

        Called on hook exceptions, queue overflow and shutdown.  Reading the
        pending keys *before* the reset is what makes the release possible.
        """
        with self._lock:
            stranded = self.machine.pending_suppressed_keys()
            commands = self.machine.force_reset(reason)

        self.release_keys(stranded, reason=reason)
        for command in commands:
            try:
                self.commands.put_nowait(command)
            except queue.Full:
                pass
        return commands

    def release_keys(self, keys: tuple[KeyId, ...], *, reason: str) -> None:
        """Synthesise a key-up for every key whose key-down we swallowed."""
        if not keys:
            return
        self.begin_self_injection()
        try:
            for key in keys:
                vk = winkeys.vk_for_key(key.scan_code, key.name)
                if vk is None:
                    if self._logger:
                        self._logger.warn("hotkey_release_unmapped", key=str(key), reason=reason)
                    continue
                ok = winkeys.send_key_up(vk)
                if self._logger:
                    self._logger.info(
                        "hotkey_synthetic_release",
                        key=str(key),
                        reason=reason,
                        accepted=ok,
                    )
        finally:
            self.end_self_injection()

    def watchdog_recovery_candidates(
        self, max_age_sec: float = WATCHDOG_RECOVERY_WINDOW_SEC
    ) -> tuple[int, ...]:
        """Return VKs backed by a recent completed pair VoxPress suppressed."""

        with self._lock:
            keys = self.machine.recent_suppressed_releases(self._clock(), max_age_sec=max_age_sec)
        candidates = {
            vk for key in keys if (vk := winkeys.vk_for_key(key.scan_code, key.name)) is not None
        }
        return tuple(sorted(candidates))

    # -- worker feedback ---------------------------------------------------

    def acknowledge(
        self, event: WorkerEvent, generation: int, error_code: str | None = None
    ) -> None:
        with self._lock:
            self.machine.handle_worker_event(event, generation, error_code)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.machine.state.value,
                "generation": self.machine.generation,
                "pending_suppressed": [str(k) for k in self.machine.pending_suppressed_keys()],
                "stats": dict(self.machine.stats),
                "max_callback_latency_ms": round(self.max_latency_ms, 3),
                "events": self.total_events,
                "queue_overflows": self.overflow_count,
                "hook_exceptions": self.exception_count,
                "installed": self._installed_handler is not None,
            }
