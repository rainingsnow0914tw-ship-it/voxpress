"""Thin Windows keyboard primitives: read async state, synthesise a release.

Isolated behind a tiny surface so the rest of the app can be unit-tested with a
fake. Two rules are enforced by placement rather than comment:

* ``GetAsyncKeyState`` is used only by the watchdog thread, never inside the
  low-level hook callback, because the asynchronous state has not been updated
  at callback time;
* synthesising a release is a *recovery* action, deliberately narrow.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C

#: The keys that can take the keyboard away from the user if left stuck down.
WATCHED_MODIFIERS: tuple[tuple[int, str], ...] = (
    (VK_LWIN, "left windows"),
    (VK_RWIN, "right windows"),
    (VK_LCONTROL, "left ctrl"),
    (VK_RCONTROL, "right ctrl"),
    (VK_LMENU, "left alt"),
    (VK_RMENU, "right alt"),
    (VK_LSHIFT, "left shift"),
    (VK_RSHIFT, "right shift"),
)

#: Only these are ever released automatically.  A stuck Windows key makes the
#: whole machine unusable; a stuck Shift is merely annoying and much more
#: likely to be a key the user is genuinely holding.
AUTO_RELEASABLE: frozenset[int] = frozenset({VK_LWIN, VK_RWIN})

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1

#: Windows keys are extended (0xE0-prefixed) scan codes.
_EXTENDED_VKS = frozenset({VK_LWIN, VK_RWIN, VK_RCONTROL, VK_RMENU})


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class _MOUSEINPUT(ctypes.Structure):
    """Largest native ``INPUT`` union member.

    Keeping the mouse member is not cosmetic: on 64-bit Windows it makes the
    union 32 bytes and the enclosing ``INPUT`` structure 40 bytes, which is the
    size ``SendInput`` validates even when the payload itself is a keyboard
    event.
    """

    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


@dataclass(frozen=True)
class ModifierSnapshot:
    """What Windows currently believes about the watched modifiers."""

    down: frozenset[int]

    def is_down(self, vk: int) -> bool:
        return vk in self.down

    @property
    def any_down(self) -> bool:
        return bool(self.down)

    def names(self) -> tuple[str, ...]:
        lookup = dict(WATCHED_MODIFIERS)
        return tuple(lookup.get(vk, hex(vk)) for vk in sorted(self.down))


def async_modifier_snapshot() -> ModifierSnapshot:
    """Read the physical/async modifier state.  Never call from the hook."""
    if not IS_WINDOWS:
        return ModifierSnapshot(frozenset())
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    down = {vk for vk, _ in WATCHED_MODIFIERS if user32.GetAsyncKeyState(vk) & 0x8000}
    return ModifierSnapshot(frozenset(down))


def send_key_up(vk: int) -> bool:
    """Synthesise a key-up for *vk*.  Returns True if Windows accepted it.

    Used in exactly two places: shutdown/error recovery for a key whose
    suppressed key-down we own, and the watchdog's bounded recovery for a
    Windows key that Windows still believes is held while our own model says it
    is not.
    """
    if not IS_WINDOWS:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    flags = _KEYEVENTF_KEYUP
    if vk in _EXTENDED_VKS:
        flags |= _KEYEVENTF_EXTENDEDKEY

    payload = _INPUT(
        type=_INPUT_KEYBOARD,
        union=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)),
    )
    sent = user32.SendInput(1, ctypes.byref(payload), ctypes.sizeof(_INPUT))
    return sent == 1


#: Map from the `keyboard` library's canonical names to virtual key codes, so a
#: KeyId recorded by the state machine can be released if it is ever stranded.
NAME_TO_VK: dict[str, int] = {
    "left windows": VK_LWIN,
    "right windows": VK_RWIN,
    "windows": VK_LWIN,
    "left ctrl": VK_LCONTROL,
    "right ctrl": VK_RCONTROL,
    "ctrl": VK_LCONTROL,
    "left alt": VK_LMENU,
    "right alt": VK_RMENU,
    "alt": VK_LMENU,
    "left shift": VK_LSHIFT,
    "right shift": VK_RSHIFT,
    "shift": VK_LSHIFT,
}


def vk_for_name(name: str) -> int | None:
    return NAME_TO_VK.get((name or "").strip().lower())


def vk_for_key(scan_code: int, name: str) -> int | None:
    """Resolve a physical key without losing left/right Windows identity.

    The ``keyboard`` package can report both Windows keys with the generic
    name ``windows``. Their scan codes remain distinct, so use those first.
    Ordinary single-character hotkeys are resolved directly; named modifiers
    retain the explicit side from :data:`NAME_TO_VK`.
    """

    normalized = (name or "").strip().lower()
    if scan_code == VK_LWIN and normalized in {"windows", "left windows", "right windows"}:
        return VK_LWIN
    if scan_code == VK_RWIN and normalized in {"windows", "left windows", "right windows"}:
        return VK_RWIN

    named = vk_for_name(normalized)
    if named is not None:
        return named
    if len(normalized) == 1 and normalized.isascii() and normalized.isalnum():
        return ord(normalized.upper())

    if IS_WINDOWS and scan_code > 0:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        mapped = int(user32.MapVirtualKeyW(scan_code, 3))  # MAPVK_VSC_TO_VK_EX
        return mapped or None
    return None
