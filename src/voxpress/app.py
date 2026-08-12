"""Composition root for the Windows VoxPress tray application."""

from __future__ import annotations

import signal
import sys
import threading

from voxpress import winkeys
from voxpress.audio import AudioService
from voxpress.config import (
    CONFIG_PATH,
    VoxpressConfig,
    load_config,
    parse_hotkey,
    safe_model_label,
)
from voxpress.hotkey_hook import HotkeyHook
from voxpress.hotkey_state import HotkeyConfig
from voxpress.instance import SingleInstance
from voxpress.paste import PasteAdapter
from voxpress.personalize import PersonalizationStore
from voxpress.safe_logging import setup_logging
from voxpress.transcribe import WhisperEngine, cleanup_stale_audio_files
from voxpress.tray import VoxPressTray
from voxpress.watchdog import StuckModifierWatchdog
from voxpress.worker import RecordingWorker

TOGGLE_ONLY_THRESHOLD_SEC = 365 * 24 * 60 * 60


def resolve_hotkey(config: VoxpressConfig, *, keyboard_module=None) -> HotkeyConfig:
    """Resolve a human-readable hotkey to scan codes without registering it."""

    keyboard = keyboard_module
    if keyboard is None:
        import keyboard as keyboard_module_import

        keyboard = keyboard_module_import

    main_key, modifiers = parse_hotkey(config.hotkey)
    main_scan_codes = frozenset(keyboard.key_to_scan_codes(main_key))
    modifier_scan_codes: set[int] = set()
    modifier_scan_code_groups: list[frozenset[int]] = []
    for modifier in modifiers:
        group = frozenset(keyboard.key_to_scan_codes(modifier))
        modifier_scan_code_groups.append(group)
        modifier_scan_codes.update(group)

    threshold = (
        config.hold_threshold_sec
        if config.interaction_mode == "hybrid"
        else TOGGLE_ONLY_THRESHOLD_SEC
    )
    return HotkeyConfig(
        main_scan_codes=main_scan_codes,
        modifier_scan_codes=frozenset(modifier_scan_codes),
        hold_threshold_sec=threshold,
        # The configured shortcut belongs to VoxPress while it is active. Its
        # main key must not leak into the focused application (for example,
        # default Alt+J must not type/trigger Alt+J there). The state machine
        # still forwards every modifier and pairs the main key's DOWN/UP.
        suppress_main_key=True,
        modifier_scan_code_groups=tuple(modifier_scan_code_groups),
    )


class VoxPressApplication:
    def __init__(self, config: VoxpressConfig, *, logger=None) -> None:
        self.config = config
        self.logger = logger or setup_logging()
        self.instance = SingleInstance()
        self.hook: HotkeyHook | None = None
        self.audio: AudioService | None = None
        self.engine: WhisperEngine | None = None
        self.worker: RecordingWorker | None = None
        self.watchdog: StuckModifierWatchdog | None = None
        self.tray: VoxPressTray | None = None
        self._shutdown = threading.Event()
        self._closed = False

    def acquire_instance(self) -> bool:
        return self.instance.acquire()

    def build(self) -> None:
        removed = cleanup_stale_audio_files(
            older_than_seconds=self.config.temp_audio_ttl_hours * 60 * 60
        )
        if removed:
            self.logger.info("stale_audio_removed", count=removed)

        personalization = PersonalizationStore(
            self.config.resolved_vocab_path(),
            self.config.resolved_corrections_path(),
            logger=self.logger,
        )
        self.audio = AudioService(self.config.sample_rate, logger=self.logger)
        self.engine = WhisperEngine(
            model_size=self.config.model,
            device=self.config.device,
            compute_type=self.config.compute_type,
            language=self.config.language,
            logger=self.logger,
        )
        self.hook = HotkeyHook(resolve_hotkey(self.config), logger=self.logger)
        paste = PasteAdapter(
            modifiers_held=self._modifiers_held,
            paste_delay_ms=self.config.paste_delay_ms,
            modifier_timeout_ms=self.config.paste_modifier_timeout_ms,
            strict_focus=self.config.strict_focus_guard,
            logger=self.logger,
            on_before_send=self.hook.begin_self_injection,
            on_after_send=self.hook.end_self_injection,
        )
        self.tray = VoxPressTray(
            hotkey=self.config.hotkey,
            config_path=CONFIG_PATH,
            on_quit=self.request_shutdown,
            status_text=self.status_line,
            logger=self.logger,
        )
        notify = self.tray.notify_ready if self.config.notify else None
        self.worker = RecordingWorker(
            hook=self.hook,
            audio=self.audio,
            engine=self.engine,
            paste=paste,
            text_processor=personalization,
            paste_method=self.config.paste_method,
            initial_prompt=self.config.initial_prompt,
            prefix=self.config.prefix,
            prefix_windows=self.config.prefix_windows,
            on_state_change=self.tray.set_state,
            on_transcription_ready=notify,
            logger=self.logger,
        )
        self.watchdog = StuckModifierWatchdog(
            self.hook,
            logger=self.logger,
            auto_release=self.config.auto_release_stuck_win,
        )

    def _modifiers_held(self) -> bool:
        return winkeys.async_modifier_snapshot().any_down

    def start(self) -> None:
        if not all((self.worker, self.hook, self.watchdog)):
            raise RuntimeError("application must be built before start")
        if self.config.warnings:
            self.logger.warn("config_warnings", count=len(self.config.warnings))
        self.logger.info(
            "starting",
            hotkey=self.config.hotkey,
            model=safe_model_label(self.config.model),
            device=self.config.device,
            method=self.config.paste_method,
        )
        self.worker.start()
        self.hook.install()
        self.watchdog.start()
        self.logger.info("ready", hotkey=self.config.hotkey)

    def request_shutdown(self) -> None:
        self._shutdown.set()
        if self.tray is not None:
            self.tray.stop()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.logger.info("shutdown_begin")
        try:
            if self.hook is not None:
                try:
                    self.hook.uninstall()
                finally:
                    self.hook.recover("shutdown")
            if self.watchdog is not None:
                self.watchdog.stop()
            if self.worker is not None:
                self.worker.stop()
            if self.audio is not None:
                self.audio.close()
            if self.tray is not None:
                self.tray.stop()
        finally:
            self.instance.release()
            self.logger.info("shutdown_complete")

    def status_line(self) -> str:
        if self.hook is None:
            return f"Not started | {self.config.hotkey}"
        state = self.hook.snapshot()["state"]
        return f"{state} | {self.config.hotkey}"


def run(*, console: bool = False, diagnostics: bool = False) -> int:
    if sys.platform != "win32":
        print("VoxPress currently supports Windows only.", file=sys.stderr)
        return 2

    config = load_config()
    logger = setup_logging(console=console, diagnostics=diagnostics)
    app = VoxPressApplication(config, logger=logger)
    if not app.acquire_instance():
        logger.warn("already_running")
        logger.close()
        return 3

    def handle_signal(_signum, _frame) -> None:
        app.request_shutdown()

    for value in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(value, handle_signal)
        except (ValueError, OSError):
            pass

    try:
        app.build()
        app.start()
        assert app.tray is not None
        app.tray.run()
        return 0
    except Exception as exc:
        logger.error("fatal", error=type(exc).__name__)
        return 1
    finally:
        app.shutdown()
        logger.close()
