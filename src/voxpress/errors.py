"""Stable error codes and plain-language recovery guidance.

PortAudio's MME backend reports ``-9999`` as "Unanticipated host error" for
several device, exclusivity, and format failures. It is not evidence of memory
exhaustion, so VoxPress keeps that case distinct instead of giving misleading
advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class ErrorCode:
    AUDIO_NO_DEVICE = "AUDIO_NO_DEVICE"
    AUDIO_DEVICE_BUSY = "AUDIO_DEVICE_BUSY"
    AUDIO_INVALID_RATE = "AUDIO_INVALID_RATE"
    AUDIO_MME_HOST_ERROR = "AUDIO_MME_HOST_ERROR"
    AUDIO_PERMISSION_DENIED = "AUDIO_PERMISSION_DENIED"
    AUDIO_OPEN_FAILED = "AUDIO_OPEN_FAILED"
    AUDIO_START_FAILED = "AUDIO_START_FAILED"
    AUDIO_ALREADY_RECORDING = "AUDIO_ALREADY_RECORDING"
    AUDIO_EMPTY = "AUDIO_EMPTY"

    STT_MODEL_LOAD_FAILED = "STT_MODEL_LOAD_FAILED"
    STT_CUDA_UNAVAILABLE = "STT_CUDA_UNAVAILABLE"
    STT_EMPTY_RESULT = "STT_EMPTY_RESULT"
    STT_FAILED = "STT_FAILED"

    PASTE_MODIFIERS_HELD = "PASTE_MODIFIERS_HELD"
    PASTE_SENSITIVE_TARGET = "PASTE_SENSITIVE_TARGET"
    PASTE_FOCUS_CHANGED = "PASTE_FOCUS_CHANGED"
    PASTE_CLIPBOARD_FAILED = "PASTE_CLIPBOARD_FAILED"
    PASTE_SEND_FAILED = "PASTE_SEND_FAILED"

    CONFIG_INVALID = "CONFIG_INVALID"
    DICT_INVALID = "DICT_INVALID"
    UNKNOWN = "UNKNOWN"


#: Plain-language guidance keyed by code for application status and recovery
#: surfaces — never a raw library traceback.
GUIDANCE: dict[str, str] = {
    ErrorCode.AUDIO_NO_DEVICE: (
        "找不到錄音裝置。檢查麥克風是否插著、Windows 音效設定裡是否被停用。"
    ),
    ErrorCode.AUDIO_DEVICE_BUSY: (
        "麥克風正被另一個程式獨占（常見：Copilot、Teams、瀏覽器分頁的通話）。關掉那個程式再試一次。"
    ),
    ErrorCode.AUDIO_INVALID_RATE: (
        "錄音裝置不接受目前的取樣率。到 Windows 音效設定把麥克風格式改成 "
        "16000 Hz 或 48000 Hz 單聲道。"
    ),
    ErrorCode.AUDIO_MME_HOST_ERROR: (
        "PortAudio 的 MME 後端回報 -9999。這個代碼**不代表記憶體不足**，"
        "雖然它的英文訊息長得像。實際上通常是三件事之一："
        "① 另一個程式獨占了麥克風；② 裝置的取樣率／聲道數不符；"
        "③ 裝置剛剛被拔掉或被停用。請依序檢查這三項，不要先去關程式清記憶體。"
    ),
    ErrorCode.AUDIO_PERMISSION_DENIED: (
        "Windows 沒有給這個程式麥克風權限。到「設定 → 隱私權 → 麥克風」開啟。"
    ),
    ErrorCode.AUDIO_EMPTY: "沒有錄到聲音。可能是按太快，或麥克風被靜音。",
    ErrorCode.STT_CUDA_UNAVAILABLE: (
        "CUDA 不可用，Whisper 無法用顯卡跑。檢查 nvidia-cublas / nvidia-cudnn "
        '套件是否還在 venv 裡。可暫時改用 device = "cpu"，但會明顯變慢。'
    ),
    ErrorCode.STT_MODEL_LOAD_FAILED: (
        "Whisper 模型載入失敗。第一次使用要下載模型，需要網路；"
        "之後失敗多半是模型快取被刪或磁碟空間不足。"
    ),
    ErrorCode.STT_EMPTY_RESULT: "有錄到音但辨識不出字。通常是太短、太小聲或只有環境音。",
    ErrorCode.PASTE_MODIFIERS_HELD: (
        "辨識好了但沒有貼上，因為 Ctrl 或 Win 還被按著（那時候送 Ctrl+V 會變成別的功能）。"
        "文字已經在剪貼簿，放開按鍵後自己按一次 Ctrl+V 就好。"
    ),
    ErrorCode.PASTE_SENSITIVE_TARGET: (
        "目前焦點在密碼欄、鎖定畫面或系統權限提示，為了安全沒有貼上。文字保留在剪貼簿。"
    ),
    ErrorCode.PASTE_FOCUS_CHANGED: "貼上前焦點換了視窗，文字保留在剪貼簿沒有亂貼。",
    ErrorCode.PASTE_CLIPBOARD_FAILED: "無法寫入剪貼簿，通常是另一個程式正鎖著剪貼簿，稍後再試。",
    ErrorCode.PASTE_SEND_FAILED: "Ctrl+V 送不出去，文字仍在剪貼簿，可手動貼上。",
    ErrorCode.CONFIG_INVALID: "設定檔有無法解析的值，已使用安全預設值啟動。",
    ErrorCode.DICT_INVALID: "詞庫或修正字典的 JSON 壞了，已沿用上一份可用的內容。",
}


@dataclass(frozen=True)
class SttError(Exception):
    code: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code

    @property
    def guidance(self) -> str:
        return GUIDANCE.get(self.code, "")


_MME_9999 = re.compile(r"-9999|unanticipated host error", re.IGNORECASE)
_INVALID_RATE = re.compile(r"invalid sample rate|-9997|incompatible.*sample rate", re.IGNORECASE)
_DEVICE_BUSY = re.compile(r"device unavailable|-9985|device is busy|exclusive", re.IGNORECASE)
_NO_DEVICE = re.compile(
    # "Error querying device -1" is what sounddevice raises when Windows has no
    # default input device at all — the single most common real-world variant.
    r"no default input|invalid device|-9996|no such device|device not found"
    r"|error querying device",
    re.IGNORECASE,
)
_PERMISSION = re.compile(r"access denied|permission|0x8985|not permitted", re.IGNORECASE)


def classify_audio_error(exc: BaseException) -> str:
    """Map a PortAudio / sounddevice exception onto a stable code.

    Order matters: the more specific patterns are tried before the MME
    catch-all, because MME frequently *reports* -9999 for conditions that have
    a precise cause we would rather name.
    """
    message = f"{type(exc).__name__}: {exc}"

    if _NO_DEVICE.search(message):
        return ErrorCode.AUDIO_NO_DEVICE
    if _INVALID_RATE.search(message):
        return ErrorCode.AUDIO_INVALID_RATE
    if _DEVICE_BUSY.search(message):
        return ErrorCode.AUDIO_DEVICE_BUSY
    if _PERMISSION.search(message):
        return ErrorCode.AUDIO_PERMISSION_DENIED
    if _MME_9999.search(message):
        return ErrorCode.AUDIO_MME_HOST_ERROR
    return ErrorCode.AUDIO_OPEN_FAILED


def classify_transcription_error(exc: BaseException) -> str:
    message = f"{type(exc).__name__}: {exc}".lower()
    if "cublas" in message or "cudnn" in message or "cuda" in message:
        return ErrorCode.STT_CUDA_UNAVAILABLE
    if "dll" in message or "could not locate" in message:
        return ErrorCode.STT_CUDA_UNAVAILABLE
    if "model" in message and ("load" in message or "download" in message):
        return ErrorCode.STT_MODEL_LOAD_FAILED
    return ErrorCode.STT_FAILED
