"""Clipboard and focused-window delivery with a no-submit contract.

Text is **pasted, never submitted**. Nothing in this module presses Enter.

Guards added:

* **Wait for the hotkey modifiers to be released first.**  This is the one that
  actually bit: sending ``Ctrl+V`` while the user still holds the Windows key
  produces ``Win+V`` — the clipboard-history flyout — instead of a paste.  The
  old code slept a flat 200 ms and fired regardless.
* **Refuse known sensitive targets.** Credential dialogs, the lock screen and
  UAC prompts do not receive a transcription.
* **Distinct outcomes.**  ``pasted`` / ``clipboard_only`` / ``failed`` are
  logged separately, so "where did my dictation go" has an answer.

Focus-change policy: if focus moved to another *ordinary* window we
still paste, because transcription latency means a brief focus flicker is
common and refusing would break the user's flow for a mostly-benign case.  Only
sensitive surfaces are a hard block.  Strict mode is available for anyone who
wants the stricter reading.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from voxpress.errors import ErrorCode

DEFAULT_PASTE_DELAY_MS = 200
DEFAULT_MODIFIER_TIMEOUT_MS = 2000
_MODIFIER_POLL_SEC = 0.02


class PasteOutcome(str, Enum):
    PASTED = "pasted"
    CLIPBOARD_ONLY = "clipboard_only"
    FAILED = "failed"


@dataclass(frozen=True)
class PasteResult:
    outcome: PasteOutcome
    error_code: str | None = None
    waited_ms: float = 0.0

    @property
    def text_is_recoverable(self) -> bool:
        """Did the text at least reach the clipboard?"""
        return self.outcome in (PasteOutcome.PASTED, PasteOutcome.CLIPBOARD_ONLY)


class Clipboard(Protocol):  # pragma: no cover - structural
    def copy(self, text: str) -> None: ...


class SystemClipboard:
    def copy(self, text: str) -> None:
        import pyperclip

        pyperclip.copy(text)


class KeySender(Protocol):  # pragma: no cover - structural
    def send(self, combination: str) -> None: ...


class SystemKeySender:
    def send(self, combination: str) -> None:
        import keyboard

        keyboard.send(combination)


class TextWriter(Protocol):  # pragma: no cover - structural
    def write(
        self,
        text: str,
        *,
        delay: float = 0.01,
        cancelled: Callable[[], bool] | None = None,
    ) -> None: ...


class SystemTextWriter:
    def write(
        self,
        text: str,
        *,
        delay: float = 0.01,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        import keyboard

        for character in text:
            if cancelled is not None and cancelled():
                raise InterruptedError("typing cancelled")
            keyboard.write(character, delay=delay)


#: Window classes that must never receive dictated text.
SENSITIVE_WINDOW_CLASSES = frozenset(
    {
        "credential dialog xaml host",
        "#32770",  # generic dialog; combined with a title check below
        "consentui",
        "creddialogxamlhost",
        "lockscreen",
        "windows.ui.core.corewindow",
    }
)
SENSITIVE_TITLE_MARKERS = (
    "user account control",
    "使用者帳戶控制",
    "sign in",
    "登入",
    "password",
    "密碼",
    "credential",
)


@dataclass(frozen=True)
class WindowInfo:
    handle: int | None = None
    title: str = ""
    window_class: str = ""

    @property
    def is_sensitive(self) -> bool:
        cls = (self.window_class or "").strip().lower()
        title = (self.title or "").strip().lower()
        if cls in SENSITIVE_WINDOW_CLASSES and cls != "#32770":
            return True
        if any(marker in title for marker in SENSITIVE_TITLE_MARKERS):
            return True
        return False


def foreground_window() -> WindowInfo:
    """Best-effort description of the focused window.  Never raises."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.argtypes = ()
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user32.GetClassNameW.restype = ctypes.c_int
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return WindowInfo()

        title_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buf, 256)
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        return WindowInfo(handle=int(hwnd), title=title_buf.value, window_class=class_buf.value)
    except Exception:
        return WindowInfo()


