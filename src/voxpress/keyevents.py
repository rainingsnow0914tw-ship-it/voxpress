"""Value types for raw keyboard events and what we decide to do with them.

These are deliberately plain data with no dependency on the ``keyboard``
library, so the entire hotkey decision layer can be tested without installing a
real global hook. The public non-live test boundary is documented in
`CONTRIBUTING.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventType(str, Enum):
    DOWN = "down"
    UP = "up"


class Disposition(str, Enum):
    """What the low-level hook returns to Windows for one event."""

    PASS = "pass"  # the OS sees the event exactly as if we were not here
    SUPPRESS = "suppress"  # we swallow it; the OS never sees it


@dataclass(frozen=True, slots=True)
class KeyId:
    """Identity of one *physical* key.

    **Equality and hashing use the scan code alone**; ``name`` is carried for
    logging and for mapping back to a virtual-key code, but is deliberately
    excluded from comparison.

    Why that matters: the ``keyboard`` library derives an event's name from a
    lookup keyed on ``(scan_code, vk, is_extended, modifiers)`` — modifier
    state is part of it.  If a name could differ between a key's down and its
    up (Ctrl released mid-hold, say), a name-sensitive identity would fail to
    pair the two halves, which is precisely the dangling-key-down failure this
    module exists to make impossible.  Scan codes do not vary that way, and
    they already separate left Windows (91) from right Windows (92), which is
    the distinction a single shared pairing boolean loses.
    """

    scan_code: int
    name: str = field(default="", compare=False)

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.name or '?'}#{self.scan_code}"


@dataclass(frozen=True, slots=True)
class KeyEvent:
    key: KeyId
    event_type: EventType
    timestamp: float  # monotonic seconds — never wall clock
    injected: bool = False  # synthesised by software (possibly by us)

    @property
    def is_down(self) -> bool:
        return self.event_type is EventType.DOWN

    @property
    def is_up(self) -> bool:
        return self.event_type is EventType.UP
