# VoxPress

[![CI](https://github.com/rainingsnow0914tw-ship-it/voxpress/actions/workflows/ci.yml/badge.svg)](https://github.com/rainingsnow0914tw-ship-it/voxpress/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg)](https://github.com/rainingsnow0914tw-ship-it/voxpress)

> Press a hotkey, speak, and paste local Whisper transcription into your current Windows app.

VoxPress is an MIT-licensed Windows tray dictation tool. It records one session at a time, runs `faster-whisper` locally, copies the result to the clipboard, and can paste it without pressing Enter. Traditional Chinese is a first-class use case, and model, language, prompt, hotkey, and paste behavior are configurable.

**[繁體中文說明](README.zh-TW.md)**

## Current release status

The v0.2 maintenance line is being prepared from recurring real-world use. Source installation is supported. VoxPress is **not currently published on PyPI**, and the existing v0.1 GitHub release has **no prebuilt executable asset**. Do not use `pip install voxpress` or expect an `.exe` until a future release explicitly provides and verifies those artifacts.

## What v0.2 adds

- Deterministic physical key down/up pairing, with left/right keys tracked separately.
- A low-level hook that only classifies events and enqueues work; audio and Whisper run off the hook thread.
- Toggle mode by default, plus optional hybrid tap-to-toggle / hold-to-talk behavior.
- Session-owned audio buffers, typed failures, model reload locking, and bounded worker queues.
- Paste guards that wait for modifiers, refuse known sensitive Windows surfaces, and never press Enter.
- Three actual delivery modes: `ctrl_v`, `typing`, and `clipboard_only`.
- Typed TOML configuration, `VOXPRESS_*` overrides, unknown-key warnings, and v0.1 compatibility.
- Metadata-only rotating logs and tray notifications that never include dictated text.
- Synthetic regression tests; CI never opens the real microphone or installs a real global hook.

## Data flow

```text
hotkey event -> paired state machine -> bounded command queue -> microphone session
                                                               -> local Whisper
                                                               -> corrections/prefix
                                                               -> guarded clipboard/paste
```

The default `Alt+J` workflow is press once to start and press again to stop. Set `interaction_mode = "hybrid"` for tap-to-toggle and hold-to-talk.

## Install from source

VoxPress targets Windows and Python 3.10–3.13. The complete public CI matrix must pass before v0.2 is released.

```powershell
git clone https://github.com/rainingsnow0914tw-ship-it/voxpress
cd voxpress
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
voxpress
```

For NVIDIA CUDA 12 support:

```powershell
python -m pip install -e ".[gpu]"
```

The first transcription downloads the selected Whisper model through the model dependency's normal Hugging Face path. Later runs reuse its cache.

### Tested hardware and performance scope

Performance depends on the model, compute type, cold or warm model state, audio length, CPU/GPU, memory, and Windows environment. The observations below are reference points, not minimum requirements or universal latency guarantees.

| System | Configuration | Observed result | Evidence level |
|---|---|---|---|
| Samsung 960XGL; Windows 11 Home 25H2 (build 26200.9168); Intel Core Ultra 9 185H; 64 GB RAM; RTX 4070 Laptop GPU with 8 GB VRAM | CUDA, `float16`, `large-v3-turbo` | Bounded live acceptance was smooth. Model loading took 5.606 s; 13.49 s and 9.83 s recordings transcribed in 816 ms and 683 ms. | Reproduced and recorded on 2026-08-12 |
| Lenovo system with an RTX 5090; CPU and RAM not recorded | GPU acceleration enabled; exact model and settings not recorded | A maintainer collaborator reported smooth interactive use. | Field observation, not an instrumented benchmark |
| The same Lenovo system with GPU acceleration disabled | CPU-only; CPU, model, compute type, cold/warm state, and audio duration not recorded | One utterance was reported to take roughly one minute. | Unverified field observation; not a general CPU benchmark |

A compatible NVIDIA GPU is strongly recommended for low-latency interactive dictation with a large model. CPU mode is supported, but large models on CPU are not recommended for interactive use. CPU-only users should start with `model = "small"`, `device = "cpu"`, and `compute_type = "int8"`, and should expect higher latency.

See [v0.2 readiness](docs/V0.2_READINESS.md) for the bounded acceptance record and remaining release gaps.

## Use

1. Run `voxpress`; a purple dot appears in the Windows system tray.
2. Press `Alt+J`; the dot turns red while recording.
3. Speak, then press `Alt+J` again.
4. The dot turns orange while local transcription runs.
5. VoxPress copies the result and attempts the configured delivery method.

VoxPress does not elevate itself. A normal user process generally cannot inject input into an administrator/elevated window; use `clipboard_only` and paste manually instead of running a dictation tool as Administrator.

## Configuration

Create `%USERPROFILE%\.voxpress.toml`. All fields are optional.

```toml
hotkey = "alt+j"
interaction_mode = "toggle"       # toggle / hybrid
hold_threshold_ms = 300            # used by hybrid mode

model = "large-v3"
device = "auto"                   # auto / cuda / cpu
compute_type = "auto"
language = "auto"                 # zh / en / ja / ko / auto / ...
initial_prompt = ""
sample_rate = 16000

paste_method = "ctrl_v"           # ctrl_v / typing / clipboard_only
paste_delay_ms = 200
paste_modifier_timeout_ms = 2000
strict_focus_guard = false
auto_release_stuck_win = true

notify = true                      # metadata only, never transcript text
prefix = ""                        # optional marker before dictated text
prefix_windows = ""                # comma-separated title filters; empty = all
temp_audio_ttl_hours = 24
```

Every field can also be overridden with `VOXPRESS_<FIELD>`, for example:

```powershell
$env:VOXPRESS_MODEL = "small"
$env:VOXPRESS_DEVICE = "cpu"
voxpress --check-config
```

`--check-config` prints only privacy-safe metadata. It omits prompts, prefixes, and local personalization paths.

### Paste behavior

- `ctrl_v`: place the full text in the clipboard and send only `Ctrl+V`.
- `typing`: retain the full text in the clipboard and type characters directly. Newlines are flattened to preserve the no-Enter contract. If the writer fails after an uncertain partial write, VoxPress does not add a full `Ctrl+V`; use the complete clipboard text for manual recovery.
- `clipboard_only`: copy the text and send no keys.

If modifiers remain held, a sensitive target is detected, strict focus changes, or injection fails, the text remains in the clipboard for manual recovery. Sensitive-target detection is best-effort and cannot identify every password field.

## Personal vocabulary and corrections

Optional user-scoped files live under `%USERPROFILE%\.voxpress\`:

- `vocab.json`: synthetic example shape `{"style_hint":"Traditional Chinese","terms":["project term"]}`
- `corrections.json`: synthetic example shape `{"common mishearing":"preferred text"}`

Their contents are not logged or included in this repository. Invalid edits keep the last known-good in-memory version.

VoxPress can also prepend a visible marker such as `🎤 ` to selected target windows. The receiving application sees it as ordinary Unicode text, not trusted voice metadata; it is useful only when the receiving AI or workflow is told what the marker means. Public defaults leave `prefix` and `prefix_windows` empty so local app names and habits remain private.

Opt in locally after teaching each receiving AI the convention:

```toml
prefix = "🎤 "
prefix_windows = "Assistant A,Assistant B"
```

Window filters are case-insensitive title substrings, not verified AI identities. Keep real assistant/window names in the private local config. If `prefix` is non-empty and `prefix_windows` is empty, the marker is applied to every target window.

See [Personalization](docs/PERSONALIZATION.md) for the public/private boundary and the planned local, confirmation-gated learning workflow. The goal is to eliminate hand-editing without blindly turning every model guess into a global replacement.

## Privacy and security

VoxPress has no analytics, account, remote logging, or cloud transcription. Audio and transcript content are not written to routine logs or tray notifications. Temporary WAV files are deleted after success or failure; old VoxPress-prefixed crash remnants are age-cleaned at startup. The complete clipboard result intentionally remains available for recovery.

The application still has a sensitive endpoint footprint: microphone access, a global keyboard hook, clipboard writes, focused-window input injection, native audio/CUDA libraries, and model downloads. Read [Privacy](docs/PRIVACY.md) and [Security](SECURITY.md) before deploying it in a high-risk environment.

## Limitations

- Windows only.
- Paste into elevated, locked, credential, game, sandboxed, or non-text surfaces may be blocked or intentionally refused.
- Window-title/class checks are best-effort, not a universal password-field detector.
- Global hook and simulated input behavior can trigger antivirus/EDR review. Verify source and release checksums; do not add a blanket antivirus exception.
- Performance and model size depend on model, hardware, language, and audio length; this project does not publish universal speed or quality ratings.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest -m "not live" --strict-markers
ruff check .
ruff format --check .
```

See [Contributing](CONTRIBUTING.md), [Architecture](docs/ARCHITECTURE.md), [Personalization](docs/PERSONALIZATION.md), and [Releasing](docs/RELEASING.md).

## License and provenance

MIT. See [LICENSE](LICENSE). The v0.2 generic safety architecture consolidates work performed by the repository owner in a private maintenance environment; private TTS workflows, vocabulary, transcripts, configuration, paths, and runtime artifacts are excluded. The public files in this repository are released under its MIT license.

## Acknowledgements

- [OpenAI Whisper](https://github.com/openai/whisper)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [keyboard](https://github.com/boppreh/keyboard)
- [pystray](https://github.com/moses-palmer/pystray)
