"""Small, transcript-free system tray interface."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

from voxpress.config import example_toml
from voxpress.paste import PasteOutcome

STATE_COLOURS = {
    "idle": (192, 132, 252, 255),
    "recording": (250, 82, 82, 255),
    "transcribing": (253, 197, 96, 255),
    "error": (130, 130, 130, 255),
}
STATE_TITLES = {
    "idle": "VoxPress — ready ({hotkey})",
    "recording": "VoxPress — recording",
    "transcribing": "VoxPress — transcribing",
    "error": "VoxPress — error (see log)",
}


def _make_image(rgba):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=rgba)
    return image


class VoxPressTray:
    def __init__(
        self,
        *,
        hotkey: str,
        config_path: Path,
        on_quit: Callable[[], None],
        status_text: Callable[[], str],
        logger=None,
    ) -> None:
        self._hotkey = hotkey
        self._config_path = config_path
        self._on_quit = on_quit
        self._status_text = status_text
        self._logger = logger
        self._icon = None
        self._state = "idle"
        self._lock = threading.Lock()

    def build(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem(lambda _item: self._status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open config", self._open_config),
            pystray.MenuItem("Quit", self._quit),
        )
        self._icon = pystray.Icon(
            "voxpress",
            _make_image(STATE_COLOURS["idle"]),
            STATE_TITLES["idle"].format(hotkey=self._hotkey),
            menu=menu,
        )
        return self._icon

    def run(self) -> None:  # pragma: no cover - blocking UI
        if self._icon is None:
            self.build()
        self._icon.run()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    def set_state(self, state: str) -> None:
        with self._lock:
            self._state = state
        if self._icon is None:
            return
        try:
            self._icon.icon = _make_image(STATE_COLOURS.get(state, STATE_COLOURS["idle"]))
            self._icon.title = STATE_TITLES.get(state, STATE_TITLES["idle"]).format(
                hotkey=self._hotkey
            )
        except Exception:
            pass

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def notify_ready(self, characters: int, outcome: PasteOutcome) -> None:
        """Notify with metadata only, never dictated text."""

        if self._icon is None or not hasattr(self._icon, "notify"):
            return
        try:
            self._icon.notify(
                f"Transcription ready: {characters} characters ({outcome.value}).",
                "VoxPress",
            )
        except Exception:
            pass

    def _open_config(self, _icon=None, _item=None) -> None:
        try:
            if not self._config_path.exists():
                self._config_path.write_text(example_toml(), encoding="utf-8")
            os.startfile(str(self._config_path))
        except Exception as exc:
            if self._logger:
                self._logger.error("open_config_failed", error=type(exc).__name__)

    def _quit(self, _icon=None, _item=None) -> None:
        self._on_quit()
