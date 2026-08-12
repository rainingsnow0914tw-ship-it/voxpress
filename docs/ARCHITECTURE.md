# Architecture

## Product boundary

VoxPress is a local Windows speech-to-text and paste tool. It does not include assistant TTS, AI-session monitoring, agent control, cloud transcription, telemetry, or a personal voice-companion workflow.

## Data flow

```text
Windows key event
  -> HotkeyHook
  -> HotkeyStateMachine
  -> bounded Command queue
  -> RecordingWorker command thread
  -> AudioService session
  -> RecordingWorker transcription thread
  -> WhisperEngine
  -> PersonalizationStore
  -> PasteAdapter
  -> clipboard / focused window
```

## Ownership and threads

- `HotkeyHook` owns the OS hook and serializes state-machine calls. Its callback performs no audio, model, clipboard, tray, filesystem, or network work.
- `HotkeyStateMachine` is deterministic. It records each physical key-down disposition and reuses it for the matching key-up.
- `RecordingWorker` has one short command loop and one transcription loop. The command loop owns start/stop/cancel; the transcription loop owns model calls and paste delivery.
- `AudioService` owns exactly one `RecordingSession`. Stopping atomically detaches its chunks so late callbacks cannot modify an older job.
- `WhisperEngine` lazy-loads the model and uses separate load/use locks so reload cannot replace an in-flight model.
- `PasteAdapter` owns clipboard delivery and simulated input. It does not submit text.

## Hotkey safety

The core invariant is: the key-up disposition always matches the recorded disposition of the same physical key-down. It is not recomputed from current modifier state. Repeats reuse the first decision, left/right keys remain distinct, worker acknowledgements carry a generation, and failures pass input back to Windows.

The default public workflow is toggle mode. Hybrid mode uses an integer-nanosecond threshold: below the threshold latches toggle; at or above it ends push-to-talk on release.

## Paste safety

The transcription is written to the clipboard first for recoverability. Delivery then:

1. waits for modifiers to be physically released;
2. probes the current window best-effort;
3. rejects known sensitive surfaces;
4. optionally rejects a focus change;
5. performs only the configured paste or typing action;
6. never sends Enter.

The clipboard intentionally retains the full result. This is a documented recovery tradeoff, not a claim that clipboard contents are ephemeral.

## Configuration and private state

- Public config: `%USERPROFILE%\.voxpress.toml`
- Optional vocabulary/corrections: `%USERPROFILE%\.voxpress\`
- Rotating metadata logs: `%LOCALAPPDATA%\VoxPress\logs\`
- Model cache: managed by the Whisper/Hugging Face dependency
- Temp audio: unpredictable `voxpress-audio-*.wav` names in the system temp directory

Prompts, prefixes, personalization paths, audio, transcripts, and clipboard contents are excluded from safe config output and routine logs.

The optional visible voice marker is deliberately treated as presentation text, not an authentication or provenance signal. A receiving AI can interpret `🎤` only if the marker survives into its message and that workflow defines the convention. Target-window filters remain local configuration.

The future learning pipeline is separated from the real-time dictation path. Conversation adapters may write confirmed, minimal correction evidence to a user-scoped inbox; a local validator then classifies candidates before updating vocabulary or corrections. See [Personalization](PERSONALIZATION.md).

## Failure cleanup

- Partial audio creation closes acquired resources.
- Stop/cancel are idempotent and prioritize releasing the microphone.
- Hook shutdown uninstalls first, then synthesizes releases only for owned suppressed keys.
- Temp audio is deleted in `finally`; only old, prefix-scoped remnants are removed at startup.
- A Windows named mutex prevents a second instance in the same session.

## Compatibility

`voxpress.stt.WhisperEngine` preserves the v0.1 constructor and `transcribe_wav()` dictionary result. The v0.2 internal engine returns a typed `Transcription` object.
