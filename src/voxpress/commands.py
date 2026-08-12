"""Commands the hotkey layer hands to the worker, and worker acknowledgements.

The split matters: the hotkey safety contract requires the low-level hook
to classify an event and enqueue a command, nothing more.  Audio, Whisper,
tray, sounds and clipboard all happen on the worker side of this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CommandType(str, Enum):
    START_RECORDING = "start_recording"
    STOP_RECORDING = "stop_recording"
    CANCEL_RECORDING = "cancel_recording"
    BUSY_CUE = "busy_cue"


@dataclass(frozen=True, slots=True)
class Command:
    type: CommandType
    generation: int
    cycle_id: int
    reason: str = ""


class WorkerEvent(str, Enum):
    """Asynchronous results flowing back from the worker to the state machine."""

    RECORDING_STARTED = "recording_started"
    RECORDING_START_FAILED = "recording_start_failed"
    RECORDING_STOPPED = "recording_stopped"
    TRANSCRIPTION_FINISHED = "transcription_finished"
    FAILED = "failed"
