from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxpress.app import TOGGLE_ONLY_THRESHOLD_SEC, resolve_hotkey
from voxpress.config import VoxpressConfig
from voxpress.hotkey_hook import HotkeyHook
from voxpress.hotkey_state import HotkeyConfig
from voxpress.paste import PasteOutcome
from voxpress.safe_logging import setup_logging
from voxpress.stt import WhisperEngine
from voxpress.tray import VoxPressTray


class FakeKeyboard:
    CODES = {
        "j": (36,),
        "alt": (56,),
        "windows": (91, 92),
        "ctrl": (29, 3613),
        "shift": (42, 54),
    }

    @classmethod
    def key_to_scan_codes(cls, name: str):
        return cls.CODES[name]


def test_toggle_default_keeps_v01_press_again_behavior_and_consumes_shortcut() -> None:
    resolved = resolve_hotkey(VoxpressConfig(), keyboard_module=FakeKeyboard)
    assert resolved.main_scan_codes == frozenset({36})
    assert resolved.modifier_scan_codes == frozenset({56})
    assert resolved.hold_threshold_sec == TOGGLE_ONLY_THRESHOLD_SEC
    assert resolved.suppress_main_key is True


def test_hybrid_reserved_hotkey_uses_real_threshold_and_suppression() -> None:
    config = VoxpressConfig(
        hotkey="ctrl+windows",
        interaction_mode="hybrid",
        hold_threshold_ms=300,
    )
    resolved = resolve_hotkey(config, keyboard_module=FakeKeyboard)
    assert resolved.main_scan_codes == frozenset({91, 92})
    assert resolved.modifier_scan_codes == frozenset({29, 3613})
    assert resolved.hold_threshold_sec == 0.3
    assert resolved.suppress_main_key is True


def test_resolve_hotkey_preserves_one_group_per_logical_modifier() -> None:
    resolved = resolve_hotkey(VoxpressConfig(hotkey="ctrl+shift+j"), keyboard_module=FakeKeyboard)
    assert resolved.modifier_scan_codes == frozenset({29, 3613, 42, 54})
    assert resolved.modifier_scan_code_groups == (
        frozenset({29, 3613}),
        frozenset({42, 54}),
    )


def test_metadata_logger_redacts_private_strings(tmp_path: Path) -> None:
    logger = setup_logging(log_dir=tmp_path)
    logger.info(
        "transcribed",
        transcript_text="do not write this phrase",
        prompt="private vocabulary",
        detail="possibly sensitive exception detail",
        chars=24,
        outcome="pasted",
        model="C:/Users/example/private-model",
    )
    logger.close()
    body = (tmp_path / "voxpress.log").read_text(encoding="utf-8")
    assert "do not write this phrase" not in body
    assert "private vocabulary" not in body
    assert "possibly sensitive exception detail" not in body
    payload = json.loads(body.strip())
    assert payload["chars"] == 24
    assert payload["outcome"] == "pasted"
    assert payload["transcript_text"] == "[redacted]"
    assert payload["model"] == "[custom-model]"
    logger.close()  # idempotent


def test_hook_diagnostics_never_wait_for_the_rotating_file_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voxpress import safe_logging

    entered = threading.Event()
    release = threading.Event()

    class BlockingHandler(logging.Handler):
        def __init__(self, *_args, **_kwargs) -> None:
            super().__init__()

        def emit(self, _record: logging.LogRecord) -> None:
            entered.set()
            release.wait(timeout=2.0)

    monkeypatch.setattr(safe_logging, "RotatingFileHandler", BlockingHandler)
    logger = safe_logging.setup_logging(log_dir=tmp_path, diagnostics=True)
    hook = HotkeyHook(
        HotkeyConfig(
            main_scan_codes=frozenset({36}),
            modifier_scan_codes=frozenset({56}),
            suppress_main_key=False,
        ),
        logger=logger,
    )
    hook._on_raw_event(SimpleNamespace(scan_code=56, name="left alt", event_type="down"))
    started = time.perf_counter()
    assert hook._on_raw_event(SimpleNamespace(scan_code=36, name="j", event_type="down"))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1
    assert entered.wait(timeout=1.0)
    release.set()
    logger.close()
    logger.close()


class FakeIcon:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []

    def notify(self, message: str, title: str) -> None:
        self.notifications.append((message, title))


def test_tray_notification_contains_metadata_only(tmp_path: Path) -> None:
    tray = VoxPressTray(
        hotkey="alt+j",
        config_path=tmp_path / ".voxpress.toml",
        on_quit=lambda: None,
        status_text=lambda: "idle",
    )
    icon = FakeIcon()
    tray._icon = icon  # type: ignore[attr-defined]
    tray.notify_ready(17, PasteOutcome.PASTED)
    assert icon.notifications == [("Transcription ready: 17 characters (pasted).", "VoxPress")]


class FakeSegment:
    text = "compatibility result"


class FakeInfo:
    language = "en"
    duration = 0.5


class FakeModel:
    def transcribe(self, _path: str, **_kwargs):
        return [FakeSegment()], FakeInfo()


def test_v01_transcribe_wav_api_remains_compatible(tmp_path: Path) -> None:
    engine = WhisperEngine(
        "tiny",
        "cpu",
        "int8",
        "en",
        "synthetic prompt",
        model_factory=lambda *_args, **_kwargs: FakeModel(),
        temp_dir=tmp_path,
    )
    result = engine.transcribe_wav(b"synthetic wav")
    assert result == {
        "text": "compatibility result",
        "language": "en",
        "duration": 0.5,
    }


def test_check_config_cli_never_prints_prompt_or_paths(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from voxpress import __main__ as cli

    config = VoxpressConfig(
        model="C:/Users/example/private-model",
        initial_prompt="private vocabulary",
        vocab_path="C:/private/vocab.json",
        corrections_path="C:/private/corrections.json",
        sources=["defaults", "C:/Users/example/.voxpress.toml"],
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    assert cli.main(["--check-config"]) == 0
    output = capsys.readouterr().out
    assert "private vocabulary" not in output
    assert "C:/private" not in output
    assert "C:/Users" not in output
    assert "[custom-model]" in output
