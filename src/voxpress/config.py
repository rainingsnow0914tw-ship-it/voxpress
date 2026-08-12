"""Typed VoxPress configuration with safe validation and v0.1 compatibility."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


CONFIG_SCHEMA_VERSION = 1
CONFIG_PATH = Path.home() / ".voxpress.toml"
DATA_DIR = Path.home() / ".voxpress"

VALID_PASTE_METHODS = ("ctrl_v", "clipboard_only", "typing")
VALID_DEVICES = ("auto", "cuda", "cpu")
VALID_COMPUTE_TYPES = ("auto", "float16", "int8", "int8_float16", "float32")
VALID_INTERACTION_MODES = ("toggle", "hybrid")
CUSTOM_MODEL_LABEL = "[custom-model]"
PUBLIC_MODEL_ALIASES = frozenset(
    {
        "tiny",
        "tiny.en",
        "base",
        "base.en",
        "small",
        "small.en",
        "medium",
        "medium.en",
        "large-v1",
        "large-v2",
        "large-v3",
        "large-v3-turbo",
        "turbo",
    }
)


def safe_model_label(value: Any) -> str:
    """Return a public label without exposing a local model path or repo id."""

    label = str(value or "").strip()
    if label in PUBLIC_MODEL_ALIASES:
        return label
    return CUSTOM_MODEL_LABEL


@dataclass
class VoxpressConfig(Mapping[str, Any]):
    """Validated settings.

    ``Mapping`` support preserves the v0.1 ``config["model"]`` and ``get`` API
    while new code can use typed attributes.
    """

    schema_version: int = CONFIG_SCHEMA_VERSION
    hotkey: str = "alt+j"
    interaction_mode: str = "toggle"
    hold_threshold_ms: int = 300
    model: str = "large-v3"
    device: str = "auto"
    compute_type: str = "auto"
    language: str = "auto"
    initial_prompt: str = ""
    paste_method: str = "ctrl_v"
    paste_delay_ms: int = 200
    paste_modifier_timeout_ms: int = 2000
    strict_focus_guard: bool = False
    auto_release_stuck_win: bool = True
    notify: bool = True
    sample_rate: int = 16000
    prefix: str = ""
    prefix_windows: str = ""
    vocab_path: str = ""
    corrections_path: str = ""
    temp_audio_ttl_hours: int = 24
    warnings: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def hold_threshold_sec(self) -> float:
        return self.hold_threshold_ms / 1000.0

    def resolved_vocab_path(self) -> Path:
        return Path(self.vocab_path) if self.vocab_path else DATA_DIR / "vocab.json"

    def resolved_corrections_path(self) -> Path:
        return (
            Path(self.corrections_path) if self.corrections_path else DATA_DIR / "corrections.json"
        )

    def public_dict(self) -> dict[str, Any]:
        """Return metadata safe for routine status output.

        Prompts, prefixes and filesystem paths can reveal dictated context or
        local identity, so they are deliberately omitted.
        """

        payload = asdict(self)
        for name in (
            "initial_prompt",
            "prefix",
            "prefix_windows",
            "vocab_path",
            "corrections_path",
        ):
            payload.pop(name, None)
        payload["model"] = safe_model_label(self.model)
        payload["warning_count"] = len(self.warnings)
        payload.pop("warnings", None)
        payload["sources"] = [
            source if source in {"defaults", "environment"} else Path(source).name or "config_file"
            for source in self.sources
        ]
        return payload

    def __getitem__(self, key: str) -> Any:
        if key not in self.__dataclass_fields__:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.__dataclass_fields__)

    def __len__(self) -> int:
        return len(self.__dataclass_fields__)


# Backward-compatible name for integrations that imported a generic Config.
Config = VoxpressConfig

_RUNTIME_FIELDS = {"warnings", "sources"}
_FIELD_NAMES = {item.name for item in fields(VoxpressConfig)} - _RUNTIME_FIELDS


def _coerce(name: str, raw: Any, warnings: list[str]) -> Any | None:
    defaults = VoxpressConfig()
    expected = getattr(defaults, name)

    try:
        if isinstance(expected, bool):
            if isinstance(raw, bool):
                value = raw
            else:
                normalized = str(raw).strip().lower()
                if normalized in ("1", "true", "yes", "on"):
                    value = True
                elif normalized in ("0", "false", "no", "off"):
                    value = False
                else:
                    raise ValueError("invalid boolean")
        elif isinstance(expected, int):
            value = int(raw)
        elif isinstance(expected, str):
            value = str(raw)
        else:
            value = raw
    except (TypeError, ValueError):
        warnings.append(
            f"{name}={raw!r} is not a valid {type(expected).__name__}; using {expected!r}"
        )
        return None

    allowed = {
        "paste_method": VALID_PASTE_METHODS,
        "device": VALID_DEVICES,
        "compute_type": VALID_COMPUTE_TYPES,
        "interaction_mode": VALID_INTERACTION_MODES,
    }
    if name in allowed and value not in allowed[name]:
        warnings.append(f"{name}={value!r} unknown; using {expected!r}")
        return None
    if name == "hold_threshold_ms" and not 50 <= value <= 3000:
        warnings.append(f"hold_threshold_ms={value} out of range 50-3000; using {expected}")
        return None
    if name == "sample_rate" and value not in (8000, 16000, 22050, 44100, 48000):
        warnings.append(f"sample_rate={value} unusual; using {expected}")
        return None
    if name in ("paste_delay_ms", "paste_modifier_timeout_ms") and not 0 <= value <= 30000:
        warnings.append(f"{name}={value} out of range 0-30000; using {expected}")
        return None
    if name == "temp_audio_ttl_hours" and not 1 <= value <= 24 * 30:
        warnings.append(f"temp_audio_ttl_hours={value} out of range 1-720; using {expected}")
        return None
    if name == "hotkey" and not value.strip():
        warnings.append("hotkey is empty; using the default")
        return None
    return value


def _read_toml(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        warnings.append(f"could not parse {path.name}: {type(exc).__name__}")
        return {}


def _apply(
    config: VoxpressConfig,
    payload: Mapping[str, Any],
    origin: str,
    warnings: list[str],
) -> None:
    for key, raw in payload.items():
        if key not in _FIELD_NAMES:
            warnings.append(f"{origin}: unknown setting {key!r} ignored")
            continue
        value = _coerce(key, raw, warnings)
        if value is not None:
            setattr(config, key, value)


def load_config(*, config_path: Path | None = None) -> VoxpressConfig:
    """Load defaults, ``~/.voxpress.toml``, then ``VOXPRESS_*`` overrides."""

    config = VoxpressConfig()
    warnings: list[str] = []
    sources = ["defaults"]
    target = CONFIG_PATH if config_path is None else config_path

    payload = _read_toml(target, warnings)
    if payload:
        payload.pop("schema_version", None)
        _apply(config, payload, target.name, warnings)
        sources.append(str(target))

    env_payload: dict[str, str] = {}
    for name in _FIELD_NAMES:
        env_name = f"VOXPRESS_{name.upper()}"
        if env_name in os.environ:
            env_payload[name] = os.environ[env_name]
    if env_payload:
        _apply(config, env_payload, "environment", warnings)
        sources.append("environment")

    if config.device == "auto":
        config.device = _detect_device()
    if config.compute_type == "auto":
        config.compute_type = "float16" if config.device == "cuda" else "int8"

    config.schema_version = CONFIG_SCHEMA_VERSION
    config.warnings = warnings
    config.sources = sources
    return config


def _detect_device() -> str:
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def parse_hotkey(hotkey: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(main_key, modifiers)`` for strings such as ``alt+j``."""

    parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    if not parts:
        return "j", ("alt",)
    return parts[-1], tuple(parts[:-1])


OS_RESERVED_KEYS = frozenset(
    {
        "windows",
        "win",
        "cmd",
        "left windows",
        "right windows",
        "alt",
        "left alt",
        "right alt",
        "alt gr",
    }
)


def example_toml() -> str:
    return """# ~/.voxpress.toml — optional VoxPress settings
schema_version = 1

hotkey = "alt+j"
interaction_mode = "toggle"       # toggle / hybrid (tap toggle, hold push-to-talk)
hold_threshold_ms = 300

model = "large-v3"
device = "auto"                   # auto / cuda / cpu
compute_type = "auto"
language = "auto"
initial_prompt = ""
sample_rate = 16000

paste_method = "ctrl_v"           # ctrl_v / clipboard_only / typing
paste_delay_ms = 200
paste_modifier_timeout_ms = 2000
strict_focus_guard = false
auto_release_stuck_win = true

notify = true                      # status only; transcript text is never shown
prefix = ""                        # e.g. "🎤 "; empty = disabled
prefix_windows = ""                # case-insensitive title substrings; empty = all
temp_audio_ttl_hours = 24
"""
