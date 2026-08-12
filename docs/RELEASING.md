# Releasing

No v0.2 release is complete until the public artifacts and their claims match.

## Gate

1. Start from a clean checkout of the intended commit.
2. Confirm version parity in `pyproject.toml`, `voxpress.__version__`, changelog, and tag.
3. Run non-live pytest on supported Python versions and blocking Ruff checks.
4. Run source/private-data scans for logs, recordings, transcripts, prompts, tokens, personal names, and absolute paths.
5. Review dependency licenses, update diff, native binaries, and reachable vulnerabilities.
6. Build wheel and source distribution; install the wheel with dependencies in a clean Windows environment.
7. Build the Windows executable from the same commit.
8. Verify a clean-machine start, config validation, toggle dictation, clipboard recovery, and graceful exit.
9. Generate SHA-256 checksums for uploaded artifacts.
10. Publish release notes with exact tests, known limitations, supported Python/Windows versions, and signing status.

## Executable

Run:

```powershell
python -m pip install -e ".[dev]"
.\scripts\build_exe.ps1
```

The script must operate only on the repository's own `build` and `dist` directories and produce `dist\voxpress.exe` plus its checksum without deleting wheel or source artifacts already in `dist`. If the executable is unsigned, say so prominently; do not tell users to bypass antivirus warnings without verifying source and checksum.

## PyPI

Do not restore `pip install voxpress` documentation until the exact project is published by the maintainer and a clean virtual environment has installed and run that artifact. A local wheel is not evidence of a PyPI release.

## GitHub Release

Attach the verified executable, checksum file, wheel/source artifacts as intended, and link the commit/tag. Confirm the visible uploaded assets after publishing; a tag or empty release page is not a downloadable build.
