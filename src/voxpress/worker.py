"""Bounded worker pipeline from hotkey commands to local transcription."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from voxpress.audio import AudioService, StoppedRecording, concatenate
from voxpress.commands import Command, CommandType, WorkerEvent
from voxpress.errors import ErrorCode, SttError
from voxpress.paste import PasteAdapter, PasteOutcome, WindowInfo, foreground_window
from voxpress.transcribe import WhisperEngine, make_wav_bytes

TRANSCRIBE_QUEUE_MAXSIZE = 8


class TextProcessor(Protocol):  # pragma: no cover - structural
    def prompt_for_next(self) -> str | None: ...

    def postprocess(self, text: str) -> str: ...


class PassthroughTextProcessor:
    def prompt_for_next(self) -> str | None:
        return None

    def postprocess(self, text: str) -> str:
        return text


class CuePlayer(Protocol):  # pragma: no cover - structural
    def play(self, cue: str) -> None: ...


class NullCuePlayer:
    def play(self, cue: str) -> None:
        del cue


def apply_prefix(
    text: str,
    *,
    prefix: str = "",
    window_filters: str = "",
    window_title: str = "",
) -> str:
    """Apply an optional marker globally or to matching window titles only."""

    if not prefix:
        return text
    wanted = [item.strip().casefold() for item in window_filters.split(",") if item.strip()]
    if wanted and not any(item in window_title.casefold() for item in wanted):
        return text
    return prefix + text


@dataclass(frozen=True)
class _TranscribeJob:
    recording: StoppedRecording
    target: WindowInfo
    requested_at: float


class RecordingWorker:
    """Own slow work outside the low-level keyboard callback.

    A short command thread opens and closes audio. A separate transcription
    thread owns Whisper and paste delivery, so inference cannot block hotkey
    acknowledgements or event pairing.
    """

    def __init__(
        self,
        *,
        hook,
        audio: AudioService,
        engine: WhisperEngine,
        paste: PasteAdapter,
        text_processor: TextProcessor | None = None,
        cue_player: CuePlayer | None = None,
        paste_method: str = "ctrl_v",
        initial_prompt: str = "",
        prefix: str = "",
        prefix_windows: str = "",
        window_probe: Callable[[], WindowInfo] = foreground_window,
        on_state_change: Callable[[str], None] | None = None,
        on_transcription_ready: Callable[[int, PasteOutcome], None] | None = None,
        logger=None,
    ) -> None:
        self._hook = hook
        self._audio = audio
        self._engine = engine
        self._paste = paste
        self._text_processor = text_processor or PassthroughTextProcessor()
        self._cue_player = cue_player or NullCuePlayer()
        self._paste_method = paste_method
        self._initial_prompt = initial_prompt
        self._prefix = prefix
        self._prefix_windows = prefix_windows
        self._window_probe = window_probe
        self._on_state_change = on_state_change or (lambda _state: None)
        self._on_transcription_ready = on_transcription_ready or (lambda _length, _outcome: None)
        self._logger = logger

        self._transcribe_queue: queue.Queue[_TranscribeJob | None] = queue.Queue(
            TRANSCRIBE_QUEUE_MAXSIZE
        )
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        self.last_success_at: float | None = None
        self.last_error_code: str | None = None
        self.completed = 0

    def start(self) -> None:
        if self._threads:
            return
        self._threads = [
            threading.Thread(target=self._command_loop, name="voxpress-commands", daemon=True),
            threading.Thread(
                target=self._transcribe_loop,
                name="voxpress-transcribe",
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        try:
            self._transcribe_queue.put_nowait(None)
        except queue.Full:
            pass
        self._audio.cancel()
        threads = tuple(self._threads)
        for thread in threads:
            thread.join(timeout=timeout)
        self._threads = [thread for thread in threads if thread.is_alive()]

    def _command_loop(self) -> None:
        while not self._stop.is_set():
            try:
                command = self._hook.commands.get(timeout=0.25)
            except queue.Empty:
                continue
            except Exception:  # pragma: no cover - defensive
                continue
            try:
                self._handle(command)
            except Exception as exc:  # pragma: no cover - defensive
                self._fail(command.generation, ErrorCode.UNKNOWN, exc)

    def _handle(self, command: Command) -> None:
        if command.type is CommandType.START_RECORDING:
            self._do_start(command)
        elif command.type is CommandType.STOP_RECORDING:
            self._do_stop(command)
        elif command.type is CommandType.CANCEL_RECORDING:
            self._do_cancel(command)
        elif command.type is CommandType.BUSY_CUE:
            self._cue_player.play("busy")

    def _do_start(self, command: Command) -> None:
        self._cue_player.play("start")
        try:
            self._audio.start(command.generation)
        except SttError as exc:
            self.last_error_code = exc.code
            self._hook.acknowledge(WorkerEvent.RECORDING_START_FAILED, command.generation, exc.code)
            self._on_state_change("error")
            if self._logger:
                self._logger.error(
                    "recording_start_failed",
                    generation=command.generation,
                    error_code=exc.code,
                )
            return
        self._hook.acknowledge(WorkerEvent.RECORDING_STARTED, command.generation)
        self._on_state_change("recording")

    def _do_stop(self, command: Command) -> None:
        self._cue_player.play("stop")
        target = self._safe_window()
        recording = self._audio.stop()
        self._hook.acknowledge(WorkerEvent.RECORDING_STOPPED, command.generation)
        if recording is None or recording.empty:
            self._finish(command.generation, ErrorCode.AUDIO_EMPTY)
            return

        self._on_state_change("transcribing")
        job = _TranscribeJob(
            recording=recording,
            target=target,
            requested_at=time.time(),
        )
        try:
            self._transcribe_queue.put_nowait(job)
        except queue.Full:
            self._finish(command.generation, ErrorCode.UNKNOWN)

    def _do_cancel(self, command: Command) -> None:
        self._audio.cancel()
        self._finish(command.generation, None)

    def _transcribe_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._transcribe_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if job is None:
                return
            if self._stop.is_set():
                return
            try:
                self._transcribe(job)
            except Exception as exc:  # pragma: no cover - defensive
                if not self._stop.is_set():
                    self._fail(job.recording.generation, ErrorCode.STT_FAILED, exc)

    def _transcribe(self, job: _TranscribeJob) -> None:
        generation = job.recording.generation
        if self._stop.is_set():
            return
        audio = concatenate(job.recording.chunks)
        if audio is None or getattr(audio, "size", 0) == 0:
            self._finish(generation, ErrorCode.AUDIO_EMPTY)
            return

        wav = make_wav_bytes(audio, sample_rate=job.recording.sample_rate)
        dynamic_prompt = self._text_processor.prompt_for_next()
        prompt = " ".join(
            part.strip() for part in (self._initial_prompt, dynamic_prompt or "") if part.strip()
        )[:400]
        try:
            result = self._engine.transcribe(
                wav,
                initial_prompt=prompt or None,
            )
        except SttError as exc:
            if self._stop.is_set():
                return
            self._finish(generation, exc.code)
            if self._logger:
                self._logger.error(
                    "transcription_failed",
                    generation=generation,
                    error_code=exc.code,
                )
            return

        if self._stop.is_set():
            return

        text = self._text_processor.postprocess(result.text or "")
        text = apply_prefix(
            text,
            prefix=self._prefix,
            window_filters=self._prefix_windows,
            window_title=job.target.title,
        )
        if self._logger:
            self._logger.info(
                "transcribed",
                generation=generation,
                chars=len(text),
                audio_sec=round(result.duration, 2),
                stt_ms=round(result.elapsed_sec * 1000),
                realtime_factor=round(result.realtime_factor, 2),
            )

        if not text:
            self._finish(generation, ErrorCode.STT_EMPTY_RESULT)
            return

        if self._stop.is_set():
            return

        outcome = self._paste.deliver(
            text,
            method=self._paste_method,
            target=job.target,
            cancelled=self._stop.is_set,
        )
        if self._stop.is_set():
            return
        self._on_transcription_ready(len(text), outcome.outcome)
        self.completed += 1
        if outcome.outcome is PasteOutcome.PASTED:
            self.last_success_at = time.time()
            self._finish(generation, None)
        else:
            self._finish(generation, outcome.error_code)

    def _safe_window(self) -> WindowInfo:
        try:
            return self._window_probe()
        except Exception:
            return WindowInfo()

    def _finish(self, generation: int, error_code: str | None) -> None:
        if error_code:
            self.last_error_code = error_code
        self._hook.acknowledge(
            WorkerEvent.TRANSCRIPTION_FINISHED,
            generation,
            error_code,
        )
        self._on_state_change("idle")

    def _fail(self, generation: int, code: str, exc: BaseException) -> None:
        self.last_error_code = code
        if self._logger:
            self._logger.error(
                "worker_failed",
                generation=generation,
                error_code=code,
                error=type(exc).__name__,
            )
        self._hook.acknowledge(WorkerEvent.FAILED, generation, code)
        self._on_state_change("idle")

    def health(self) -> dict:
        return {
            "completed": self.completed,
            "last_success_at": self.last_success_at,
            "last_error_code": self.last_error_code,
            "transcribe_backlog": self._transcribe_queue.qsize(),
        }
