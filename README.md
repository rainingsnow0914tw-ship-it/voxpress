# VoxPress

[![CI](https://github.com/rainingsnow0914tw-ship-it/voxpress/actions/workflows/ci.yml/badge.svg)](https://github.com/rainingsnow0914tw-ship-it/voxpress/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg)](https://github.com/rainingsnow0914tw-ship-it/voxpress)

> Press a hotkey, talk, paste — anywhere on Windows.

A tiny system tray app that turns your voice into text using local Whisper, then pastes it into the focused app. No cloud, no telemetry, no account. Just press `Alt+J`, speak, release, and your words appear at the cursor.

**[繁體中文 README](README.zh-TW.md)**

![tray-states](docs/screenshots/states.png)

## Why VoxPress

- **Truly private** — Whisper runs locally on your machine. Audio never leaves the device.
- **Works in any app** — Browser, terminal, IDE, chat, Notion, even admin shells. If you can type there, VoxPress can paste there.
- **Traditional Chinese friendly** — Tested heavily on `zh-TW`. Default `large-v3` produces clean Traditional Chinese with proper punctuation. Mixed Chinese + English handled.
- **Tiny footprint** — Single Python process, ~80 MB without the model, no Electron, no web server.

## Why not [other tool]?

| Tool | License | Local / Cloud | zh-TW quality | OS | Price | Runtime |
| --- | --- | --- | --- | --- | --- | --- |
| **VoxPress** | **MIT** | **100% local** | **⭐⭐⭐⭐⭐ first-class** | Windows | **Free** | Single Python process, ~80 MB |
| SuperWhisper | Closed | Local | ⭐⭐⭐ | macOS only | $8.99 / mo | Native |
| Wispr Flow | Closed | Cloud | ⭐⭐⭐⭐ | Win / Mac | $12 / mo | Audio uploaded to their servers |
| WhisperWriter | GPL-3.0 | Local | ⭐⭐ | Win / Mac / Linux | Free | PyQt + Whisper |
| open-wispr | MIT | Local + Cloud | ⭐⭐ | Win / Mac / Linux | Free | Electron, ~200 MB |
| OpenWhispr | MIT | Local + Cloud | ⭐⭐ | Win / Mac / Linux | Free | Electron + Parakeet/Whisper |
| Whisper_SST | (unspec) | Local | ⭐⭐ | Win | Free | PyAutoGUI scripts |
| TypeWhisper | (unspec) | Local | ⭐⭐ | Win | Free | — |

**What makes VoxPress different**:

- **Traditional Chinese is the main use case, not an afterthought.** Built and tested daily with `zh-TW`. `large-v3` is the *default* model (not a tucked-away option). `initial_prompt` is a first-class config field for nudging Whisper toward Traditional characters and proper punctuation.
- **No Electron.** Single Python process, ~80 MB. Most open-source dictation tools ship 200+ MB Electron shells.
- **3 paste modes**, not just one. `ctrl_v` / `typing` / `clipboard_only` to handle apps that block clipboard paste (games, sandboxed UIs) or where you want manual control.
- **Configurable via TOML**, not hardcoded. Change hotkey / model / language / paste behaviour without editing source.
- **Truly local & private.** No telemetry, no account, no cloud round-trip. The only network activity is the one-time Whisper model download.

## How it works

```
press Alt+J  →  record mic  →  press Alt+J again  →  Whisper transcribes
                                                            ↓
                                              clipboard ← text
                                                            ↓
                                                      simulated Ctrl+V
                                                            ↓
                                                 pasted into focused app
```

## Install

### Option 1: pip (recommended for developers)

```powershell
pip install voxpress

# GPU users (NVIDIA + CUDA 12) — optional, much faster:
pip install voxpress[gpu]

# Run
voxpress
```

### Option 2: Standalone .exe (recommended for end users)

Download the latest `voxpress.exe` from [Releases](../../releases) and double-click. No Python required.

### Option 3: From source

```powershell
git clone https://github.com/rainingsnow0914tw-ship-it/voxpress
cd voxpress
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .
voxpress
```

## Usage

1. After install, run `voxpress` — a purple dot appears in your system tray.
2. Press **Alt+J** (default hotkey) — dot turns red, recording starts.
3. Speak.
4. Press **Alt+J** again — dot turns orange (transcribing), then back to purple. Text is pasted into whatever window has focus.

**First run takes longer**: Whisper downloads the model (`large-v3` is ~3 GB, cached at `%USERPROFILE%\.cache\huggingface\hub\`). Subsequent runs reuse it.

### Tray icon colors

| Color  | Meaning                            |
| ------ | ---------------------------------- |
| 🟣 Purple | Idle, press hotkey to start        |
| 🔴 Red    | Recording                          |
| 🟠 Orange | Transcribing                       |
| ⚫ Gray   | Error — see console for details    |

### Tray menu

Right-click the tray icon for:

- **Show config (console)** — Prints current settings
- **Reload model** — Forces model reload (useful after editing config)
- **Quit** — Exits cleanly

## Configuration

VoxPress looks for `~/.voxpress.toml` (i.e. `C:\Users\<you>\.voxpress.toml`). All fields are optional.

```toml
hotkey = "alt+j"                 # any keyboard combo; e.g. "ctrl+alt+v", "f9", "win+shift+space"
model = "large-v3"               # tiny / base / small / medium / large-v3 / large-v3-turbo
device = "auto"                  # auto / cuda / cpu
compute_type = "auto"            # auto / float16 / int8 / int8_float16
language = "auto"                # auto / zh / en / ja / ko / ... (ISO 639-1)
initial_prompt = ""              # nudge Whisper, e.g. "Please use traditional Chinese."
paste_method = "ctrl_v"          # ctrl_v / typing / clipboard_only
notify = true                    # tray balloon after each transcription
sample_rate = 16000
```

You can also override any field via environment variables:

```powershell
$env:VOXPRESS_HOTKEY = "ctrl+alt+v"
$env:VOXPRESS_MODEL  = "medium"
voxpress
```

### Model size vs. quality vs. speed

| Model           | VRAM  | Quality              | Speed (RTX 4090 / 4 sec clip) |
| --------------- | ----- | -------------------- | ----------------------------- |
| `tiny`          | ~1 GB | OK for English only  | <1 s                          |
| `base`          | ~1 GB | OK                   | ~1 s                          |
| `small`         | ~2 GB | Good                 | ~1 s                          |
| `medium`        | ~3 GB | Great                | ~2 s                          |
| `large-v3`      | ~3 GB | **Best, default**    | ~3 s                          |
| `large-v3-turbo`| ~3 GB | Almost large-v3      | ~2 s                          |

CPU is ~10× slower. `small` on CPU is usable for short clips.

### Paste methods

- `ctrl_v` (default) — Most reliable. Works everywhere a clipboard paste works.
- `typing` — Types character by character. Useful where Ctrl+V is blocked or transformed.
- `clipboard_only` — Just copies to clipboard, you paste manually. Useful for sensitive contexts.

## Troubleshooting

### Antivirus warning

VoxPress uses the `keyboard` library to register global hotkeys + the Windows `SendInput` API to simulate Ctrl+V. Some antivirus / EDR products flag this pattern as keylogger-like behavior.

**It is not a keylogger.** VoxPress only sends keystrokes (Ctrl+V); it does not capture anything you type. Source is open, audit freely. If your AV blocks it, add an exception for the `voxpress` install folder.

### Hotkey "doesn't work"

- Some browsers / IDEs grab certain Alt combos. If `Alt+J` doesn't fire, try `Ctrl+Alt+V`, `F9`, or `Win+Shift+Space`.
- On Windows, registering hotkeys may require running as Administrator if you also want VoxPress to work *inside* elevated apps (PowerShell admin, Task Manager, etc.).

### "CUDA library not found"

You're on the GPU path but missing the CUDA runtime DLLs. Either:

- `pip install voxpress[gpu]` to add `nvidia-cublas-cu12` + `nvidia-cudnn-cu12`, or
- Set `device = "cpu"` in `~/.voxpress.toml`.

### Whisper returns empty text

- Speak louder, closer to the mic.
- Check `sounddevice.query_devices()` to ensure the right input device is selected (VoxPress uses the system default).
- For Mandarin Chinese, set `initial_prompt = "請用繁體中文。"` to nudge the language.

### Pastes nothing but the clipboard has the text

`Ctrl+V` was blocked by the focused app (some games, some sandboxed UIs). Switch to `paste_method = "clipboard_only"` and paste manually.

## Privacy

VoxPress collects nothing. Sends nothing. Transcription happens entirely on your device via `faster-whisper`. The only network activity is the one-time download of the Whisper model from Hugging Face on first run.

## Build standalone .exe

```powershell
pip install voxpress[dev]
.\scripts\build_exe.ps1
# Output: dist\voxpress.exe
```

## License

MIT. See [LICENSE](LICENSE).

## Acknowledgements

- [openai/whisper](https://github.com/openai/whisper) for the model
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) for the fast inference runtime
- [boppreh/keyboard](https://github.com/boppreh/keyboard) for global hotkeys
- [moses-palmer/pystray](https://github.com/moses-palmer/pystray) for the tray icon
