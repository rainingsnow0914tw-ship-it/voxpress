from __future__ import annotations

from pathlib import Path

import pytest

from voxpress.config import (
    CONFIG_SCHEMA_VERSION,
    CUSTOM_MODEL_LABEL,
    VoxpressConfig,
    load_config,
    parse_hotkey,
    safe_model_label,
)

V01_CONFIG = """\
hotkey = "ctrl+shift+k"
model = "small"
device = "cpu"
compute_type = "int8"
language = "zh"
initial_prompt = "synthetic project vocabulary"
paste_method = "clipboard_only"
notify = false
sample_rate = 16000
"""


def test_public_defaults_preserve_v01_workflow() -> None:
    config = VoxpressConfig()
    assert config.hotkey == "alt+j"
    assert config.interaction_mode == "toggle"
    assert config.model == "large-v3"
    assert config.language == "auto"
    assert config.paste_method == "ctrl_v"
    assert config.notify is True
    assert config.prefix == ""
    assert config.prefix_windows == ""


def test_v01_toml_migrates_every_value(tmp_path: Path) -> None:
    target = tmp_path / ".voxpress.toml"
    target.write_text(V01_CONFIG, encoding="utf-8")
    config = load_config(config_path=target)
    assert config.hotkey == "ctrl+shift+k"
    assert config.model == "small"
    assert config.device == "cpu"
    assert config.compute_type == "int8"
    assert config.language == "zh"
    assert config.initial_prompt == "synthetic project vocabulary"
    assert config.paste_method == "clipboard_only"
    assert config.notify is False
    assert str(target) in config.sources


def test_environment_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / ".voxpress.toml"
    target.write_text('model = "small"\n', encoding="utf-8")
    monkeypatch.setenv("VOXPRESS_MODEL", "tiny")
    monkeypatch.setenv("VOXPRESS_DEVICE", "cpu")
    config = load_config(config_path=target)
    assert config.model == "tiny"
    assert config.device == "cpu"
    assert "environment" in config.sources


@pytest.mark.parametrize(
    "line,field,expected_default",
    [
        ('paste_method = "telepathy"', "paste_method", "ctrl_v"),
        ('device = "quantum"', "device", "cpu"),
        ('interaction_mode = "mystery"', "interaction_mode", "toggle"),
        ("hold_threshold_ms = 99999", "hold_threshold_ms", 300),
        ("sample_rate = 12345", "sample_rate", 16000),
        ('hotkey = "   "', "hotkey", "alt+j"),
        ("paste_delay_ms = -5", "paste_delay_ms", 200),
        ("temp_audio_ttl_hours = 0", "temp_audio_ttl_hours", 24),
        ('notify = "perhaps"', "notify", True),
    ],
)
def test_invalid_values_fall_back_with_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    line: str,
    field: str,
    expected_default,
) -> None:
    monkeypatch.setattr("voxpress.config._detect_device", lambda: "cpu")
    target = tmp_path / ".voxpress.toml"
    target.write_text(line + "\n", encoding="utf-8")
    config = load_config(config_path=target)
    assert getattr(config, field) == expected_default
    assert any(field in warning for warning in config.warnings)


def test_unknown_key_is_reported(tmp_path: Path) -> None:
    target = tmp_path / ".voxpress.toml"
    target.write_text('hotkeys = "ctrl+alt"\n', encoding="utf-8")
    config = load_config(config_path=target)
    assert any("hotkeys" in warning for warning in config.warnings)
    assert config.hotkey == "alt+j"


def test_unparseable_toml_does_not_crash(tmp_path: Path) -> None:
    target = tmp_path / ".voxpress.toml"
    target.write_text("this is [not valid", encoding="utf-8")
    config = load_config(config_path=target)
    assert config.hotkey == "alt+j"
    assert any("could not parse" in warning for warning in config.warnings)


def test_public_dict_omits_sensitive_context_and_paths() -> None:
    config = VoxpressConfig(
        model="C:/Users/example/private-model",
        initial_prompt="private terms",
        prefix="private prefix",
        prefix_windows="private title",
        vocab_path="C:/private/vocab.json",
        corrections_path="C:/private/corrections.json",
        sources=["defaults", "C:/Users/example/.voxpress.toml", "environment"],
        warnings=["sample_rate='C:/Users/example/private-value' is invalid"],
    )
    payload = config.public_dict()
    assert payload["schema_version"] == CONFIG_SCHEMA_VERSION
    for key in (
        "initial_prompt",
        "prefix",
        "prefix_windows",
        "vocab_path",
        "corrections_path",
    ):
        assert key not in payload
    assert payload["sources"] == ["defaults", ".voxpress.toml", "environment"]
    assert payload["model"] == "[custom-model]"
    assert payload["warning_count"] == 1
    assert "warnings" not in payload
    assert "C:/Users" not in str(payload)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("large-v3", "large-v3"),
        ("tiny.en", "tiny.en"),
        ("private-client-model", CUSTOM_MODEL_LABEL),
        ("org/private-model", CUSTOM_MODEL_LABEL),
        ("../private-model", CUSTOM_MODEL_LABEL),
    ],
)
def test_model_label_only_exposes_known_public_aliases(value: str, expected: str) -> None:
    assert safe_model_label(value) == expected


def test_mapping_api_keeps_v01_callers_working() -> None:
    config = VoxpressConfig(model="tiny")
    assert config["model"] == "tiny"
    assert config.get("paste_method") == "ctrl_v"
    with pytest.raises(KeyError):
        _ = config["missing"]


@pytest.mark.parametrize(
    "hotkey,expected",
    [
        ("ctrl+windows", ("windows", ("ctrl",))),
        ("alt+j", ("j", ("alt",))),
        ("ctrl+shift+space", ("space", ("ctrl", "shift"))),
        ("z", ("z", ())),
        ("", ("j", ("alt",))),
    ],
)
def test_hotkey_parsing(hotkey: str, expected: tuple[str, tuple[str, ...]]) -> None:
    assert parse_hotkey(hotkey) == expected


def test_personalization_paths_are_user_scoped() -> None:
    config = VoxpressConfig()
    assert config.resolved_vocab_path().parent.name == ".voxpress"
    assert config.resolved_corrections_path().parent.name == ".voxpress"