class PasteAdapter:
    def __init__(
        self,
        *,
        clipboard: Clipboard | None = None,
        key_sender: KeySender | None = None,
        text_writer: TextWriter | None = None,
        modifiers_held: Callable[[], bool] | None = None,
        window_probe: Callable[[], WindowInfo] = foreground_window,
        paste_delay_ms: int = DEFAULT_PASTE_DELAY_MS,
        modifier_timeout_ms: int = DEFAULT_MODIFIER_TIMEOUT_MS,
        strict_focus: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        logger=None,
        on_before_send=None,
        on_after_send=None,
    ) -> None:
        self._clipboard = clipboard or SystemClipboard()
        self._keys = key_sender or SystemKeySender()
        self._writer = text_writer or SystemTextWriter()
        self._modifiers_held = modifiers_held or (lambda: False)
        self._window_probe = window_probe
        self._paste_delay_ms = paste_delay_ms
        self._modifier_timeout_ms = modifier_timeout_ms
        self._strict_focus = strict_focus
        self._sleep = sleep
        self._clock = clock
        self._logger = logger
        self._on_before_send = on_before_send
        self._on_after_send = on_after_send

    # -- public -----------------------------------------------------------

    def deliver(
        self,
        text: str,
        *,
        method: str = "ctrl_v",
        target: WindowInfo | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PasteResult:
        """Put *text* where the user is typing.  Never submits it."""
        if not text:
            return PasteResult(PasteOutcome.FAILED, ErrorCode.STT_EMPTY_RESULT)
        if cancelled is not None and cancelled():
            # Shutdown won the race before VoxPress touched any user state.
            self._log(PasteOutcome.FAILED, None, 0.0)
            return PasteResult(PasteOutcome.FAILED)

        try:
            self._clipboard.copy(text)
        except Exception as exc:
            if self._logger:
                self._logger.error("paste_clipboard_failed", error=type(exc).__name__)
            return PasteResult(PasteOutcome.FAILED, ErrorCode.PASTE_CLIPBOARD_FAILED)

        if method == "clipboard_only":
            self._log(PasteOutcome.CLIPBOARD_ONLY, None, 0.0)
            return PasteResult(PasteOutcome.CLIPBOARD_ONLY)
        if method not in ("typing", "ctrl_v"):
            self._log(PasteOutcome.CLIPBOARD_ONLY, ErrorCode.CONFIG_INVALID, 0.0)
            return PasteResult(PasteOutcome.CLIPBOARD_ONLY, ErrorCode.CONFIG_INVALID)

        waited_ms, released = self._wait_for_modifier_release()
        if not released:
            # Sending Ctrl+V now would be Win+V (clipboard history) or worse.
            self._log(PasteOutcome.CLIPBOARD_ONLY, ErrorCode.PASTE_MODIFIERS_HELD, waited_ms)
            return PasteResult(
                PasteOutcome.CLIPBOARD_ONLY, ErrorCode.PASTE_MODIFIERS_HELD, waited_ms
            )

        self._sleep(self._paste_delay_ms / 1000.0)

        blocked = self._prepare_injection(
            text,
            target=target,
            waited_ms=waited_ms,
            cancelled=cancelled,
        )
        if blocked is not None:
            return blocked

        injection_active = False
        try:
            if self._on_before_send:
                self._on_before_send()
                injection_active = True
            if method == "typing":
                # keyboard.write can map newline characters to Enter on some
                # backends. Flatten them so typing mode keeps the no-submit
                # contract; the complete original remains in the clipboard.
                safe_text = " ".join(text.replace("\r", "\n").splitlines())
                self._writer.write(safe_text, delay=0.01, cancelled=cancelled)
            elif method == "ctrl_v":
                # "ctrl+v" and nothing else. There is no Enter path here.
                self._keys.send("ctrl+v")
        except Exception as exc:
            if injection_active and self._on_after_send:
                self._on_after_send()
                injection_active = False
            if self._logger:
                self._logger.error("paste_send_failed", error=type(exc).__name__)
            # A typing backend may have emitted an unknown prefix before
            # failing. Automatically pasting the complete transcript here
            # could duplicate or corrupt the destination, so leave the full
            # text in the clipboard for explicit recovery instead.
            return PasteResult(PasteOutcome.CLIPBOARD_ONLY, ErrorCode.PASTE_SEND_FAILED, waited_ms)
        finally:
            if injection_active and self._on_after_send:
                self._on_after_send()

        self._log(PasteOutcome.PASTED, None, waited_ms)
        return PasteResult(PasteOutcome.PASTED, None, waited_ms)

    # -- internals ---------------------------------------------------------

    def _wait_for_modifier_release(self) -> tuple[float, bool]:
        started = self._clock()
        deadline = started + self._modifier_timeout_ms / 1000.0
        while self._modifiers_held():
            if self._clock() >= deadline:
                return (self._clock() - started) * 1000.0, False
            self._sleep(_MODIFIER_POLL_SEC)
        return (self._clock() - started) * 1000.0, True

    def _safe_probe(self) -> WindowInfo:
        try:
            return self._window_probe()
        except Exception:
            return WindowInfo()

    def _prepare_injection(
        self,
        text: str,
        *,
        target: WindowInfo | None,
        waited_ms: float,
        cancelled: Callable[[], bool] | None,
    ) -> PasteResult | None:
        """Revalidate and restore clipboard immediately before input injection."""

        if cancelled is not None and cancelled():
            self._log(PasteOutcome.CLIPBOARD_ONLY, None, waited_ms)
            return PasteResult(PasteOutcome.CLIPBOARD_ONLY, None, waited_ms)
        if self._modifiers_held():
            self._log(PasteOutcome.CLIPBOARD_ONLY, ErrorCode.PASTE_MODIFIERS_HELD, waited_ms)
            return PasteResult(
                PasteOutcome.CLIPBOARD_ONLY,
                ErrorCode.PASTE_MODIFIERS_HELD,
                waited_ms,
            )

        current = self._safe_probe()
        if current.is_sensitive:
            self._log(PasteOutcome.CLIPBOARD_ONLY, ErrorCode.PASTE_SENSITIVE_TARGET, waited_ms)
            return PasteResult(
                PasteOutcome.CLIPBOARD_ONLY,
                ErrorCode.PASTE_SENSITIVE_TARGET,
                waited_ms,
            )

        focus_changed = (
            target is not None
            and target.handle is not None
            and current.handle is not None
            and target.handle != current.handle
        )
        if focus_changed and self._strict_focus:
            self._log(PasteOutcome.CLIPBOARD_ONLY, ErrorCode.PASTE_FOCUS_CHANGED, waited_ms)
            return PasteResult(
                PasteOutcome.CLIPBOARD_ONLY,
                ErrorCode.PASTE_FOCUS_CHANGED,
                waited_ms,
            )
        if focus_changed and self._logger:
            self._logger.warn("paste_focus_changed_but_allowed")

        try:
            self._clipboard.copy(text)
        except Exception as exc:
            if self._logger:
                self._logger.error("paste_clipboard_refresh_failed", error=type(exc).__name__)
            return PasteResult(PasteOutcome.FAILED, ErrorCode.PASTE_CLIPBOARD_FAILED, waited_ms)
        return None

    def _log(self, outcome: PasteOutcome, code: str | None, waited_ms: float) -> None:
        if not self._logger:
            return
        # Outcome and timing only — never the text itself.
        self._logger.info(
            "paste_result",
            outcome=outcome.value,
            error_code=code,
            modifier_wait_ms=round(waited_ms, 1),
        )
