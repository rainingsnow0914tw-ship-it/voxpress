"""Audio lifecycle, error classification, personalization and paste safety.

Every test follows the non-live boundary documented in `CONTRIBUTING.md`: no
PortAudio device, microphone, real clipboard, global hook, or input injection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxpress.audio import AudioService, RecordingSession, concatenate
from voxpress.errors import (
    ErrorCode,
    SttError,
    classify_audio_error,
    classify_transcription_error,
)
from voxpress.paste import (
    PasteAdapter,
    PasteOutcome,
    WindowInfo,
)
from voxpress.personalize import (
    PersonalizationStore,
    apply_corrections,
    build_context,
    build_vocab_prompt,
    collapse_punctuation,
    compose_prompt,
)

# =====================================================================
# Audio stream lifecycle  (5.1)
# =====================================================================


class FakeStream:
    def __init__(self, *, fail_on_start=None, fail_on_stop=None, fail_on_close=None):
        self.started = False
        self.stopped = False
        self.closed = False
        self._fail_on_start = fail_on_start
        self._fail_on_stop = fail_on_stop
        self._fail_on_close = fail_on_close

    def start(self):
        if self._fail_on_start:
            raise self._fail_on_start
        self.started = True

    def stop(self):
        if self._fail_on_stop:
            raise self._fail_on_stop
        self.stopped = True

    def close(self):
        if self._fail_on_close:
            raise self._fail_on_close
        self.closed = True


def make_service(stream=None, construct_error=None, captured=None):
    made = captured if captured is not None else []

    def factory(**kwargs):
        if construct_error:
            raise construct_error
        s = stream or FakeStream()
        s.callback = kwargs["callback"]
        made.append(s)
        return s

    return AudioService(16000, stream_factory=factory), made


def test_normal_start_and_stop() -> None:
    service, made = make_service()
    session = service.start(generation=1)
    assert service.active is True
    assert isinstance(session, RecordingSession)

    stopped = service.stop()
    assert service.active is False
    assert stopped is not None and stopped.generation == 1
    assert made[0].stopped and made[0].closed


def test_constructor_failure_raises_typed_error_and_leaves_nothing_active() -> None:
    service, _ = make_service(construct_error=RuntimeError("Error opening InputStream"))
    with pytest.raises(SttError) as info:
        service.start(generation=1)
    assert info.value.code == ErrorCode.AUDIO_OPEN_FAILED
    assert service.active is False


def test_start_failure_after_construction_closes_the_stream() -> None:
    """The bug: the old code published the stream before start() succeeded."""
    stream = FakeStream(fail_on_start=RuntimeError("PaErrorCode -9999"))
    service, _ = make_service(stream=stream)

    with pytest.raises(SttError) as info:
        service.start(generation=1)

    assert info.value.code == ErrorCode.AUDIO_MME_HOST_ERROR
    assert stream.closed is True, "half-open stream leaked after a failed start"
    assert service.active is False


def test_stop_failure_still_attempts_close() -> None:
    stream = FakeStream(fail_on_stop=RuntimeError("stop exploded"))
    service, _ = make_service(stream=stream)
    service.start(generation=1)
    service.stop()
    assert stream.closed is True, "device stayed open because stop() raised"


def test_stop_is_idempotent() -> None:
    service, _ = make_service()
    service.start(generation=1)
    assert service.stop() is not None
    assert service.stop() is None
    assert service.stop() is None


def test_second_start_is_refused_deterministically() -> None:
    service, _ = make_service()
    service.start(generation=1)
    with pytest.raises(SttError) as info:
        service.start(generation=2)
    assert info.value.code == ErrorCode.AUDIO_ALREADY_RECORDING
    # And crucially the first recording is untouched.
    assert service.current_generation == 1


# =====================================================================
# Session buffers  (5.2)
# =====================================================================


def test_audio_callback_appends_to_the_right_session() -> None:
    service, made = make_service()
    service.start(generation=7)
    made[0].callback(_chunk(1), 1, None, None)
    made[0].callback(_chunk(2), 1, None, None)
    stopped = service.stop()
    assert stopped is not None and len(stopped.chunks) == 2


def test_late_callback_cannot_pollute_the_next_recording() -> None:
    """The exact hazard behind the shared `state["audio"]` list."""
    service, made = make_service()
    service.start(generation=1)
    first_stream = made[0]
    first_stream.callback(_chunk(1), 1, None, None)
    first = service.stop()
    assert first is not None and len(first.chunks) == 1

    service.start(generation=2)
    # The old stream's callback fires after its recording finished.
    first_stream.callback(_chunk(99), 1, None, None)

    second = service.stop()
    assert second is not None
    assert second.chunks == [], "a stale callback leaked into a newer recording"
    assert service.dropped_late_chunks == 1


def test_detached_buffer_is_frozen() -> None:
    session = RecordingSession(generation=1, sample_rate=16000)
    assert session.append("a") is True
    taken = session.detach()
    assert taken == ["a"]
    assert session.append("b") is False
    assert session.chunk_count == 0


def test_concatenate_handles_empty_and_zero_size() -> None:
    assert concatenate([]) is None


def _chunk(value: int):
    import numpy as np

    return np.full((4, 1), float(value), dtype="float32")


# =====================================================================
# Error classification
# =====================================================================


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Error querying device -1", ErrorCode.AUDIO_NO_DEVICE),
        ("Invalid sample rate", ErrorCode.AUDIO_INVALID_RATE),
        ("Device unavailable", ErrorCode.AUDIO_DEVICE_BUSY),
        ("Access denied to microphone", ErrorCode.AUDIO_PERMISSION_DENIED),
        (
            "Error opening InputStream: Unanticipated host error [PaErrorCode -9999]",
            ErrorCode.AUDIO_MME_HOST_ERROR,
        ),
        ("something entirely new", ErrorCode.AUDIO_OPEN_FAILED),
    ],
)
def test_audio_errors_are_classified(message: str, expected: str) -> None:
    assert classify_audio_error(RuntimeError(message)) == expected


def test_mme_9999_guidance_does_not_blame_memory() -> None:
    """MME -9999 is explicitly not treated as a RAM diagnosis.

    PortAudio's "Unanticipated host error" can be a device/backend failure;
    treating it as memory exhaustion sends the user closing programs for no reason.
    """
    error = SttError(ErrorCode.AUDIO_MME_HOST_ERROR, "PaErrorCode -9999")
    guidance = error.guidance
    assert "不代表記憶體不足" in guidance
    assert "獨占了麥克風" in guidance
    assert "取樣率" in guidance


def test_cuda_errors_are_classified() -> None:
    assert (
        classify_transcription_error(OSError("cublas64_12.dll not found"))
        == ErrorCode.STT_CUDA_UNAVAILABLE
    )


# =====================================================================
# Personalization
# =====================================================================


def test_vocab_prompt_matches_the_live_wrapper_shape() -> None:
    vocab = {"style_hint": "以下是繁體中文口語對話。", "terms": ["量子貓", "星河", "晨光"]}
    assert build_vocab_prompt(vocab) == "以下是繁體中文口語對話。量子貓、星河、晨光。"


def test_vocab_prompt_is_budget_limited() -> None:
    vocab = {"style_hint": "x" * 90, "terms": ["term"] * 50}
    assert len(build_vocab_prompt(vocab)) <= 100


def test_vocab_prompt_survives_wrong_types() -> None:
    assert build_vocab_prompt({"style_hint": 5, "terms": "not a list"}) == ""
    assert build_vocab_prompt({"terms": [None, 3, "ok"]}) == "ok。"


def test_context_takes_the_tail() -> None:
    assert build_context(["a" * 100], max_chars=10) == "a" * 10
    assert build_context([]) == ""


def test_compose_prompt_caps_total_length() -> None:
    assert len(compose_prompt("v" * 150, "c" * 150)) <= 200


def test_corrections_replace_and_skip_metadata() -> None:
    corrections = {"量子喵": "量子貓", "_comment": "ignored", "_note": "x"}
    assert apply_corrections("跟量子喵說", corrections) == "跟量子貓說"
    assert apply_corrections("_comment", corrections) == "_comment"


def test_punctuation_runs_collapse() -> None:
    assert collapse_punctuation("好。。。的，，，嗎？？？") == "好。的，嗎？"
    assert collapse_punctuation("等等......") == "等等..."


def test_store_hot_reloads_and_keeps_last_good_on_corruption(tmp_path: Path) -> None:
    vocab_path = tmp_path / "vocab.json"
    corr_path = tmp_path / "corrections.json"
    vocab_path.write_text('{"terms": ["量子貓"]}', encoding="utf-8")
    corr_path.write_text('{"量子喵": "量子貓"}', encoding="utf-8")

    logger = _Recorder()
    store = PersonalizationStore(vocab_path, corr_path, logger=logger)
    assert store.postprocess("跟量子喵說") == "跟量子貓說"

    # the user saves a broken file mid-edit.
    corr_path.write_text('{"量子喵": ', encoding="utf-8")
    import os

    os.utime(corr_path, (1, 1))

    assert store.postprocess("跟量子喵說") == "跟量子貓說", "lost corrections on bad JSON"
    assert "corrections_invalid" in logger.names()

    # And it recovers when she finishes saving.
    corr_path.write_text('{"量子喵": "量子貓", "晨星": "晨光"}', encoding="utf-8")
    os.utime(corr_path, (2, 2))
    assert store.postprocess("晨星好") == "晨光好"


def test_store_context_window_rolls(tmp_path: Path) -> None:
    store = PersonalizationStore(tmp_path / "v.json", tmp_path / "c.json")
    for word in ["一", "二", "三", "四"]:
        store.postprocess(word * 2)
    assert store.recent() == ("二二", "三三", "四四")


def test_store_missing_files_are_not_an_error(tmp_path: Path) -> None:
    store = PersonalizationStore(tmp_path / "none.json", tmp_path / "none2.json")
    assert store.prompt_for_next() == ""
    assert store.postprocess("原文") == "原文"
    assert store.health()["vocab_error"] is None


def test_health_reports_real_counts_before_any_transcription(tmp_path: Path) -> None:
    """health() must load, not read a cold lazy cache.

    Reporting "terms=0" for a dictionary that actually has 48 entries reads as
    "the user's personal word list was lost" — the worst possible false alarm for the
    one file in this system that cannot be regenerated.
    """
    vocab_path = tmp_path / "vocab.json"
    corr_path = tmp_path / "corrections.json"
    vocab_path.write_text('{"terms": ["量子貓", "星河", "晨光", "示例詞"]}', encoding="utf-8")
    corr_path.write_text('{"量子喵": "量子貓", "_note": "meta"}', encoding="utf-8")

    store = PersonalizationStore(vocab_path, corr_path)
    health = store.health()  # no transcription has happened yet

    assert health["vocab_terms"] == 4
    assert health["correction_entries"] == 1  # "_note" is metadata
    assert health["vocab_configured"] is True
    assert health["corrections_configured"] is True


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def _record(self, event, **fields):
        self.events.append((event, fields))

    info = warn = error = debug = diagnostic = _record

    def names(self):
        return [n for n, _ in self.events]


# =====================================================================
# Paste safety  (5.4)
# =====================================================================


class FakeClipboard:
    def __init__(self, fail=False):
        self.value = None
        self.fail = fail

    def copy(self, text):
        if self.fail:
            raise RuntimeError("clipboard locked")
        self.value = text


class FakeKeys:
    def __init__(self, fail=False):
        self.sent: list[str] = []
        self.fail = fail

    def send(self, combination):
        if self.fail:
            raise RuntimeError("SendInput failed")
        self.sent.append(combination)


class FakeWriter:
    def __init__(self, fail=False):
        self.written: list[tuple[str, float]] = []
        self.fail = fail

    def write(self, text, *, delay=0.01, cancelled=None):
        del cancelled
        if self.fail:
            raise RuntimeError("typing failed")
        self.written.append((text, delay))


def make_paste(
    *,
    modifiers_held=None,
    window=None,
    strict=False,
    keys=None,
    clipboard=None,
    writer=None,
):
    clip = clipboard or FakeClipboard()
    key_sender = keys or FakeKeys()
    ticks = [0.0]

    def clock():
        return ticks[0]

    def sleep(seconds):
        ticks[0] += seconds

    adapter = PasteAdapter(
        clipboard=clip,
        key_sender=key_sender,
        text_writer=writer or FakeWriter(),
        modifiers_held=modifiers_held or (lambda: False),
        window_probe=lambda: window or WindowInfo(handle=1, title="Notepad", window_class="Edit"),
        strict_focus=strict,
        sleep=sleep,
        clock=clock,
        modifier_timeout_ms=200,
    )
    return adapter, clip, key_sender


def test_normal_paste_sends_ctrl_v_and_nothing_else() -> None:
    adapter, clip, keys = make_paste()
    result = adapter.deliver("你好")
    assert result.outcome is PasteOutcome.PASTED
    assert clip.value == "你好"
    assert keys.sent == ["ctrl+v"]


def test_never_presses_enter() -> None:
    """The auto-paste-without-send contract, asserted rather than trusted."""
    adapter, _, keys = make_paste()
    adapter.deliver("句子")
    assert all("enter" not in combination.lower() for combination in keys.sent)
    assert all("return" not in combination.lower() for combination in keys.sent)

    source = Path(__file__).parents[1] / "src/voxpress/paste.py"
    body = source.read_text(encoding="utf-8").lower()
    assert 'send("enter")' not in body and "'enter'" not in body


def test_clipboard_only_mode_does_not_send_keys() -> None:
    adapter, clip, keys = make_paste()
    result = adapter.deliver("你好", method="clipboard_only")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert clip.value == "你好"
    assert keys.sent == []


def test_typing_mode_writes_text_without_enter() -> None:
    writer = FakeWriter()
    adapter, clip, keys = make_paste(writer=writer)
    result = adapter.deliver("第一行\n第二行", method="typing")
    assert result.outcome is PasteOutcome.PASTED
    assert clip.value == "第一行\n第二行"
    assert writer.written == [("第一行 第二行", 0.01)]
    assert keys.sent == []


def test_typing_failure_leaves_full_text_in_clipboard_without_fallback() -> None:
    adapter, clip, keys = make_paste(writer=FakeWriter(fail=True))
    result = adapter.deliver("可恢復文字", method="typing")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.PASTE_SEND_FAILED
    assert clip.value == "可恢復文字"
    assert keys.sent == []


def test_partial_typing_failure_never_duplicates_with_ctrl_v() -> None:
    class PartialWriter:
        def write(self, _text, *, delay=0.01, cancelled=None):
            del delay, cancelled
            raise RuntimeError("failed after an unknown number of characters")

    adapter, clip, keys = make_paste(writer=PartialWriter())
    result = adapter.deliver("完整逐字稿", method="typing")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert clip.value == "完整逐字稿"
    assert keys.sent == []


def test_typing_cancelled_mid_write_never_falls_back_to_ctrl_v() -> None:
    cancelled = [False]

    class CancellingWriter:
        def write(self, _text, *, delay=0.01, cancelled=None):
            del delay, cancelled
            raise InterruptedError("synthetic shutdown")

    writer = CancellingWriter()
    adapter, clip, keys = make_paste(writer=writer)

    def cancellation_probe() -> bool:
        if cancelled[0]:
            return True
        cancelled[0] = True
        return False

    result = adapter.deliver(
        "不要晚貼上",
        method="typing",
        cancelled=cancellation_probe,
    )
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert clip.value == "不要晚貼上"
    assert keys.sent == []


def test_unknown_method_fails_safe_to_clipboard() -> None:
    adapter, clip, keys = make_paste()
    result = adapter.deliver("保留", method="unknown")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.CONFIG_INVALID
    assert clip.value == "保留"
    assert keys.sent == []


def test_modifiers_still_held_defers_to_clipboard() -> None:
    """Sending Ctrl+V while Win is down would open clipboard history instead."""
    adapter, clip, keys = make_paste(modifiers_held=lambda: True)
    result = adapter.deliver("你好")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.PASTE_MODIFIERS_HELD
    assert keys.sent == []
    assert clip.value == "你好", "text must stay recoverable"
    assert result.text_is_recoverable is True


def test_modifier_release_during_the_wait_allows_the_paste() -> None:
    remaining = [3]

    def held():
        remaining[0] -= 1
        return remaining[0] > 0

    adapter, _, keys = make_paste(modifiers_held=held)
    result = adapter.deliver("你好")
    assert result.outcome is PasteOutcome.PASTED
    assert keys.sent == ["ctrl+v"]


def test_modifier_pressed_during_delay_blocks_injection() -> None:
    checks = iter((False, True))
    adapter, clip, keys = make_paste(modifiers_held=lambda: next(checks, True))
    result = adapter.deliver("仍可手動貼上")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.PASTE_MODIFIERS_HELD
    assert clip.value == "仍可手動貼上"
    assert keys.sent == []


def test_sensitive_focus_entered_during_delay_blocks_injection() -> None:
    clip = FakeClipboard()
    keys = FakeKeys()
    current = [WindowInfo(handle=1, title="Notepad", window_class="Edit")]

    def sleep(_seconds: float) -> None:
        current[0] = WindowInfo(
            handle=9,
            title="User Account Control",
            window_class="#32770",
        )

    adapter = PasteAdapter(
        clipboard=clip,
        key_sender=keys,
        window_probe=lambda: current[0],
        sleep=sleep,
    )
    result = adapter.deliver("不要注入")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.PASTE_SENSITIVE_TARGET
    assert keys.sent == []


def test_strict_focus_change_during_delay_blocks_injection() -> None:
    clip = FakeClipboard()
    keys = FakeKeys()
    original = WindowInfo(handle=1, title="Editor", window_class="Edit")
    current = [original]

    def sleep(_seconds: float) -> None:
        current[0] = WindowInfo(handle=2, title="Other editor", window_class="Edit")

    adapter = PasteAdapter(
        clipboard=clip,
        key_sender=keys,
        window_probe=lambda: current[0],
        strict_focus=True,
        sleep=sleep,
    )
    result = adapter.deliver("不要貼錯視窗", target=original)
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.PASTE_FOCUS_CHANGED
    assert keys.sent == []


def test_intended_text_is_restored_to_clipboard_immediately_before_paste() -> None:
    clip = FakeClipboard()
    keys = FakeKeys()

    def sleep(_seconds: float) -> None:
        clip.value = "unrelated clipboard change"

    adapter = PasteAdapter(clipboard=clip, key_sender=keys, sleep=sleep)
    result = adapter.deliver("intended transcription")
    assert result.outcome is PasteOutcome.PASTED
    assert clip.value == "intended transcription"
    assert keys.sent == ["ctrl+v"]


def test_shutdown_during_delay_never_injects() -> None:
    cancelled = [False]
    clip = FakeClipboard()
    keys = FakeKeys()

    def sleep(_seconds: float) -> None:
        cancelled[0] = True

    adapter = PasteAdapter(clipboard=clip, key_sender=keys, sleep=sleep)
    result = adapter.deliver("private transcript", cancelled=lambda: cancelled[0])
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert clip.value == "private transcript"
    assert keys.sent == []


def test_already_cancelled_delivery_does_not_touch_clipboard() -> None:
    clip = FakeClipboard()
    clip.value = "existing clipboard"
    keys = FakeKeys()
    adapter = PasteAdapter(clipboard=clip, key_sender=keys)
    result = adapter.deliver("private transcript", cancelled=lambda: True)
    assert result.outcome is PasteOutcome.FAILED
    assert result.text_is_recoverable is False
    assert clip.value == "existing clipboard"
    assert keys.sent == []


def test_sensitive_target_is_refused() -> None:
    uac = WindowInfo(handle=9, title="User Account Control", window_class="#32770")
    adapter, clip, keys = make_paste(window=uac)
    result = adapter.deliver("我的密碼是什麼")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.PASTE_SENSITIVE_TARGET
    assert keys.sent == []


def test_credential_host_is_refused() -> None:
    cred = WindowInfo(handle=9, title="", window_class="Credential Dialog Xaml Host")
    adapter, _, keys = make_paste(window=cred)
    assert adapter.deliver("秘密").error_code == ErrorCode.PASTE_SENSITIVE_TARGET
    assert keys.sent == []


def test_focus_change_pastes_by_default_but_blocks_in_strict_mode() -> None:
    """A brief focus flicker is common; strict mode can refuse it."""
    started_in = WindowInfo(handle=1, title="Chat", window_class="Edit")
    now_in = WindowInfo(handle=2, title="Editor", window_class="Edit")

    adapter, _, keys = make_paste(window=now_in)
    assert adapter.deliver("你好", target=started_in).outcome is PasteOutcome.PASTED

    strict, _, strict_keys = make_paste(window=now_in, strict=True)
    result = strict.deliver("你好", target=started_in)
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.PASTE_FOCUS_CHANGED
    assert strict_keys.sent == []


def test_clipboard_failure_is_reported_as_failed() -> None:
    adapter, _, keys = make_paste(clipboard=FakeClipboard(fail=True))
    result = adapter.deliver("你好")
    assert result.outcome is PasteOutcome.FAILED
    assert result.error_code == ErrorCode.PASTE_CLIPBOARD_FAILED
    assert result.text_is_recoverable is False
    assert keys.sent == []


def test_send_failure_leaves_text_recoverable() -> None:
    adapter, clip, _ = make_paste(keys=FakeKeys(fail=True))
    result = adapter.deliver("你好")
    assert result.outcome is PasteOutcome.CLIPBOARD_ONLY
    assert result.error_code == ErrorCode.PASTE_SEND_FAILED
    assert clip.value == "你好"


def test_empty_text_is_never_pasted() -> None:
    adapter, _, keys = make_paste()
    assert adapter.deliver("").outcome is PasteOutcome.FAILED
    assert keys.sent == []
