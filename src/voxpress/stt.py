"""Whisper STT engine — lazy load, in-process, no HTTP."""
import os
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Optional


def _ensure_cuda_libs_in_path():
    """nvidia-cublas-cu12 + nvidia-cudnn-cu12 DLL 路徑加進 Windows DLL search、
    給 CTranslate2 找。faster-whisper GPU 模式需要。"""
    if sys.platform != "win32":
        return
    try:
        import nvidia.cublas.lib
        cublas_dir = Path(nvidia.cublas.lib.__file__).parent
        os.add_dll_directory(str(cublas_dir))
        os.environ["PATH"] = str(cublas_dir) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass
    try:
        import nvidia.cudnn.lib
        cudnn_dir = Path(nvidia.cudnn.lib.__file__).parent
        os.add_dll_directory(str(cudnn_dir))
        os.environ["PATH"] = str(cudnn_dir) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


class WhisperEngine:
    """faster-whisper 包裝、lazy load、thread-safe transcribe。"""

    def __init__(self, model_size: str = "large-v3",
                 device: str = "cpu", compute_type: str = "int8",
                 language: Optional[str] = None,
                 initial_prompt: str = ""):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = None if not language or language == "auto" else language
        self.initial_prompt = initial_prompt or None
        self._model = None
        self._lock = threading.Lock()
        if device == "cuda":
            _ensure_cuda_libs_in_path()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self):
        """eager load — 預載模型省第一次延遲。"""
        with self._lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel
            print(f"[voxpress] loading whisper {self.model_size} on {self.device} ({self.compute_type})")
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
            print(f"[voxpress] whisper loaded")

    def transcribe_wav(self, wav_bytes: bytes) -> dict:
        """收 wav bytes、回 {text, language, duration}。"""
        if self._model is None:
            self.load()
        # 寫到 tmp file（faster-whisper 接 path 比較穩）
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            path = f.name
        try:
            segments, info = self._model.transcribe(
                path,
                language=self.language,
                task="transcribe",
                initial_prompt=self.initial_prompt,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            text_parts = [s.text for s in segments]
            return {
                "text": "".join(text_parts).strip(),
                "language": info.language,
                "duration": info.duration,
            }
        finally:
            try: Path(path).unlink(missing_ok=True)
            except Exception: pass


def make_wav_bytes(audio_float32, sample_rate: int = 16000) -> bytes:
    """numpy float32 mono [-1, 1] → 16-bit PCM WAV bytes。"""
    import numpy as np
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        audio_i16 = np.clip(audio_float32 * 32767, -32768, 32767).astype(np.int16)
        wf.writeframes(audio_i16.tobytes())
    return buf.getvalue()
