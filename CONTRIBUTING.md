# Contributing

VoxPress welcomes focused fixes and tests for Windows dictation reliability, privacy, accessibility, and Traditional Chinese workflows.

## Development setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Required checks

```powershell
python -m pytest -m "not live" --strict-markers
ruff check .
ruff format --check .
python -m build
```

The normal test suite must not register a real global hook, open the microphone, mutate the user's clipboard, load/download a Whisper model, or touch a live VoxPress installation. Mark any deliberate physical-device check as both `windows` and `live`; CI excludes `live` tests.

## Safety rules

- Keep the hook callback allocation-light and non-blocking.
- Preserve down/up disposition pairing and fail-open recovery.
- Never add Enter, Return, auto-submit, or automatic shell execution.
- Keep transcript, audio, clipboard, prompt, token, path, and window-title content out of logs and notifications.
- Use only synthetic text/audio/events in tests and bug reports.
- Do not commit personal dictionaries, `.voxpress.toml`, logs, model caches, temp WAVs, executables, or local paths.
- Treat changes to dependencies, Actions workflows, build scripts, keyboard permissions, and paste guards as security-sensitive.

## Pull request checklist

- Describe the user-visible behavior and failure being fixed.
- Add a regression test before or with the fix.
- State whether the change touches microphone, hook, clipboard, focus, filesystem, network, dependency, or release boundaries.
- Confirm that no private data or machine-specific path is present.
- Record the exact tests run and any Windows/manual checks still outstanding.
- Keep generated/model/build artifacts out of the pull request.

Please report vulnerabilities through [SECURITY.md](SECURITY.md), not a public issue with exploit details.
