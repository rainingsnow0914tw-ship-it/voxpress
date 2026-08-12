"""Owned audio stream with per-recording buffers.

Replaces the shared mutable dict in the VoxPress engine::

    state = {"recording": False, "transcribing": False, "stream": None, "audio": []}

    def start_rec():
        state["audio"] = []
        try:
            state["stream"] = sd.InputStream(...)   # assigned BEFORE start()
            state["stream"].start()
            state["recording"] = True
        except Exception as e:
            print(f"[rec] start fail: {e}")          # partial stream leaks

That design has three concrete defects:

* the stream was published to shared state *before* ``start()`` succeeded, so a
  failed start left a half-open stream reachable and never closed;
* one ``audio`` list was shared by every recording, so a late callback from a
  finished recording could append into the next one's buffer;
* failure was swallowed with a print — the hotkey layer never learned that
  recording had not begun, which is why a failed start used to leave the state
  machine believing it was recording.

Here a recording is an object with its own buffer and generation.  The stream
is built into a local, only published after a successful ``start()``, and any
partial failure closes everything it acquired before raising a typed error.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from voxpress.errors import ErrorCode, SttError, classify_audio_error


class _Stream(Protocol):  # pragma: no cover - structural typing only
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


@dataclass
class RecordingSession:
    """One recording.  Owns its buffer; nothing else may append to it."""

    generation: int
    sample_rate: int
    _chunks: list[Any] = field(default_factory=list)
    _detached: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, chunk: Any) -> bool:
        """Append audio.  Returns False once detached (a late callback)."""
        with self._lock:
            if self._detached:
                return False
            self._chunks.append(chunk)
            return True

    def detach(self) -> list[Any]:
        """Atomically take the buffer.  Later appends are refused, not lost
        into a buffer someone else is now transcribing."""
        with self._lock:
            self._detached = True
            chunks, self._chunks = self._chunks, []
            return chunks

    @property
    def detached(self) -> bool:
        with self._lock:
            return self._detached

    @property
    def chunk_count(self) -> int:
        with self._lock:
            return len(self._chunks)


@dataclass(frozen=True)
class StoppedRecording:
    """The immutable result of stopping a recording."""

    generation: int
    sample_rate: int
    chunks: list[Any]

    @property
    def empty(self) -> bool:
        return not self.chunks


def _default_stream_factory(
    *, samplerate: int, channels: int, dtype: str, callback: Callable
) -> _Stream:
    import sounddevice as sd  # imported late so tests never need PortAudio

    return sd.InputStream(samplerate=samplerate, channels=channels, dtype=dtype, callback=callback)


class AudioService:
    """Serialises stream ownership behind one lock with explicit states."""

    def __init__(
        self,
        sample_rate: int = 16000,
        *,
        stream_factory: Callable[..., _Stream] = _default_stream_factory,
        logger=None,
    ) -> None:
        self.sample_rate = sample_rate
        self._factory = stream_factory
        self._logger = logger
        self._lock = threading.Lock()
        self._stream: _Stream | None = None
        self._session: RecordingSession | None = None
        self.last_error_code: str | None = None
        self.dropped_late_chunks = 0

    @property
    def active(self) -> bool:
        with self._lock:
            return self._stream is not None

    @property
    def current_generation(self) -> int | None:
        with self._lock:
            return self._session.generation if self._session else None

    # -- start ------------------------------------------------------------

    def start(self, generation: int) -> RecordingSession:
        """Open and start a stream.  Raises :class:`SttError` on any failure."""
        with self._lock:
            if self._stream is not None:
                # Deterministic refusal — emphatically not a partial reset of
                # the audio state, which is how the old code lost recordings.
                raise SttError(ErrorCode.AUDIO_ALREADY_RECORDING, "a recording is active")

            session = RecordingSession(generation=generation, sample_rate=self.sample_rate)

            def on_audio(indata, frames, time_info, status) -> None:
                if status and self._logger:
                    self._logger.diagnostic("audio_callback_status", status=str(status))
                # Bound to *this* session by closure: a callback that fires late
                # cannot reach a newer recording's buffer.
                if not session.append(indata.copy()):
                    self.dropped_late_chunks += 1

            stream: _Stream | None = None
            try:
                stream = self._factory(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=on_audio,
                )
                stream.start()
            except BaseException as exc:
                # Close whatever we managed to acquire before propagating.
                if stream is not None:
                    _quiet_stop_close(stream)
                code = classify_audio_error(exc)
                self.last_error_code = code
                if self._logger:
                    self._logger.error(
                        "audio_start_failed",
                        error_code=code,
                        generation=generation,
                        error=type(exc).__name__,
                    )
                raise SttError(code, str(exc)) from exc

            self._stream = stream
            self._session = session

        if self._logger:
            self._logger.info("audio_started", generation=generation, sample_rate=self.sample_rate)
        return session

    # -- stop -------------------------------------------------------------

    def stop(self) -> "StoppedRecording | None":
        """Stop and detach.  Idempotent: a second call returns None.

        Returning the detached chunks rather than the live session is what
        makes "stopping atomically detaches the buffer" true at the
        API level — the caller cannot accidentally keep reading a buffer the
        audio callback might still be racing to fill.
        """
        with self._lock:
            stream, self._stream = self._stream, None
            session, self._session = self._session, None

        if stream is None:
            return None

        _quiet_stop_close(stream, logger=self._logger)

        if session is None:  # pragma: no cover - stream without session
            return None

        chunks = session.detach()
        if self._logger:
            self._logger.info("audio_stopped", generation=session.generation, chunks=len(chunks))
        return StoppedRecording(
            generation=session.generation,
            sample_rate=session.sample_rate,
            chunks=chunks,
        )

    def cancel(self) -> None:
        """Abandon any recording without transcribing it."""
        stopped = self.stop()
        if stopped is not None and self._logger:
            self._logger.info("audio_cancelled", generation=stopped.generation)

    def close(self) -> None:
        self.cancel()


def _quiet_stop_close(stream: _Stream, logger=None) -> None:
    """Stop then close, attempting close even when stop raises.

    Skipping close after a failed stop is how a device stays held open and the
    microphone indicator never goes away.
    """
    try:
        stream.stop()
    except Exception as exc:
        if logger:
            logger.warn("audio_stream_stop_failed", error=type(exc).__name__)
    try:
        stream.close()
    except Exception as exc:
        if logger:
            logger.warn("audio_stream_close_failed", error=type(exc).__name__)


def concatenate(chunks: list[Any]):
    """Join float32 chunks into one mono array.  Returns None when empty."""
    if not chunks:
        return None
    import numpy as np

    joined = np.concatenate(chunks, axis=0)
    if joined.size == 0:
        return None
    return joined[:, 0] if joined.ndim > 1 else joined
