"""Deterministic hotkey state machine with paired physical key decisions.

Background — what actually broke
-------------------------------
A short multi-key press can leave Windows believing a key is still held when a
suppressing hook decides each endpoint independently. The previous hook
independently and tracked pairing with a single mutable boolean::

    s = {"press_time": None, "hold_active": False, "swallowed_down": False}

    if event_type == KEY_DOWN:
        for m in modifiers:
            if not keyboard.is_pressed(m):
                return True          # pass to the OS
        s["swallowed_down"] = True
        _do_press()                  # <-- opens a PortAudio stream, inline
        return False                 # suppress
    else:
        if s["swallowed_down"]:
            s["swallowed_down"] = False
            _do_release()            # <-- closes the stream, inline
            return False
        return True

That shape has two independent routes to a dangling key-down, and both are
fixed here:

1. **Disposition divergence.**  The DOWN and the UP of one physical key are
   decided by *different* predicates.  Any repeat/duplicate DOWN that arrives
   after Ctrl has been released takes the ``return True`` branch and reaches
   the OS, while ``swallowed_down`` is still ``True`` so the following UP is
   swallowed.  The OS is left holding a Win-down with no Win-up.  The single
   boolean is also shared by the left and right Windows keys.

2. **Hook timeout.**  ``_do_press()`` calls ``sd.InputStream(...).start()``
   inside the low-level hook callback. Opening a PortAudio stream can take long
   enough to exceed the hook timeout, especially on backend failures. When a
   WH_KEYBOARD_LL callback
   overruns ``LowLevelHooksTimeout`` Windows stops honouring its return value
   and delivers the event anyway.  The code believes it suppressed the DOWN,
   the OS received it, and the paired UP is then swallowed.  Same dangling
   key-down, different cause.

The invariant that replaces both
--------------------------------
**The disposition of a key's UP is always the disposition recorded for that
same physical key's DOWN.**  Never recomputed, never derived from modifier
state, never shared between keys.  A dangling down therefore cannot be
constructed, regardless of event order, repeats, duplicate endpoints, timing,
or worker failure.

Supporting rules:

* every decision here is a pure function of recorded state — no I/O, no audio,
  no clipboard, no ``GetAsyncKeyState`` (whose value is not yet updated at
  callback time anyway);
* modifiers are *never* suppressed, so a stuck Ctrl is out of reach by
  construction;
* time is monotonic and injected, so 300 ms boundary tests need no sleeps;
* every recording carries a generation, so a late worker result cannot stop a
  newer recording;
* failures fail **open**: on any doubt the event goes to Windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from voxpress.commands import Command, CommandType, WorkerEvent
from voxpress.keyevents import Disposition, KeyEvent, KeyId

#: the user's muscle memory, preserved: below this a press is a "tap" that latches
#: toggle recording; at or above it the press is push-to-talk.  Exactly-at is
#: defined as long-press so the comparison has one unambiguous form.
DEFAULT_HOLD_THRESHOLD_SEC = 0.30

#: A duplicate UP arriving within this window (contact bounce, or a second
#: keyboard endpoint echoing the same key) reuses the disposition already
#: applied, so the OS never sees a mismatched pair.
BOUNCE_WINDOW_SEC = 0.25


class State(str, Enum):
    IDLE = "idle"
    ARMED_RECORDING = "armed_recording"  # key held; short-vs-long undecided
    TOGGLE_RECORDING = "toggle_recording"  # tap latched; recording continues
    STOPPING = "stopping"  # STOP issued; awaiting the worker
    TRANSCRIBING = "transcribing"
    ERROR_RECOVERY = "error_recovery"


@dataclass(frozen=True, slots=True)
class HotkeyConfig:
    #: Scan codes of the main key (both Windows keys, for ``ctrl+windows``).
    main_scan_codes: frozenset[int]
    #: Scan codes of the required modifiers (both Ctrl keys).  Never suppressed.
    modifier_scan_codes: frozenset[int]
    hold_threshold_sec: float = DEFAULT_HOLD_THRESHOLD_SEC
    #: False for an ordinary main key such as ``z``, where swallowing the key
    #: would break typing and the OS has no special meaning to intercept.
    suppress_main_key: bool = True
    #: One alternatives-group per logical modifier. Every group is required;
    #: either scan code inside a group may satisfy left/right variants.
    modifier_scan_code_groups: tuple[frozenset[int], ...] = ()


@dataclass(frozen=True, slots=True)
class Decision:
    """What the hook returns to Windows, plus work to hand to the worker."""

    disposition: Disposition
    commands: tuple[Command, ...] = ()
    note: str = ""

    @property
    def suppress(self) -> bool:
        return self.disposition is Disposition.SUPPRESS


@dataclass
class _Cycle:
    """Bookkeeping for one physical press-and-release of the main key."""

    cycle_id: int
    key: KeyId
    pressed_at: float


class HotkeyStateMachine:
    """Single-consumer decision core.  Pure, synchronous, allocation-light.

    Thread model: every ``handle_*`` call happens on the hook thread except
    ``handle_worker_event``, which the worker calls.  Both are cheap and the
    owner serialises them with one lock (see :mod:`voxpress.hotkey_hook`);
    nothing here blocks, so that lock is never held for a measurable time.
    """

    def __init__(self, config: HotkeyConfig) -> None:
        self.config = config
        self.state = State.IDLE

        self._generation = 0
        self._cycle_counter = 0
        self._cycle: _Cycle | None = None

        # The pairing table.  Key present => we have seen its DOWN and not yet
        # its UP, and this is the disposition we applied.
        self._pending: dict[KeyId, Disposition] = {}
        # Short-lived memory so a bounced/duplicated UP matches its DOWN.
        self._recent: dict[KeyId, tuple[float, Disposition]] = {}
        # Modifier state derived from the raw event stream, not from a library
        # cache and not from GetAsyncKeyState.
        self._modifiers_down: set[KeyId] = set()

        self.last_error: str | None = None
        self.stats: dict[str, int] = {
            "cycles": 0,
            "starts": 0,
            "stops": 0,
            "repeats_absorbed": 0,
            "unpaired_ups": 0,
            "busy_presses": 0,
            "stale_worker_events": 0,
        }

    # -- introspection ---------------------------------------------------

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def recording(self) -> bool:
        return self.state in (State.ARMED_RECORDING, State.TOGGLE_RECORDING)

    @property
    def busy(self) -> bool:
        return self.state in (State.STOPPING, State.TRANSCRIBING)

    def pending_suppressed_keys(self) -> tuple[KeyId, ...]:
        """Keys whose DOWN we swallowed and whose UP has not arrived.

        Shutdown and error recovery must synthesise a release for each of these
        or Windows keeps them down — the exact harm this module exists to
        prevent.
        """
        return tuple(
            key for key, disposition in self._pending.items() if disposition is Disposition.SUPPRESS
        )

    def recent_suppressed_releases(self, now: float, *, max_age_sec: float) -> tuple[KeyId, ...]:
        """Completed suppressed pairs that may need bounded OS-state recovery."""

        return tuple(
            key
            for key, (at, disposition) in self._recent.items()
            if key not in self._pending
            and disposition is Disposition.SUPPRESS
            and 0.0 <= now - at <= max_age_sec
        )

    def modifiers_held(self) -> tuple[KeyId, ...]:
        return tuple(self._modifiers_down)

    # -- key events ------------------------------------------------------

    def handle_key_event(self, event: KeyEvent) -> Decision:
        scan = event.key.scan_code

        if scan in self.config.modifier_scan_codes:
            self._track_modifier(event)
            # Modifiers are always forwarded.  Suppressing Ctrl would put a
            # stuck-Ctrl failure mode back on the table for no benefit.
            return Decision(Disposition.PASS, note="modifier")

        if scan not in self.config.main_scan_codes:
            return Decision(Disposition.PASS, note="unrelated")

        if event.is_down:
            return self._handle_main_down(event)
        return self._handle_main_up(event)

    def _track_modifier(self, event: KeyEvent) -> None:
        if event.is_down:
            self._modifiers_down.add(event.key)
        else:
            self._modifiers_down.discard(event.key)

    def _all_modifiers_required_down(self) -> bool:
        groups = self.config.modifier_scan_code_groups
        if not groups and self.config.modifier_scan_codes:
            groups = (self.config.modifier_scan_codes,)
        if not groups:
            return True
        held = {key.scan_code for key in self._modifiers_down}
        return all(bool(held & group) for group in groups)

    def _handle_main_down(self, event: KeyEvent) -> Decision:
        prior = self._pending.get(event.key)
        if prior is not None:
            # Auto-repeat, or a duplicate from a second keyboard endpoint.
            # Reuse the original disposition and emit nothing: this single
            # branch removes both the repeated-START bug and the divergence
            # that produced the stuck LWin.
            self.stats["repeats_absorbed"] += 1
            return Decision(prior, note="repeat")

        if event.injected:
            # Our own Ctrl+V, or another tool's synthetic input.  Never let it
            # recurse into the feature, and never claim its pairing.
            return Decision(Disposition.PASS, note="injected")

        if not self._all_modifiers_required_down():
            # A bare Windows press: Windows keeps its Start menu.  Recorded so
            # that if Ctrl arrives *during* this hold, the repeat and the UP
            # still resolve to PASS and the OS gets a matched pair.
            self._remember(event.key, Disposition.PASS, event.timestamp)
            return Decision(Disposition.PASS, note="bare_main_key")

        disposition = Disposition.SUPPRESS if self.config.suppress_main_key else Disposition.PASS
        self._remember(event.key, disposition, event.timestamp)
        commands = self._on_press(event)
        return Decision(disposition, commands, note="hotkey_down")

    def _handle_main_up(self, event: KeyEvent) -> Decision:
        prior = self._pending.pop(event.key, None)
        if prior is None:
            recent = self._recent.get(event.key)
            if recent is not None and event.timestamp - recent[0] <= BOUNCE_WINDOW_SEC:
                # Bounce / duplicate endpoint: mirror what we already did.
                return Decision(recent[1], note="bounce_up")
            # We never saw this key's DOWN — the hook was installed while the
            # key was already held, or the OS dropped an event.  Fail open.
            self.stats["unpaired_ups"] += 1
            return Decision(Disposition.PASS, note="unpaired_up")

        self._recent[event.key] = (event.timestamp, prior)
        commands = self._on_release(event)
        return Decision(prior, commands, note="hotkey_up")

    def _remember(self, key: KeyId, disposition: Disposition, at: float) -> None:
        self._pending[key] = disposition
        self._recent[key] = (at, disposition)
        self._prune_recent(at)

    def _prune_recent(self, now: float) -> None:
        if len(self._recent) < 8:
            return
        stale = [
            key
            for key, (at, _) in self._recent.items()
            if now - at > BOUNCE_WINDOW_SEC and key not in self._pending
        ]
        for key in stale:
            self._recent.pop(key, None)

    # -- state transitions ------------------------------------------------

    def _on_press(self, event: KeyEvent) -> tuple[Command, ...]:
        if self.state is State.IDLE:
            self._generation += 1
            self._cycle_counter += 1
            self._cycle = _Cycle(
                cycle_id=self._cycle_counter,
                key=event.key,
                pressed_at=event.timestamp,
            )
            self.state = State.ARMED_RECORDING
            self.stats["cycles"] += 1
            self.stats["starts"] += 1
            return (
                Command(
                    CommandType.START_RECORDING,
                    generation=self._generation,
                    cycle_id=self._cycle_counter,
                    reason="press",
                ),
            )

        if self.state is State.ARMED_RECORDING:
            # A second main key pressed while the first is held (e.g. the other
            # Windows key).  One recording, one command.
            return ()

        if self.state is State.TOGGLE_RECORDING:
            # The deliberate second press that ends a tap-latched recording.
            self._cycle = _Cycle(
                cycle_id=self._cycle_counter,
                key=event.key,
                pressed_at=event.timestamp,
            )
            self.state = State.STOPPING
            self.stats["stops"] += 1
            return (
                Command(
                    CommandType.STOP_RECORDING,
                    generation=self._generation,
                    cycle_id=self._cycle_counter,
                    reason="toggle_second_press",
                ),
            )

        if self.state in (State.STOPPING, State.TRANSCRIBING):
            # Busy.  the user gets an audible receipt instead of silence, and the
            # key pairing is unaffected.
            self.stats["busy_presses"] += 1
            return (
                Command(
                    CommandType.BUSY_CUE,
                    generation=self._generation,
                    cycle_id=self._cycle_counter,
                    reason=f"busy:{self.state.value}",
                ),
            )

        return ()

    def _on_release(self, event: KeyEvent) -> tuple[Command, ...]:
        if self.state is not State.ARMED_RECORDING:
            # Release while toggle-latched, stopping, transcribing or idle.
            # Nothing to do — but the disposition was already decided by the
            # pairing table, which is what keeps the keyboard sane.
            return ()

        cycle = self._cycle
        if cycle is None or cycle.key != event.key:
            # A different main key released while ours is still held.
            return ()

        # Quantise to nanoseconds before comparing.  Subtracting two float
        # seconds loses precision: a real 300.000 ms hold measured from a
        # timestamp near 1e3 comes back as 0.29999999999995, which would
        # classify an exact-threshold press as *short* and contradict the
        # documented rule.  Integer nanoseconds make the boundary exact.
        elapsed_ns = round((event.timestamp - cycle.pressed_at) * 1e9)
        threshold_ns = round(self.config.hold_threshold_sec * 1e9)
        if elapsed_ns < threshold_ns:
            # Short press: latch toggle mode, keep recording, wait for the next
            # deliberate press.  This is the long-form dictation path.
            self.state = State.TOGGLE_RECORDING
            return ()

        # Long press: push-to-talk ends on release.
        self.state = State.STOPPING
        self.stats["stops"] += 1
        return (
            Command(
                CommandType.STOP_RECORDING,
                generation=self._generation,
                cycle_id=cycle.cycle_id,
                reason="hold_release",
            ),
        )

    # -- worker acknowledgements ------------------------------------------

    def handle_worker_event(
        self, event: WorkerEvent, generation: int, error_code: str | None = None
    ) -> None:
        if generation != self._generation:
            # A result from a recording the user has already superseded.  Dropping
            # it is what stops a stale STOP from killing a fresh recording.
            self.stats["stale_worker_events"] += 1
            return

        if event is WorkerEvent.RECORDING_STARTED:
            return  # confirmation only; ARMED/TOGGLE already reflect it

        if event is WorkerEvent.RECORDING_START_FAILED:
            # The microphone never opened.  Return to idle so the pending
            # release does not try to stop a recording that never existed.
            self.last_error = error_code
            self._cycle = None
            self.state = State.IDLE
            return

        if event is WorkerEvent.RECORDING_STOPPED:
            if self.state is State.STOPPING:
                self.state = State.TRANSCRIBING
            return

        if event is WorkerEvent.TRANSCRIPTION_FINISHED:
            self._cycle = None
            self.state = State.IDLE
            return

        if event is WorkerEvent.FAILED:
            self.last_error = error_code
            self._cycle = None
            self.state = State.IDLE
            return

    # -- recovery ---------------------------------------------------------

    def force_reset(self, reason: str) -> tuple[Command, ...]:
        """Fail-open reset.  Returns the work needed to leave nothing running.

        The caller is responsible for synthesising a key-up for every key in
        :meth:`pending_suppressed_keys` *before* clearing, which is why that is
        read here rather than by the caller afterwards.
        """
        self.last_error = reason
        commands: tuple[Command, ...] = ()
        if self.recording or self.state is State.STOPPING:
            commands = (
                Command(
                    CommandType.CANCEL_RECORDING,
                    generation=self._generation,
                    cycle_id=self._cycle_counter,
                    reason=reason,
                ),
            )
        self._pending.clear()
        self._recent.clear()
        self._modifiers_down.clear()
        self._cycle = None
        self.state = State.IDLE
        return commands
