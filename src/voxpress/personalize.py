"""User-scoped vocabulary, corrections and bounded rolling context.

Prompt construction is bounded to 12 terms and 200 characters, with a
three-transcription context window. The store adds:

* a *last known good* snapshot, so saving a broken ``corrections.json`` while
  the assistant is running degrades to the previous content instead of
  silently reverting to no corrections at all;
* hot reload driven by file mtime, so edits apply without a restart;
* pure functions, so the behaviour is testable without a microphone.

These files hold the user's personal word list.  They are read here and never
logged, never committed, and never copied into a diagnostic bundle.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from voxpress.atomicio import read_json_checked
from voxpress.errors import ErrorCode

MAX_VOCAB_TERMS = 12
MAX_VOCAB_PROMPT_CHARS = 100
MAX_FULL_PROMPT_CHARS = 200
MAX_CONTEXT_CHARS = 80
CONTEXT_WINDOW = 3


def build_vocab_prompt(
    vocab: dict,
    max_terms: int = MAX_VOCAB_TERMS,
    max_chars: int = MAX_VOCAB_PROMPT_CHARS,
) -> str:
    """Compose the Whisper initial prompt from the term list."""
    hint = vocab.get("style_hint", "")
    if not isinstance(hint, str):
        hint = ""
    terms = vocab.get("terms", [])
    if not isinstance(terms, list):
        terms = []
    usable = [t for t in terms if isinstance(t, str) and t]
    if not usable:
        return hint[:max_chars]
    picked = usable[:max_terms]
    return f"{hint}{'、'.join(picked)}。"[:max_chars]


def build_context(recent: list[str], max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Tail of what was said recently, to steer Whisper toward continuity."""
    if not recent:
        return ""
    return " ".join(recent)[-max_chars:]


def compose_prompt(vocab_prompt: str, context: str) -> str:
    if context:
        return f"{vocab_prompt} 最近講過:{context}"[:MAX_FULL_PROMPT_CHARS]
    return vocab_prompt


def apply_corrections(text: str, corrections: dict) -> str:
    """Replace known mis-hearings.  Keys starting with ``_`` are metadata."""
    if not text or not corrections:
        return text
    for wrong, right in corrections.items():
        if not isinstance(wrong, str) or not isinstance(right, str):
            continue
        if wrong.startswith("_"):
            continue
        if wrong and wrong in text:
            text = text.replace(wrong, right)
    return text


_REPEAT_PATTERNS = (
    (re.compile(r"。{2,}"), "。"),
    (re.compile(r"，{2,}"), "，"),
    (re.compile(r"\.{3,}"), "..."),
    (re.compile(r"！{2,}"), "！"),
    (re.compile(r"？{2,}"), "？"),
)


def collapse_punctuation(text: str) -> str:
    """Whisper over-segments on pauses; collapse the resulting runs."""
    for pattern, replacement in _REPEAT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


@dataclass
class _CachedFile:
    path: Path
    default: dict
    value: dict
    mtime: float | None = None
    error_code: str | None = None
    loaded_once: bool = False


class PersonalizationStore:
    """Hot-reloading vocab/corrections with last-known-good fallback."""

    def __init__(
        self,
        vocab_path: Path,
        corrections_path: Path,
        *,
        logger=None,
        context_window: int = CONTEXT_WINDOW,
    ) -> None:
        self._logger = logger
        self._lock = threading.Lock()
        self._vocab = _CachedFile(vocab_path, {}, {})
        self._corrections = _CachedFile(corrections_path, {}, {})
        self._recent: deque[str] = deque(maxlen=context_window)

    # -- loading ----------------------------------------------------------

    def _refresh(self, cached: _CachedFile, kind: str) -> dict:
        try:
            mtime = cached.path.stat().st_mtime
        except OSError:
            if cached.loaded_once:
                return cached.value  # file vanished: keep what we had
            cached.loaded_once = True
            return cached.default

        if cached.loaded_once and cached.mtime == mtime:
            return cached.value

        ok, payload, error = read_json_checked(cached.path, default=None)
        cached.mtime = mtime
        cached.loaded_once = True

        if ok and isinstance(payload, dict):
            if cached.error_code is not None and self._logger:
                self._logger.info(f"{kind}_recovered", path=cached.path.name)
            cached.error_code = None
            cached.value = payload
            return payload

        code = ErrorCode.DICT_INVALID if error == "corrupt" else None
        if code is not None and cached.error_code != code and self._logger:
            # Loud, once per transition — a broken dictionary must not scroll
            # past silently, and must not crash the assistant either.
            self._logger.error(
                f"{kind}_invalid",
                path=cached.path.name,
                error_code=code,
                using_last_good=bool(cached.value),
            )
        cached.error_code = code
        return cached.value if cached.value else cached.default

    def vocab(self) -> dict:
        with self._lock:
            return self._refresh(self._vocab, "vocab")

    def corrections(self) -> dict:
        with self._lock:
            return self._refresh(self._corrections, "corrections")

    # -- use --------------------------------------------------------------

    def prompt_for_next(self) -> str:
        with self._lock:
            recent = list(self._recent)
        return compose_prompt(build_vocab_prompt(self.vocab()), build_context(recent))

    def postprocess(self, text: str) -> str:
        """Corrections + punctuation collapse, then remember for context."""
        cleaned = collapse_punctuation(apply_corrections(text, self.corrections()))
        if cleaned and len(cleaned) > 1:
            with self._lock:
                self._recent.append(cleaned)
        return cleaned

    def recent(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._recent)

    def health(self) -> dict:
        # Load first.  Reading the lazy cache directly reports 0 terms until
        # the first transcription, which reads as "the user's dictionary is gone" --
        # exactly the wrong signal for the one file that cannot be regenerated.
        vocab = self.vocab()
        corrections = self.corrections()
        with self._lock:
            terms = vocab.get("terms", []) if isinstance(vocab, dict) else []
            return {
                "vocab_error": self._vocab.error_code,
                "corrections_error": self._corrections.error_code,
                "vocab_configured": self._vocab.path.exists(),
                "corrections_configured": self._corrections.path.exists(),
                "vocab_terms": len(terms or []),
                "correction_entries": sum(1 for key in corrections if not str(key).startswith("_")),
            }
