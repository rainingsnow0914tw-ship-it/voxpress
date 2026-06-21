"""Config loader: env vars → ~/.voxpress.toml → defaults."""
import os
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

CONFIG_PATH = Path.home() / ".voxpress.toml"

DEFAULTS = {
    "hotkey": "alt+j",
    "model": "large-v3",       # tiny / base / small / medium / large-v3 / large-v3-turbo
    "device": "auto",          # auto / cuda / cpu
    "compute_type": "auto",    # auto / float16 / int8 / int8_float16
    "language": "auto",        # auto / zh / en / ja / ko / ...
    "initial_prompt": "",      # guide Whisper（e.g. 用繁體中文）
    "paste_method": "ctrl_v",  # ctrl_v / typing / clipboard_only
    "notify": True,            # tray balloon after each transcription
    "sample_rate": 16000,
}


def load_config() -> dict:
    """env > toml > defaults。"""
    config = dict(DEFAULTS)

    # toml override
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("rb") as f:
                user = tomllib.load(f)
            config.update({k: v for k, v in user.items() if k in DEFAULTS})
        except Exception as e:
            print(f"[voxpress] failed to load {CONFIG_PATH}: {e}", file=sys.stderr)

    # env override (VOXPRESS_HOTKEY, VOXPRESS_MODEL, ...)
    for key in DEFAULTS:
        env_name = f"VOXPRESS_{key.upper()}"
        if env_name in os.environ:
            val = os.environ[env_name]
            # bool / int 轉型
            if isinstance(DEFAULTS[key], bool):
                config[key] = val.lower() in ("1", "true", "yes")
            elif isinstance(DEFAULTS[key], int):
                try: config[key] = int(val)
                except ValueError: pass
            else:
                config[key] = val

    # auto resolution for device / compute_type
    if config["device"] == "auto":
        config["device"] = _detect_device()
    if config["compute_type"] == "auto":
        config["compute_type"] = "float16" if config["device"] == "cuda" else "int8"

    return config


def _detect_device() -> str:
    """偵測 CUDA 可用、否則回 cpu。"""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def example_toml() -> str:
    """印給用戶看的範例 toml。"""
    return """# ~/.voxpress.toml — VoxPress 設定檔（可選、不存在就用預設）
hotkey = "alt+j"                 # 全 Windows 全域熱鍵
model = "large-v3"               # tiny / base / small / medium / large-v3 / large-v3-turbo
device = "auto"                  # auto / cuda / cpu
compute_type = "auto"            # auto / float16 / int8 / int8_float16
language = "auto"                # auto / zh / en / ja / ko / ...
initial_prompt = ""              # 引導 Whisper（例：用繁體中文）
paste_method = "ctrl_v"          # ctrl_v / typing / clipboard_only
notify = true                    # 結束後 tray 通知
sample_rate = 16000
"""
