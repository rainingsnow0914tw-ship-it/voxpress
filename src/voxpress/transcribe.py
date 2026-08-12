"""Whisper transcription.

This module evolves the public VoxPress v0.1 transcription API. It preserves
the CUDA ``bin/`` and legacy ``lib/`` discovery needed across NVIDIA package
layouts, while adding typed failures, reload locking, and per-call prompts.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from voxpress.config import safe_model_label
from voxpress.errors import SttError, classify_transcription_error

TEMP_AUDIO_PREFIX = "voxpress-audio-"
DEFAULT_STALE_AUDIO_SECONDS = 24 * 60 * 60
_DLL_DIRECTORY_LOCK = threading.Lock()
_DLL_DIRECTORY_HANDLES: dict[str, object] = {}


def cleanup_stale_audio_files(
    temp_dir: Path | None = None,
    *,
    older_than_seconds: float = DEFAULT_STALE_AUDIO_SECONDS,
    now: float | None = None,
) -> int:
    """Remove only old VoxPress-owned WAV remnants from one temp directory."""

    root = Path(tempfile.gettempdir()) if temp_dir is None else Path(temp_dir)
    current = time.time() if now is None else now
    removed = 0
    try:
        candidates = tuple(root.glob(f"{TEMP_AUDIO_PREFIX}*.wav"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if current - path.stat().st_mtime < older_than_seconds:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def ensure_cuda_libs_on_path(logger=None) -> list[str]:
    """Add the nvidia wheel DLL directories to the Windows search path.

    Two layouts exist in the wild and both must work:
    ``nvidia/{cublas,cudnn}/lib`` (older wheels) and ``nvidia/{cublas,cudnn}/bin``
    (newer NVIDIA wheel layouts).
    """
    if sys.platform != "win32":
        return []

    import importlib.util

    added: list[str] = []
    for package in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(package)
        except Exception:
            continue
        if not spec or not spec.submodule_search_locations:
            continue
        for base in spec.submodule_search_locations:
            for subdir in ("bin", "lib"):
                target = Path(base) / subdir
                if not target.is_dir():
                    continue
                canonical = str(target.resolve()).casefold()
                try:
                    with _DLL_DIRECTORY_LOCK:
                        if canonical in _DLL_DIRECTORY_HANDLES:
                            continue
                        handle = os.add_dll_directory(str(target))
                        _DLL_DIRECTORY_HANDLES[canonical] = handle
                        current_path = os.environ.get("PATH", "")
                        entries = {
                            str(Path(item).resolve()).casefold()
                            for item in current_path.split(os.pathsep)
                            if item
                        }
                        if canonical not in entries:
                            os.environ["PATH"] = str(target) + os.pathsep + current_path
                        added.append(str(target))
                except OSError:
                    pass
    if added and logger:
        logger.info("cuda_dll_paths_added", count=len(added))
    return added


def make_wav_bytes(audio_float32, sample_rate: int = 16000) -> bytes:
    """float32 mono in [-1, 1] -> 16-bit PCM WAV bytes."""
    import numpy as np

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        clipped = np.clip(audio_float32 * 32767, -32768, 32767).astype(np.int16)
        handle.writeframes(clipped.tobytes())
    return buffer.getvalue()


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None
    duration: float
    elapsed_sec: float

    @property
    def realtime_factor(self) -> float:
        return self.elapsed_sec / max(self.duration, 0.01)


class WhisperEngine:
    """Lazy-loading faster-whisper wrapper, safe under concurrent access."""

    def __init__(
        self,
        *,
        model_size: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str | None = "zh",
        logger=None,
        model_factory=None,
        temp_dir: Path | None = None,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = None if not language or language == "auto" else language
        self._logger = logger
        self._model_factory = model_factory
        self._temp_dir = Path(temp_dir) if temp_dir is not None else None
        self._model = None
        # One lifecycle lock prevents load/reload/inference lock-order inversions.
        self._model_lock = threading.RLock()
        self.last_error_code: str | None = None

        if device == "cuda":
            ensure_cuda_libs_on_path(logger)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    # -- model lifecycle --------------------------------------------------

    def _build_model(self):
        if self._model_factory is not None:
            return self._model_factory(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        from faster_whisper import WhisperModel

        return WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)

    def load(self) -> None:
        with self._model_lock:
            if self._model is not None:
                return
            started = time.perf_counter()
            try:
                model = self._build_model()
            except BaseException as exc:
                code = classify_transcription_error(exc)
                self.last_error_code = code
                if self._logger:
                    self._logger.error(
                        "whisper_load_failed",
                        error_code=code,
                        model=safe_model_label(self.model_size),
                        device=self.device,
                        error=type(exc).__name__,
                    )
                raise SttError(code, str(exc)) from exc
            self._model = model
            if self._logger:
                self._logger.info(
                    "whisper_loaded",
                    model=safe_model_label(self.model_size),
                    device=self.device,
                    compute_type=self.compute_type,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )

    def request_reload(self) -> None:
        """Drop the model so the next transcription reloads it.

        Takes the use lock, so this can never tear down a model that a
        transcription is currently running on.
        """
        with self._model_lock:
            self._model = None
        if self._logger:
            self._logger.info("whisper_reload_requested")

    def warmup(self, audio_path: Path | None = None) -> bool:
        """Run one throwaway inference so the first real press is not slow.

        Uses a real short Chinese clip when available: a silent buffer warms
        CUDA kernels but not the decode path, which is what actually makes the
        first press after boot feel slow.
        """
        try:
            self.load()
            if audio_path is not None and audio_path.exists():
                with self._model_lock:
                    if self._model is None:
                        self.load()
                    segments, _ = self._model.transcribe(
                        str(audio_path),
                        language=self.language,
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                        task="transcribe",
                    )
                    list(segments)  # the generator must be drained to run
                if self._logger:
                    self._logger.info("whisper_warmup", kind="real_speech")
                return True

            import numpy as np

            silence = np.zeros(int(0.5 * 16000), dtype=np.float32)
            self.transcribe(make_wav_bytes(silence), initial_prompt=None)
            if self._logger:
                self._logger.info("whisper_warmup", kind="silence_fallback")
            return True
        except Exception as exc:
            # Never fatal: a cold first press is an inconvenience, a failed
            # start is not.
            if self._logger:
                self._logger.warn("whisper_warmup_failed", error=type(exc).__name__)
            return False

    # -- transcription -----------------------------------------------------

    def transcribe(self, wav_bytes: bytes, *, initial_prompt: str | None = None) -> Transcription:
        if self._model is None:
            self.load()

        started = time.perf_counter()
        handle = tempfile.NamedTemporaryFile(
            prefix=TEMP_AUDIO_PREFIX,
            suffix=".wav",
            dir=self._temp_dir,
            delete=False,
        )
        try:
            handle.write(wav_bytes)
            handle.close()
            path = Path(handle.name)

            with self._model_lock:
                if self._model is None:
                    self.load()
                segments, info = self._model.transcribe(
                    str(path),
                    language=self.language,
                    task="transcribe",
                    initial_prompt=initial_prompt or None,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                )
                text = "".join(segment.text for segment in segments).strip()

            return Transcription(
                text=text,
                language=getattr(info, "language", None),
                duration=float(getattr(info, "duration", 0.0) or 0.0),
                elapsed_sec=time.perf_counter() - started,
            )
        except SttError:
            raise
        except BaseException as exc:
            code = classify_transcription_error(exc)
            self.last_error_code = code
            if self._logger:
                self._logger.error("whisper_failed", error_code=code, error=type(exc).__name__)
            raise SttError(code, str(exc)) from exc
        finally:
            # The temp file holds the user's voice. Close first so unlink works
            # on Windows, then remove it on every normal or exception path.
            try:
                handle.close()
            except Exception:
                pass
            try:
                Path(handle.name).unlink(missing_ok=True)
            except Exception:
                pass

    def health(self) -> dict:
        return {
            "loaded": self.loaded,
            "model": safe_model_label(self.model_size),
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language,
            "last_error_code": self.last_error_code,
        }


def cuda_available() -> tuple[bool, str]:
    """Report CUDA availability without loading a model."""
    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
        if count > 0:
            return True, f"{count} device(s)"
        return False, "no CUDA device reported by ctranslate2"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
