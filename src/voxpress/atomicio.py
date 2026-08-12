"""Crash-safe reads and writes for small user-scoped state files.

Temporary files are created beside their destination so ``os.replace`` keeps
readers on either the old complete value or the new complete value.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_ENCODING = "utf-8"


def atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* so that readers only ever see old or new content.

    ``os.replace`` is atomic on Windows for same-volume renames, which is why
    the temporary file is created in the destination directory rather than in
    the system temp dir.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=_ENCODING, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning *default* for missing, unreadable or corrupt files.

    Callers that need to distinguish "absent" from "corrupt" should use
    :func:`read_json_checked`.
    """
    ok, value, _ = read_json_checked(path, default)
    return value if ok or value is not None else default


def read_json_checked(path: Path, default: Any = None) -> tuple[bool, Any, str | None]:
    """Return ``(ok, value, error_code)``.

    ``error_code`` is ``None`` on success, ``"absent"`` when the file does not
    exist, ``"unreadable"`` for OS errors and ``"corrupt"`` for invalid JSON.
    Distinguishing these matters because a corrupt user dictionary must be
    reported loudly while an absent one is simply a first run.
    """
    try:
        raw = path.read_text(encoding=_ENCODING)
    except FileNotFoundError:
        return False, default, "absent"
    except OSError:
        return False, default, "unreadable"
    try:
        return True, json.loads(raw), None
    except (ValueError, UnicodeDecodeError):
        return False, default, "corrupt"
