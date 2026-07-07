"""VoxPress main — tray icon, hotkey, recording, transcribe, paste."""
import io
import sys
import time
import threading

import keyboard
import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw

from voxpress.config import load_config, CONFIG_PATH, example_toml
from voxpress.stt import WhisperEngine, make_wav_bytes

# 解 Windows cp950 print 卡死
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# === state ===

state = {
    "recording": False,
    "transcribing": False,
    "stream": None,
    "audio": [],
}

tray_icon = None
engine: WhisperEngine = None
config: dict = {}


# === icons ===

COLORS = {
    "idle": (192, 132, 252, 255),         # purple
    "recording": (250, 82, 82, 255),      # red
    "transcribing": (253, 197, 96, 255),  # orange
    "error": (130, 130, 130, 255),        # gray
}


def make_icon(rgba):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=rgba)
    return img


def set_state(name: str):
    if not tray_icon:
        return
    if name == "idle":
        tray_icon.icon = make_icon(COLORS["idle"])
        tray_icon.title = f"VoxPress (idle • press {config['hotkey']})"
    elif name == "recording":
        tray_icon.icon = make_icon(COLORS["recording"])
        tray_icon.title = "VoxPress (recording • press again to stop)"
    elif name == "transcribing":
        tray_icon.icon = make_icon(COLORS["transcribing"])
        tray_icon.title = "VoxPress (transcribing...)"
    elif name == "error":
        tray_icon.icon = make_icon(COLORS["error"])
        tray_icon.title = "VoxPress (error — see console)"


# === recording ===

def _audio_cb(indata, frames, t, status):
    if status:
        print(f"[rec] status: {status}")
    state["audio"].append(indata.copy())


def start_rec():
    state["audio"] = []
    try:
        state["stream"] = sd.InputStream(
            samplerate=config["sample_rate"],
            channels=1,
            dtype="float32",
            callback=_audio_cb,
        )
        state["stream"].start()
        state["recording"] = True
        set_state("recording")
        print("[rec] start")
    except Exception as e:
        print(f"[rec] start fail: {e}")
        set_state("error")
        time.sleep(2)
        set_state("idle")


def _transcribe_and_paste():
    """transcribe in background, paste via clipboard + Ctrl+V."""
    audio_chunks = state["audio"]
    state["audio"] = []
    if not audio_chunks:
        set_state("idle")
        state["transcribing"] = False
        return

    audio = np.concatenate(audio_chunks, axis=0)
    if audio.size == 0:
        set_state("idle")
        state["transcribing"] = False
        return

    wav_bytes = make_wav_bytes(audio[:, 0], sample_rate=config["sample_rate"])
    print(f"[stt] {len(wav_bytes)} bytes audio, transcribing...")

    try:
        result = engine.transcribe_wav(wav_bytes)
        text = (result.get("text") or "").strip()
        if not text:
            print("[stt] empty result")
            set_state("idle")
            return
        print(f"[stt] => {text[:120]}")
        _paste(text)
        if config.get("notify") and tray_icon and hasattr(tray_icon, "notify"):
            try:
                tray_icon.notify(text[:100], "VoxPress")
            except Exception:
                pass
    except Exception as e:
        print(f"[stt] error: {e}")
        set_state("error")
        time.sleep(2)
    finally:
        state["transcribing"] = False
        set_state("idle")


def _paste(text: str):
    method = config.get("paste_method", "ctrl_v")
    # 給對話框 focus 切換時間, 50ms 太短會撞到 focus 尚未就緒的空窗期
    delay = config.get("paste_delay_ms", 200) / 1000.0
    if method in ("ctrl_v", "clipboard_only"):
        pyperclip.copy(text)
    if method == "clipboard_only":
        return
    if method == "typing":
        try:
            keyboard.write(text, delay=0.01)
        except Exception as e:
            print(f"[paste] typing fail, fallback ctrl+v: {e}")
            pyperclip.copy(text)
            time.sleep(delay)
            keyboard.send("ctrl+v")
        return
    # ctrl_v
    time.sleep(delay)
    try:
        keyboard.send("ctrl+v")
    except Exception as e:
        print(f"[paste] ctrl+v fail (clipboard still has text, manual Ctrl+V): {e}")


def stop_rec():
    if state["stream"]:
        try:
            state["stream"].stop()
            state["stream"].close()
        except Exception as e:
            print(f"[rec] stop err: {e}")
        state["stream"] = None
    state["recording"] = False
    state["transcribing"] = True
    set_state("transcribing")
    print("[rec] stop, transcribing...")
    threading.Thread(target=_transcribe_and_paste, daemon=True).start()


def toggle():
    if state["transcribing"]:
        print("[hotkey] busy, ignored")
        return
    if state["recording"]:
        stop_rec()
    else:
        start_rec()


# === tray ===

def on_quit(icon, item):
    try:
        if state["stream"]:
            state["stream"].stop()
            state["stream"].close()
    except Exception:
        pass
    icon.stop()


def on_show_config(icon, item):
    print("---")
    print(f"Config file: {CONFIG_PATH}")
    print(f"Exists: {CONFIG_PATH.exists()}")
    print(f"Hotkey: {config['hotkey']}")
    print(f"Model: {config['model']}  Device: {config['device']}  Compute: {config['compute_type']}")
    print(f"Language: {config['language']}  Initial prompt: {config['initial_prompt']!r}")
    print(f"Paste: {config['paste_method']}")
    print("---")


def on_reload_model(icon, item):
    print("[main] forcing model reload on next transcribe")
    engine._model = None


# === main ===

def main():
    global tray_icon, engine, config

    config = load_config()
    print(f"[voxpress] config loaded: hotkey={config['hotkey']}, model={config['model']}, "
          f"device={config['device']}, compute={config['compute_type']}, lang={config['language']}")
    if not CONFIG_PATH.exists():
        print(f"[voxpress] no config file at {CONFIG_PATH}, using defaults")
        print(f"[voxpress] to customize, create {CONFIG_PATH} with:")
        print(example_toml())

    engine = WhisperEngine(
        model_size=config["model"],
        device=config["device"],
        compute_type=config["compute_type"],
        language=config["language"],
        initial_prompt=config["initial_prompt"],
    )

    tray_icon = pystray.Icon(
        "voxpress",
        make_icon(COLORS["idle"]),
        f"VoxPress (idle • press {config['hotkey']})",
        menu=pystray.Menu(
            pystray.MenuItem("Show config (console)", on_show_config),
            pystray.MenuItem("Reload model", on_reload_model),
            pystray.MenuItem("Quit", on_quit),
        ),
    )

    try:
        keyboard.add_hotkey(config["hotkey"], toggle)
    except Exception as e:
        print(f"[voxpress] failed to register hotkey '{config['hotkey']}': {e}")
        print("[voxpress] tip: try a different hotkey via ~/.voxpress.toml or VOXPRESS_HOTKEY env")
        sys.exit(1)

    print(f"[voxpress] ready — press {config['hotkey']} to record/stop")

    # preload model in background (省第一次延遲)
    threading.Thread(target=engine.load, daemon=True).start()

    tray_icon.run()


if __name__ == "__main__":
    main()
