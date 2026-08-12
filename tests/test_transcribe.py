from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from voxpress.errors import SttError
from voxpress.transcribe import (
    _DLL_DIRECTORY_HANDLES,
    TEMP_AUDIO_PREFIX,
    WhisperEngine,
    cleanup_stale_audio_files,
    ensure_cuda_libs_on_path,
)


@dataclass
class _Segment:
    text: str


@dataclass
class _Info:
    language: str = "zh"
    duration: float = 1.0


class _Model:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.paths: list[Path] = []

    def transcribe(self, path: str, **_kwargs):
        audio_path = Path(path)
        assert audio_path.exists()
        assert audio_path.name.startswith(TEMP_AUDIO_PREFIX)
        self.paths.append(audio_path)
        if self.fail:
            raise RuntimeError("synthetic inference failure")
        return [_Segment("測試文字")], _Info()


def _engine(tmp_path: Path, model: _Model) -> WhisperEngine:
    return WhisperEngine(
        model_size="tiny",
        device="cpu",
        compute_type="int8",
        language="zh",
        model_factory=lambda *_args, **_kwargs: model,
        temp_dir=tmp_path,
    )


def test_temp_wav_is_deleted_after_success(tmp_path: Path) -> None:
    model = _Model()
    result = _engine(tmp_path, model).transcribe(b"synthetic wav bytes")
    assert result.text == "測試文字"
    assert model.paths and not model.paths[0].exists()
    assert list(tmp_path.glob(f"{TEMP_AUDIO_PREFIX}*.wav")) == []


def test_temp_wav_is_deleted_after_failure(tmp_path: Path) -> None:
    model = _Model(fail=True)
    with pytest.raises(SttError):
        _engine(tmp_path, model).transcribe(b"synthetic wav bytes")
    assert model.paths and not model.paths[0].exists()
    assert list(tmp_path.glob(f"{TEMP_AUDIO_PREFIX}*.wav")) == []


def test_stale_cleanup_is_prefix_scoped_and_age_bounded(tmp_path: Path) -> None:
    old = tmp_path / f"{TEMP_AUDIO_PREFIX}old.wav"
    recent = tmp_path / f"{TEMP_AUDIO_PREFIX}recent.wav"
    unrelated = tmp_path / "other-app.wav"
    for path in (old, recent, unrelated):
        path.write_bytes(b"synthetic")
    now = 10_000.0
    os.utime(old, (now - 500, now - 500))
    os.utime(recent, (now - 10, now - 10))
    os.utime(unrelated, (now - 500, now - 500))

    assert cleanup_stale_audio_files(tmp_path, older_than_seconds=100, now=now) == 1
    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_health_redacts_local_model_paths(tmp_path: Path) -> None:
    engine = WhisperEngine(
        model_size="C:/Users/example/private-model",
        device="cpu",
        model_factory=lambda *_args, **_kwargs: _Model(),
        temp_dir=tmp_path,
    )
    assert engine.health()["model"] == "[custom-model]"


def test_cuda_directory_handles_are_retained_and_deduplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    import sys
    from types import SimpleNamespace

    roots = {}
    for package in ("nvidia.cublas", "nvidia.cudnn"):
        root = tmp_path / package.replace(".", "-")
        (root / "bin").mkdir(parents=True)
        roots[package] = root

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda package: SimpleNamespace(submodule_search_locations=[str(roots[package])]),
    )
    handles: list[object] = []

    def add_directory(_path: str):
        handle = object()
        handles.append(handle)
        return handle

    monkeypatch.setattr(os, "add_dll_directory", add_directory, raising=False)
    monkeypatch.setenv("PATH", "")
    _DLL_DIRECTORY_HANDLES.clear()
    try:
        first = ensure_cuda_libs_on_path()
        second = ensure_cuda_libs_on_path()
        assert len(first) == 2
        assert second == []
        assert len(handles) == 2
        assert len(_DLL_DIRECTORY_HANDLES) == 2
        for path in first:
            assert os.environ["PATH"].casefold().count(path.casefold()) == 1
    finally:
        _DLL_DIRECTORY_HANDLES.clear()


def test_reload_waits_for_active_inference_and_next_call_reloads_once(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingModel(_Model):
        def transcribe(self, path: str, **kwargs):
            entered.set()
            release.wait(timeout=3.0)
            return super().transcribe(path, **kwargs)

    built: list[_Model] = []

    def factory(*_args, **_kwargs):
        model = BlockingModel() if not built else _Model()
        built.append(model)
        return model

    engine = WhisperEngine(
        model_size="tiny",
        device="cpu",
        model_factory=factory,
        temp_dir=tmp_path,
    )
    inference = threading.Thread(target=lambda: engine.transcribe(b"synthetic wav"))
    inference.start()
    assert entered.wait(timeout=1.0)

    reload_thread = threading.Thread(target=engine.request_reload)
    reload_thread.start()
    time.sleep(0.05)
    assert reload_thread.is_alive(), "reload replaced a model still in use"

    release.set()
    inference.join(timeout=2.0)
    reload_thread.join(timeout=2.0)
    assert not inference.is_alive()
    assert not reload_thread.is_alive()
    assert len(built) == 1

    assert engine.transcribe(b"synthetic wav").text == "測試文字"
    assert len(built) == 2
