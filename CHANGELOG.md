# Changelog

All notable changes are documented here. The project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and semantic versioning.

## [Unreleased]

### Added

- Deterministic hotkey state machine with per-key down/up pairing, left/right separation, generation IDs, bounded commands, injected-event handling, and fail-open recovery.
- Optional hybrid tap-to-toggle / hold-to-talk mode while retaining v0.1 toggle behavior by default.
- Session-owned audio buffers, typed audio/Whisper errors, safe model reload, and bounded two-thread worker pipeline.
- Modifier wait, focus policy, sensitive-window guard, and real `ctrl_v`, `typing`, and `clipboard_only` delivery modes.
- Typed configuration, `VOXPRESS_*` overrides, unknown-setting warnings, neutral prefix filters, and user-scoped vocabulary/corrections.
- Metadata-only rotating logs, transcript-free notifications, scoped temp-WAV cleanup, and a Windows named-mutex single-instance guard.
- Synthetic hotkey, audio, transcription, paste, config, privacy, compatibility, and end-to-end regression tests.
- Security, privacy, architecture, contributing, and release documentation.
- A documented public/private personalization boundary and future local lexicon-learning workflow.
- A documented opt-in `🎤` receiving-AI convention for phonetic error handling, clarification, exact code/URL preservation, and private window-scoped configuration.

### Changed

- Split the v0.1 monolithic runtime into owned components and moved all slow work off the low-level hook callback.
- Preserved the public v0.1 `WhisperEngine.transcribe_wav()` API through a compatibility layer.
- Replaced unsupported PyPI/executable and unverified size/speed/comparison claims with current source-install status.

### Security

- Removed transcript text from console/log paths and tray notifications.
- Added no-Enter enforcement, modifier and sensitive-target guards, metadata redaction, and stale audio cleanup.
- Corrected the native Win64 `SendInput` layout and scan-code-aware left/right Windows-key recovery.
- Consume the configured main hotkey, leave the clipboard untouched when shutdown already won, and avoid automatic `Ctrl+V` after an uncertain partial typing failure.
- Restrict public model labels to known built-in aliases so custom relative model names are redacted.

## [0.1.0] - 2026-06-22

### Added

- Initial Windows tray dictation prototype using local faster-whisper, configurable hotkey, and clipboard paste.

[Unreleased]: https://github.com/rainingsnow0914tw-ship-it/voxpress/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rainingsnow0914tw-ship-it/voxpress/releases/tag/v0.1.0
