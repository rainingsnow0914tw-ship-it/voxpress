# Security policy

## Supported code

Security fixes target the latest code on `main` and the newest published release. The unreleased v0.2 line is under active hardening; v0.1 receives best-effort critical fixes only.

## Reporting a vulnerability

Do not publish real recordings, transcripts, clipboard contents, private configuration, credentials, local paths, or exploit details in a public issue.

Use the repository's **Report a vulnerability** option if it is visible under the Security tab. If private vulnerability reporting is unavailable, open a minimal issue asking the maintainer for a private contact channel and include no sensitive detail. State the affected version, the security boundary involved, and whether the report requires a Windows-specific reproduction.

## Security boundaries

VoxPress deliberately touches high-impact local resources:

- microphone audio;
- a global Windows keyboard hook;
- clipboard contents;
- simulated input into the focused window;
- native PortAudio, CTranslate2, CUDA, and packaging dependencies;
- model files downloaded by the Whisper dependency;
- third-party contributions and release workflows.

## Required invariants

- The low-level hook may classify events and enqueue bounded work only. It must not open audio, run inference, paste, update UI, or perform filesystem/network I/O.
- A physical key-up follows the disposition recorded for the matching physical key-down. Left/right keys do not share ambiguous pairing state.
- Hook faults and queue overflow fail open so normal keyboard input returns to Windows.
- VoxPress never presses Enter or submits dictated text.
- Paste waits for modifiers and refuses known credential, lock-screen, and UAC surfaces.
- Sensitive-target detection is best-effort; window class/title checks cannot identify every password field.
- Routine logs and tray notifications contain metadata only, never transcript or clipboard text.
- Temporary audio is prefix-scoped, deleted after normal success/failure, and stale crash remnants are removed only after a configured age.
- Personal prompts, vocabulary, corrections, config paths, recordings, and runtime state do not enter Git or synthetic fixtures.
- Release artifacts require clean-source tests, checksum generation, and an explicit statement when unsigned.

## Supply chain expectations

Dependency and workflow changes should be isolated, reviewable, and tested. New native binaries or model sources require license, origin, and reachability review. A dependency finding is not automatically exploitable; reports should identify whether the affected code path is reachable in VoxPress.

## Safe test data

Use generated key events, synthetic arrays, and invented phrases. Never turn a user's actual dictation, vocabulary, logs, or audio into a fixture.
