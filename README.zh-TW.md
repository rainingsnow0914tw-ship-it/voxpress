# VoxPress

[![CI](https://github.com/rainingsnow0914tw-ship-it/voxpress/actions/workflows/ci.yml/badge.svg)](https://github.com/rainingsnow0914tw-ship-it/voxpress/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg)](https://github.com/rainingsnow0914tw-ship-it/voxpress)

> 按熱鍵、說話，用本機 Whisper 辨識，再把文字貼進目前的 Windows app。

VoxPress 是 MIT 授權的 Windows 系統列聽寫工具。每次只擁有一段錄音，使用 `faster-whisper` 在本機辨識，把結果放進剪貼簿，並且可以貼上但絕不按 Enter。繁體中文是第一級使用情境；模型、語言、prompt、熱鍵與貼上方式都可設定。

**[English](README.md)**

## 目前發布狀態

v0.2 維護線正在把日常使用中累積的修復整理成公開版本。目前只支援從 source 安裝。VoxPress **尚未發布到 PyPI**，現有 v0.1 GitHub Release **也沒有 exe 資產**。在未來 release 明確提供並驗證以前，請不要使用 `pip install voxpress`，也不要期待能下載 `.exe`。

## v0.2 重點

- 每顆實體鍵獨立配對 down/up，左右鍵不共用一個模糊狀態。
- 低階 hook 只分類事件與排入 queue；麥克風與 Whisper 不在 hook thread 執行。
- 預設維持「按一下開始、再按一下停止」；可選 tap-toggle／hold-to-talk 混合模式。
- 每次錄音擁有自己的 buffer、typed error、model reload lock、bounded worker queue。
- 等修飾鍵放開、拒絕已知敏感 Windows 畫面、永遠不按 Enter。
- `ctrl_v`、`typing`、`clipboard_only` 三種真正可運作的傳遞方式。
- typed TOML、`VOXPRESS_*` 覆蓋、未知鍵告警、v0.1 API 相容層。
- 輪替 log 與 tray 通知只寫 metadata，不顯示使用者說的正文。
- CI 使用人工合成事件／文字／音訊，不開真麥克風、不掛真實全域熱鍵。

## 資料流

```text
hotkey -> 配對 state machine -> bounded queue -> 錄音 session
                                           -> 本機 Whisper
                                           -> corrections / prefix
                                           -> 有 guard 的 clipboard / paste
```

預設 `Alt+J`：按一次開始，再按一次停止。設定 `interaction_mode = "hybrid"` 後，短按是 toggle、長按是按住講。

## 從 source 安裝

VoxPress 以 Windows、Python 3.10–3.13 為目標；v0.2 發佈前，公開 CI 的完整版本矩陣必須全部通過。

```powershell
git clone https://github.com/rainingsnow0914tw-ship-it/voxpress
cd voxpress
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
voxpress
```

NVIDIA CUDA 12：

```powershell
python -m pip install -e ".[gpu]"
```

第一次辨識會透過 model dependency 的正常 Hugging Face 路徑下載所選 Whisper 模型；之後沿用 cache。

## 使用

1. 執行 `voxpress`，Windows 系統列出現紫色圓點。
2. 按 `Alt+J`，變紅並開始錄音。
3. 說話，再按一次 `Alt+J`。
4. 變橘代表本機辨識中。
5. VoxPress 把結果放進剪貼簿，再嘗試設定的傳遞方式。

VoxPress 不會自行提權。一般權限程式通常無法把輸入注入管理員視窗；請用 `clipboard_only` 手動貼，不要為了這件事把聽寫工具長期用 Administrator 執行。

## 設定

建立 `%USERPROFILE%\.voxpress.toml`，每個欄位都可省略。

```toml
hotkey = "alt+j"
interaction_mode = "toggle"       # toggle / hybrid
hold_threshold_ms = 300

model = "large-v3"
device = "auto"                   # auto / cuda / cpu
compute_type = "auto"
language = "auto"
initial_prompt = ""
sample_rate = 16000

paste_method = "ctrl_v"           # ctrl_v / typing / clipboard_only
paste_delay_ms = 200
paste_modifier_timeout_ms = 2000
strict_focus_guard = false
auto_release_stuck_win = true

notify = true                      # 只顯示 metadata，不顯示辨識正文
prefix = ""
prefix_windows = ""                # 逗號分隔視窗標題；空白代表所有視窗
temp_audio_ttl_hours = 24
```

也可以用 `VOXPRESS_<欄位>` 覆蓋：

```powershell
$env:VOXPRESS_MODEL = "small"
$env:VOXPRESS_DEVICE = "cpu"
voxpress --check-config
```

`--check-config` 只印可公開 metadata，不會印 prompt、prefix 或本機個人化檔案路徑。

### 貼上模式

- `ctrl_v`：完整文字留在剪貼簿，只送 `Ctrl+V`。
- `typing`：完整文字仍留剪貼簿並逐字輸入；換行會攤平成空白，確保不會送 Enter。若輸入器可能已寫出一部分才失敗，VoxPress 不會再補貼整段 `Ctrl+V`，請用剪貼簿中的完整文字手動復原。
- `clipboard_only`：只複製，不送任何按鍵。

若修飾鍵仍按著、偵測到敏感目標、strict focus 已變更或注入失敗，文字會留在剪貼簿供手動復原。敏感目標偵測是 best-effort，不能辨認所有密碼欄。

## 個人詞庫與修正

可選檔案放在 `%USERPROFILE%\.voxpress\`：

- `vocab.json`：人工範例 `{"style_hint":"繁體中文","terms":["專案詞"]}`
- `corrections.json`：人工範例 `{"常見誤聽":"偏好文字"}`

內容不進 log、不放進 repo；編輯出錯時，執行中的程式保留上一份可用版本。

VoxPress 也能針對指定視窗，在文字前加上 `🎤 ` 這類可見標記。接收端看到的是普通 Unicode 文字，不是可信的語音 metadata；只有當接收的 AI 或工作流知道這個約定時，它才有「這是語音轉寫，請留意同音誤植」的作用。公開預設把 `prefix` 與 `prefix_windows` 留空，避免公開個人使用習慣與視窗名稱。

可先把這段規則貼給要使用的 AI：

> 當我的訊息開頭有 `🎤`，代表這句是語音辨識產生的文字；你沒有取得原始音訊。請先按讀音與上下文理解可能的同音錯字、標點錯亂與口語贅字。遇到沒見過的怪詞，先視為可能的辨識錯誤，不要逕自創造新名詞；若仍看不懂、前後文不順或有多種合理解讀，直接簡短問我，不要硬猜。例外：程式碼、網址，以及明確標為引用或複製的文字，必須照字面精確處理，不得擅改。

先把約定告訴各個接收 AI，再在本機選擇性開啟：

```toml
prefix = "🎤 "
prefix_windows = "Assistant A,Assistant B"
```

視窗篩選是不分大小寫的「標題子字串」，不是經驗證的 AI 身分。真實 AI／視窗名稱只放私人設定；若 `prefix` 非空而 `prefix_windows` 留空，標記會套用到所有目標視窗。

公開／私人邊界，以及未來「不用手改 JSON、但不盲目自動替換」的本機詞庫學習流程，見 [Personalization](docs/PERSONALIZATION.md)。

## 隱私與安全

VoxPress 沒有 analytics、帳號、遠端 log 或雲端辨識。音訊與辨識正文不進一般 log／tray 通知。暫存 WAV 在成功或失敗後刪除；意外中止留下的 VoxPress 專屬舊檔會在下次啟動按年齡清理。為了失敗復原，完整結果會刻意留在系統剪貼簿。

它仍然靠近敏感邊界：麥克風、全域鍵盤 hook、剪貼簿、前景視窗輸入注入、原生 audio/CUDA library 與模型下載。高風險環境使用前請讀 [隱私](docs/PRIVACY.md) 與 [安全政策](SECURITY.md)。

## 限制

- 只支援 Windows。
- 管理員、鎖定、credential、遊戲、sandbox 或非文字表面可能無法貼上，或會被刻意拒絕。
- 視窗標題／class 檢查不是萬能密碼欄偵測器。
- 全域 hook 與模擬輸入可能觸發防毒／EDR 審查。先核對 source 與 release checksum，不建議直接加整包白名單。
- 效能、模型大小與品質依模型、硬體、語言、音訊長度而變；本專案不宣稱通用速度或星等。

## 開發

```powershell
python -m pip install -e ".[dev]"
python -m pytest -m "not live" --strict-markers
ruff check .
ruff format --check .
```

另見 [貢獻指南](CONTRIBUTING.md)、[架構](docs/ARCHITECTURE.md)、[Personalization](docs/PERSONALIZATION.md)、[發布流程](docs/RELEASING.md)。

## 授權與來源

MIT，見 [LICENSE](LICENSE)。v0.2 的通用安全架構由 repo owner 從自己的私人維護環境整理而來；私人 TTS 流程、詞庫、transcript、設定、路徑與 runtime 產物全部排除。此公開 repo 內的檔案皆以本 repo 的 MIT license 發布。
