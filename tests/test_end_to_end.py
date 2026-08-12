"""End-to-end dictation flow with every operating-system boundary faked."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from voxpress.audio import AudioService
from voxpress.hotkey_hook import HotkeyHook
from voxpress.hotkey_state import HotkeyConfig, State
from voxpress.paste import PasteAdapter, WindowInfo
from voxpress.personalize import PersonalizationStore
from voxpress.transcribe import WhisperEngine
from voxpress.worker import RecordingWorker, apply_prefix

HOTKEY = HotkeyConfig(
    main_scan_codes=frozenset({91, 92}),
    modifier_scan_codes=frozenset({29}),
)


@dataclass
class FakeEvent:
    scan_code: int
    name: str
    event_type: str


class FakeStream:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.closed = False

    def start(self) -> None:
        self.emit(0.2)

    def emit(self, value: float) -> None:
        import numpy as np

        self.callback(np.full((160, 1), value, dtype="float32"), 160, None, None)

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeInfo:
    language = "zh"
    duration = 1.5


class FakeModel:
    def __init__(self, text: str = "今天天氣很好") -> None:
        self.text = text
        self.prompts: list[str | None] = []

    def transcribe(self, _path: str, **kwargs):
        self.prompts.append(kwargs.get("initial_prompt"))
        return iter([FakeSegment(self.text)]), FakeInfo()


class BlockingModel(FakeModel):
    def __init__(self, text: str = "不應晚貼上的內容") -> None:
        super().__init__(text)
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe(self, _path: str, **kwargs):
        self.prompts.append(kwargs.get("initial_prompt"))
        self.started.set()
        self.release.wait(timeout=5.0)
        return iter([FakeSegment(self.text)]), FakeInfo()


class FakeClipboard:
    def __init__(self) -> None:
        self.value: str | None = None

    def copy(self, text: str) -> None:
        self.value = text


class FakeKeys:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, combination: str) -> None:
        self.sent.append(combination)


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        transcript: str = "今天天氣很好",
        modifiers_held=None,
        prefix: str = "",
        prefix_windows: str = "",
        window_title: str = "Notepad",
        model: FakeModel | None = None,
    ) -> None:
        self.streams: list[FakeStream] = []
        self.clock_value = 100.0

        def clock() -> float:
            return self.clock_value

        def stream_factory(**kwargs):
            stream = FakeStream(kwargs["callback"])
            self.streams.append(stream)
            return stream

        window = WindowInfo(1, window_title, "Edit")
        self.hook = HotkeyHook(HOTKEY, clock=clock)
        self.audio = AudioService(16000, stream_factory=stream_factory)
        self.model = model or FakeModel(transcript)
        self.engine = WhisperEngine(
            model_size="tiny",
            device="cpu",
            compute_type="int8",
            model_factory=lambda *_args, **_kwargs: self.model,
            temp_dir=tmp_path,
        )
        self.personalization = PersonalizationStore(
            tmp_path / "vocab.json",
            tmp_path / "corrections.json",
        )
        self.clipboard = FakeClipboard()
        self.keys = FakeKeys()
        self.paste = PasteAdapter(
            clipboard=self.clipboard,
            key_sender=self.keys,
            modifiers_held=modifiers_held or (lambda: False),
            window_probe=lambda: window,
            paste_delay_ms=0,
            modifier_timeout_ms=100,
        )
        self.states: list[str] = []
        self.ready: list[tuple[int, object]] = []
        self.worker = RecordingWorker(
            hook=self.hook,
            audio=self.audio,
            engine=self.engine,
            text_processor=self.personalization,
            paste=self.paste,
            prefix=prefix,
            prefix_windows=prefix_windows,
            window_probe=lambda: window,
            on_state_change=self.states.append,
            on_transcription_ready=lambda length, outcome: self.ready.append((length, outcome)),
        )
        self.worker.start()

    def advance_ms(self, milliseconds: float) -> None:
        self.clock_value += milliseconds / 1000.0

    def key(self, scan: int, name: str, kind: str) -> None:
        self.hook._on_raw_event(FakeEvent(scan, name, kind))

    def press(self, hold_ms: float) -> None:
        self.key(29, "left ctrl", "down")
        self.key(91, "left windows", "down")
        self.advance_ms(hold_ms)
        self.key(91, "left windows", "up")
        self.key(29, "left ctrl", "up")

    def speak(self, value: float = 0.3) -> None:
        for stream in self.streams:
            if not stream.closed:
                stream.emit(value)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.hook.machine.state is State.IDLE and not self.hook.commands.qsize():
                return True
            time.sleep(0.01)
        return False

    def close(self) -> None:
        self.worker.stop(timeout=2.0)


@pytest.fixture
def harness(tmp_path: Path):
    value = Harness(tmp_path)
    yield value
    value.close()


def test_long_press_records_transcribes_and_pastes(harness: Harness) -> None:
    harness.key(29, "left ctrl", "down")
    harness.key(91, "left windows", "down")
    time.sleep(0.05)
    harness.speak()
    harness.advance_ms(800)
    harness.key(91, "left windows", "up")
    harness.key(29, "left ctrl", "up")

    assert harness.wait_idle()
    assert harness.clipboard.value == "今天天氣很好"
    assert harness.keys.sent == ["ctrl+v"]
    assert harness.hook.machine.pending_suppressed_keys() == ()


def test_short_press_toggle_then_second_press_completes(harness: Harness) -> None:
    harness.press(150)
    time.sleep(0.05)
    assert harness.hook.machine.state is State.TOGGLE_RECORDING
    harness.speak()
    harness.advance_ms(5000)
    harness.press(120)
    assert harness.wait_idle()
    assert harness.clipboard.value == "今天天氣很好"
    assert harness.keys.sent == ["ctrl+v"]


def test_synthetic_corrections_and_vocabulary_are_applied(tmp_path: Path) -> None:
    (tmp_path / "vocab.json").write_text(
        '{"style_hint": "繁體中文口語", "terms": ["量子貓", "星河"]}',
        encoding="utf-8",
    )
    (tmp_path / "corrections.json").write_text(
        '{"量子喵": "量子貓"}',
        encoding="utf-8",
    )
    value = Harness(tmp_path, transcript="跟量子喵說一聲")
    try:
        value.key(29, "left ctrl", "down")
        value.key(91, "left windows", "down")
        time.sleep(0.05)
        value.speak()
        value.advance_ms(600)
        value.key(91, "left windows", "up")
        value.key(29, "left ctrl", "up")
        assert value.wait_idle()
        assert value.clipboard.value == "跟量子貓說一聲"
        assert value.model.prompts[0] is not None
        assert "量子貓" in value.model.prompts[0]
    finally:
        value.close()


def test_modifiers_held_leaves_text_in_clipboard(tmp_path: Path) -> None:
    value = Harness(tmp_path, modifiers_held=lambda: True)
    try:
        value.key(29, "left ctrl", "down")
        value.key(91, "left windows", "down")
        time.sleep(0.05)
        value.speak()
        value.advance_ms(600)
        value.key(91, "left windows", "up")
        value.key(29, "left ctrl", "up")
        assert value.wait_idle()
        assert value.clipboard.value == "今天天氣很好"
        assert value.keys.sent == []
    finally:
        value.close()


def test_audio_start_failure_recovers_to_idle(tmp_path: Path) -> None:
    value = Harness(tmp_path)
    try:

        def exploding_factory(**_kwargs):
            raise RuntimeError("Error opening InputStream: [PaErrorCode -9999]")

        value.audio._factory = exploding_factory  # type: ignore[attr-defined]
        value.press(600)
        assert value.wait_idle()
        assert value.hook.machine.pending_suppressed_keys() == ()

        value.audio._factory = lambda **kwargs: _register(value, kwargs)  # type: ignore[attr-defined]
        value.advance_ms(1000)
        value.key(29, "left ctrl", "down")
        value.key(91, "left windows", "down")
        time.sleep(0.05)
        value.speak()
        value.advance_ms(600)
        value.key(91, "left windows", "up")
        value.key(29, "left ctrl", "up")
        assert value.wait_idle()
        assert value.clipboard.value == "今天天氣很好"
    finally:
        value.close()


def _register(harness: Harness, kwargs) -> FakeStream:
    stream = FakeStream(kwargs["callback"])
    harness.streams.append(stream)
    return stream


def test_fifty_alternating_cycles_leave_no_key_held(tmp_path: Path) -> None:
    value = Harness(tmp_path)
    try:
        for index in range(50):
            hold = 120 if index % 2 == 0 else 600
            value.key(29, "left ctrl", "down")
            value.key(91, "left windows", "down")
            time.sleep(0.002)
            value.speak()
            value.advance_ms(hold)
            value.key(91, "left windows", "up")
            value.key(29, "left ctrl", "up")
            if hold == 120:
                value.advance_ms(50)
                value.press(80)
            assert value.wait_idle(), f"cycle {index} did not return to idle"
            assert value.hook.machine.pending_suppressed_keys() == ()
        assert value.worker.completed == 50
        assert value.hook.machine.stats["starts"] == 50
        assert value.hook.machine.stats["stops"] == 50
    finally:
        value.close()


def test_shutdown_releases_held_key_and_closes_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = Harness(tmp_path)
    released: list[int] = []
    monkeypatch.setattr(
        "voxpress.winkeys.send_key_up",
        lambda virtual_key: released.append(virtual_key) or True,
    )
    value.key(29, "left ctrl", "down")
    value.key(91, "left windows", "down")
    time.sleep(0.05)
    assert value.hook.machine.pending_suppressed_keys()
    value.hook.recover("shutdown")
    value.close()
    assert released
    assert all(stream.closed for stream in value.streams)


def test_prefix_is_neutral_and_window_scoped() -> None:
    assert apply_prefix("hello") == "hello"
    assert apply_prefix("hello", prefix="🎤 ") == "🎤 hello"
    assert (
        apply_prefix(
            "hello",
            prefix="🎤 ",
            window_filters="Chat,Editor",
            window_title="Notes",
        )
        == "hello"
    )
    assert (
        apply_prefix(
            "hello",
            prefix="🎤 ",
            window_filters="Chat,Editor",
            window_title="PROJECT chat",
        )
        == "🎤 hello"
    )


@pytest.mark.parametrize(
    "payload",
    (
        "https://example.com/A%2FB?q=Exact+Case",
        'print("模式密碼")\nvalue = "Bispy"',
        "Copied TEXT: punctuation?!  spacing",
    ),
)
def test_prefix_preserves_code_urls_and_copied_payload_exactly(payload: str) -> None:
    assert (
        apply_prefix(
            payload,
            prefix="🎤 ",
            window_filters="Assistant A,Assistant B",
            window_title="assistant b - conversation",
        )
        == "🎤 " + payload
    )


def test_shutdown_during_inference_suppresses_all_late_side_effects(tmp_path: Path) -> None:
    model = BlockingModel()
    value = Harness(tmp_path, model=model)
    try:
        value.key(29, "left ctrl", "down")
        value.key(91, "left windows", "down")
        time.sleep(0.05)
        value.speak()
        value.advance_ms(600)
        value.key(91, "left windows", "up")
        value.key(29, "left ctrl", "up")
        assert model.started.wait(timeout=2.0)

        threads = tuple(value.worker._threads)  # type: ignore[attr-defined]
        value.worker.stop(timeout=0.01)
        model.release.set()
        for thread in threads:
            thread.join(timeout=2.0)
            assert not thread.is_alive()

        assert value.clipboard.value is None
        assert value.keys.sent == []
        assert value.ready == []
        assert value.worker.completed == 0
        assert value.worker.last_success_at is None
    finally:
        model.release.set()
        value.close()
