"""Compatibility layer for the public VoxPress v0.1 transcription API."""

from __future__ import annotations

from typing import Optional

from voxpress.transcribe import WhisperEngine as _WhisperEngine
from voxpress.transcribe import make_wav_bytes


class WhisperEngine(_WhisperEngine):
    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cpu",
        compute_type: str = "int8",
        language: Optional[str] = None,
        initial_prompt: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            model_size=model_size,
            device=device,
            compute_type=compute_type,
            language=language,
            **kwargs,
        )
        self.initial_prompt = initial_prompt or None

    def transcribe_wav(self, wav_bytes: bytes) -> dict:
        result = self.transcribe(wav_bytes, initial_prompt=self.initial_prompt)
        return {
            "text": result.text,
            "language": result.language,
            "duration": result.duration,
        }


__all__ = ["WhisperEngine", "make_wav_bytes"]
