# Privacy

## Summary

VoxPress performs transcription locally and contains no analytics, account system, remote logger, or cloud-inference fallback. The first use of a model can make a network request through the model dependency to download model files. Dependency installation and updates also use the configured Python package index.

## Data lifecycle

| Data | Location | Lifetime | Network use |
| --- | --- | --- | --- |
| Microphone samples | memory owned by one recording session | detached at stop; released after processing | none |
| Temporary WAV | system temp, random `voxpress-audio-` prefix | deleted after success/failure; old crash remnants age-cleaned | none |
| Transcript | memory, then clipboard | memory is process-scoped; clipboard remains until replaced | none |
| Prompt/vocabulary/corrections | user-scoped config files | until the user edits/deletes them | passed only to local Whisper |
| Routine logs | `%LOCALAPPDATA%\VoxPress\logs` | 2 MiB active file plus 3 rotating backups | none |
| Whisper model | dependency-managed cache | until user removes it | downloaded on first use/update |
| Learning candidates (future) | user-scoped local learning directory | user-controlled; minimal evidence only | none in the core design |

## What logs contain

Allowed examples: event name, state, error code, character count, duration, queue length, scan code, paste outcome, and version. Sensitive string fields are redacted by default.

Logs and tray notifications must not contain transcription text, clipboard text, prompt content, personal vocabulary, credentials, local paths, or window titles.

## Clipboard tradeoff

VoxPress leaves the complete transcription in the system clipboard. This allows manual recovery when focus injection fails or is blocked. Other local applications or clipboard-history features may read or retain it. Use `clipboard_only` for deliberate manual control, disable clipboard history if appropriate, and avoid dictating secrets into an untrusted desktop session.

## Focus and sensitive fields

VoxPress refuses known credential, lock-screen, and UAC surfaces based on best-effort window metadata. Windows does not expose a universal, reliable password-field signal to this architecture, so the guard cannot promise to detect every sensitive field. Keep `strict_focus_guard = true` in environments where focus changes are high risk.

## Removing local data

Exit VoxPress first, then remove only the data you intend:

- config: `%USERPROFILE%\.voxpress.toml`
- vocabulary/corrections: `%USERPROFILE%\.voxpress\`
- metadata logs: `%LOCALAPPDATA%\VoxPress\logs\`
- model cache: use the cache-management guidance for the installed Whisper/Hugging Face dependency

Do not recursively delete a user profile or shared cache root merely to remove VoxPress data.

## Personalization learning boundary

The public project may contain schemas, validators, collision tests, and synthetic examples for automated vocabulary learning. It must not contain a real person's vocabulary, correction history, conversation excerpts, names, app-window allowlist, or confirmation records. A future host adapter must be opt-in and local by default; it should store the smallest confirmed phrase pair needed to reproduce a correction rather than a full conversation.
