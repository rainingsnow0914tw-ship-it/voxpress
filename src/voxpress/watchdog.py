"""Post-cycle stuck-modifier watchdog.

The state machine makes a stranded key unreachable *by our own decisions*.
This is the second line of defence for events Windows never delivered, a hook
Windows silently detached, or another application's injected input.

Recovery is deliberately conservative.  It fires only when **all** hold:

* VoxPress recently completed a suppressing down/up pair for that exact key;
* Windows' async state says it *is* held;
* that disagreement survives two consecutive checks (so a key genuinely being
  pressed at that instant is not snatched away);
* the key is a Windows key — see :data:`voxpress.winkeys.AUTO_RELEASABLE`.

The asymmetry is intentional.  A false positive costs the user one dropped Win
keypress. A false negative can leave the keyboard unusable.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from voxpress import winkeys

DEFAULT_INTERVAL_SEC = 0.4
#: Consecutive disagreeing observations required before acting.
CONFIRMATIONS_REQUIRED = 2


class StuckModifierWatchdog:
    """Recovers only keys with recent, explicit VoxPress suppression provenance."""

    def __init__(
        self,
        hook,
        *,
        logger=None,
        interval: float = DEFAULT_INTERVAL_SEC,
        auto_release: bool = True,
        snapshot_fn: Callable[[], winkeys.ModifierSnapshot] = winkeys.async_modifier_snapshot,
        release_fn: Callable[[int], bool] = winkeys.send_key_up,
    ) -> None:
        self._hook = hook
        self._logger = logger
        self._interval = interval
        self._auto_release = auto_release
        self._snapshot_fn = snapshot_fn
        self._release_fn = release_fn

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._suspicion: dict[int, int] = {}

        self.detections = 0
        self.recoveries = 0
        self.last_detection_at: float | None = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="stt-stuck-modifier-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def _run(self) -> None:  # pragma: no cover - thread body
        while not self._stop.wait(self._interval):
            try:
                self.check_once()
            except Exception as exc:
                if self._logger:
                    self._logger.error("watchdog_check_failed", error=type(exc).__name__)

    # -- the check --------------------------------------------------------

    def check_once(self) -> tuple[int, ...]:
        """One observation.  Returns the VKs released this round (usually none)."""
        snapshot = self._hook.snapshot()
        candidates = set(self._hook.watchdog_recovery_candidates())
        if snapshot["pending_suppressed"] or not candidates:
            self._suspicion.clear()
            return ()

        physical = self._snapshot_fn()
        if not physical.any_down:
            self._suspicion.clear()
            return ()

        released: list[int] = []
        for vk in sorted(physical.down & candidates):
            count = self._suspicion.get(vk, 0) + 1
            self._suspicion[vk] = count
            if count < CONFIRMATIONS_REQUIRED:
                continue

            self.detections += 1
            self.last_detection_at = time.time()
            name = dict(winkeys.WATCHED_MODIFIERS).get(vk, hex(vk))
            if self._logger:
                self._logger.warn(
                    "stuck_modifier_detected",
                    key=name,
                    vk=hex(vk),
                    observations=count,
                    will_release=self._auto_release and vk in winkeys.AUTO_RELEASABLE,
                )

            if not (self._auto_release and vk in winkeys.AUTO_RELEASABLE):
                continue

            self._hook.begin_self_injection()
            try:
                accepted = self._release_fn(vk)
            finally:
                self._hook.end_self_injection()

            if accepted:
                self.recoveries += 1
                released.append(vk)
                self._suspicion.pop(vk, None)
            if self._logger:
                self._logger.info("stuck_modifier_release", key=name, accepted=accepted)

        # Forget keys that are no longer reported down.
        for vk in list(self._suspicion):
            if vk not in physical.down or vk not in candidates:
                self._suspicion.pop(vk, None)

        return tuple(released)

    def health(self) -> dict:
        return {
            "detections": self.detections,
            "recoveries": self.recoveries,
            "last_detection_at": self.last_detection_at,
            "auto_release": self._auto_release,
        }
