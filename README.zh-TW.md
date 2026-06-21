# VoxPress

[![CI](https://github.com/rainingsnow0914tw-ship-it/voxpress/actions/workflows/ci.yml/badge.svg)](https://github.com/rainingsnow0914tw-ship-it/voxpress/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg)](https://github.com/rainingsnow0914tw-ship-it/voxpress)

> 按熱鍵 → 講話 → 自動貼到游標處。

一個極小的 Windows 系統列工具：按 `Alt+J`、講話、再按一次、Whisper 在你電腦上跑、識別好的文字自動貼進當前 focus 視窗。零雲端、零追蹤、零帳號。

**[English README](README.md)**

![tray-states](docs/screenshots/states.png)

## 為什麼用 VoxPress

- **真的本地** ── Whisper 完全在你電腦上跑、音訊不上雲、不上傳。
- **任何 app 都能用** ── 瀏覽器、終端、IDE、聊天、Notion、甚至 admin PowerShell。只要那邊能 Ctrl+V、VoxPress 就能貼進去。
- **繁體中文友善** ── 主力測試 `zh-TW`、預設 `large-v3` 出來的繁中標點乾淨。中英混雜也吃。
- **體積小** ── 單一 Python process、不含模型 ~80 MB、沒有 Electron、沒有 web server。

## 為什麼不用其他工具？

| 工具 | License | 本地 / 雲端 | 繁中品質 | OS | 費用 | Runtime |
| --- | --- | --- | --- | --- | --- | --- |
| **VoxPress** | **MIT** | **100% 本地** | **⭐⭐⭐⭐⭐ 主力** | Windows | **免費** | 單一 Python process、~80 MB |
| SuperWhisper | 閉源 | 本地 | ⭐⭐⭐ | 只 macOS | $8.99 / 月 | 原生 |
| Wispr Flow | 閉源 | 雲端 | ⭐⭐⭐⭐ | Win / Mac | $12 / 月 | 音訊上傳到他們的 server |
| WhisperWriter | GPL-3.0 | 本地 | ⭐⭐ | Win / Mac / Linux | 免費 | PyQt + Whisper |
| open-wispr | MIT | 本地 + 雲 | ⭐⭐ | Win / Mac / Linux | 免費 | Electron、~200 MB |
| OpenWhispr | MIT | 本地 + 雲 | ⭐⭐ | Win / Mac / Linux | 免費 | Electron + Parakeet/Whisper |
| Whisper_SST | (未指定) | 本地 | ⭐⭐ | Win | 免費 | PyAutoGUI scripts |
| TypeWhisper | (未指定) | 本地 | ⭐⭐ | Win | 免費 | — |

**VoxPress 不一樣的地方**：

- **繁體中文是主軸、不是附帶**。每天用 `zh-TW` 測試。`large-v3` 是**預設**模型（不是藏在選項裡）。`initial_prompt` 是第一順位的設定欄、用來引導 Whisper 用繁體 + 正確標點。
- **沒有 Electron**。單一 Python process、~80 MB。多數開源聽寫工具都是 200+ MB Electron 殼。
- **3 種貼上模式**、不只一種。`ctrl_v` / `typing` / `clipboard_only` 應付擋剪貼簿的 app（遊戲、sandbox UI）或想手動控制的情境。
- **TOML 配置、不寫死**。改熱鍵 / 模型 / 語言 / 貼上行為不用改 source code。
- **真本地、真私密**。零追蹤、零帳號、零雲端往返。唯一的網路活動是第一次下載 Whisper 模型。

## 怎麼運作

```
按 Alt+J  →  錄音  →  再按一次 Alt+J  →  Whisper 識別
                                              ↓
                                         文字 → 剪貼簿
                                              ↓
                                          模擬 Ctrl+V
                                              ↓
                                       貼進當前 focus 視窗
```

## 安裝

### 方式 1：pip（推薦給工程師）

```powershell
pip install voxpress

# 有 NVIDIA GPU + CUDA 12 → 快很多：
pip install voxpress[gpu]

# 啟動
voxpress
```

### 方式 2：單檔 .exe（推薦給一般使用者）

去 [Releases](../../releases) 下載最新的 `voxpress.exe`、雙擊就跑。不用裝 Python。

### 方式 3：從原始碼

```powershell
git clone https://github.com/rainingsnow0914tw-ship-it/voxpress
cd voxpress
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .
voxpress
```

## 怎麼用

1. 跑 `voxpress`、右下角系統列出現一個紫色圓點。
2. 按 **Alt+J**（預設熱鍵）── 圓點變紅、開始錄音。
3. 講話。
4. 再按一次 **Alt+J** ── 圓點變橘（辨識中）、然後變回紫色、文字自動貼進當前 focus 視窗。

**第一次跑會比較慢**：Whisper 要下載模型（`large-v3` 約 3 GB、cache 在 `%USERPROFILE%\.cache\huggingface\hub\`）。之後重啟就直接用 cache、快很多。

### 系統列圖示顏色

| 顏色   | 意思                        |
| ------ | --------------------------- |
| 🟣 紫色 | Idle、按熱鍵就開始           |
| 🔴 紅色 | 錄音中                       |
| 🟠 橘色 | 辨識中                       |
| ⚫ 灰色 | 出錯了、看 console 訊息      |

### 系統列右鍵選單

- **Show config (console)** ── 印出當前設定
- **Reload model** ── 強制重載模型
- **Quit** ── 乾淨關閉

## 設定

VoxPress 找 `~/.voxpress.toml`（也就是 `C:\Users\你\.voxpress.toml`）。每個欄位都可選、沒設用預設。

```toml
hotkey = "alt+j"                 # 任何按鍵組合；例「ctrl+alt+v」、「f9」、「win+shift+space」
model = "large-v3"               # tiny / base / small / medium / large-v3 / large-v3-turbo
device = "auto"                  # auto / cuda / cpu
compute_type = "auto"            # auto / float16 / int8 / int8_float16
language = "auto"                # auto / zh / en / ja / ko 等
initial_prompt = ""              # 引導 Whisper、例：「請用繁體中文。」
paste_method = "ctrl_v"          # ctrl_v / typing / clipboard_only
notify = true                    # 結束後系統列氣泡通知
sample_rate = 16000
```

也可以用環境變數覆蓋：

```powershell
$env:VOXPRESS_HOTKEY = "ctrl+alt+v"
$env:VOXPRESS_MODEL  = "medium"
voxpress
```

### 模型大小 / 品質 / 速度

| 模型             | VRAM  | 品質           | 速度（4090 / 4 秒音訊）|
| ---------------- | ----- | -------------- | --------------------- |
| `tiny`           | ~1 GB | 只夠英文        | <1 秒                |
| `base`           | ~1 GB | 普通            | ~1 秒                |
| `small`          | ~2 GB | 不錯            | ~1 秒                |
| `medium`         | ~3 GB | 很好            | ~2 秒                |
| `large-v3`       | ~3 GB | **最佳、預設** | ~3 秒                |
| `large-v3-turbo` | ~3 GB | 接近 large-v3   | ~2 秒                |

CPU 慢約 10 倍。`small` 在 CPU 跑短句子還可以接受。

### 貼上模式

- `ctrl_v`（預設）── 最穩、模擬 Ctrl+V、任何能貼的地方都活。
- `typing` ── 一字一字打、用在 Ctrl+V 被擋的地方。
- `clipboard_only` ── 只 copy 到剪貼簿、你自己 Ctrl+V。用在敏感場景。

## 疑難排解

### 防毒軟體警告

VoxPress 用 `keyboard` 函式庫註冊全域熱鍵、用 Windows `SendInput` API 模擬 Ctrl+V。有些防毒 / EDR 會把這種模式當 keylogger 行為警告。

**它不是 keylogger。** VoxPress 只**送出**按鍵（Ctrl+V）、不記錄你打了什麼。Source code 全開、可自行 audit。如果 AV 擋了、在防毒軟體把 `voxpress` 安裝資料夾加白名單即可。

### 熱鍵按了沒反應

- 某些瀏覽器 / IDE 會吃掉特定 Alt 組合。Alt+J 不響就換 `Ctrl+Alt+V`、`F9`、`Win+Shift+Space`。
- 要在「以系統管理員身分執行」的 app 裡面（PowerShell admin、工作管理員等）也活、VoxPress 自己也要用 admin 跑。

### 「找不到 CUDA library」

走 GPU 路徑但缺 CUDA runtime DLL。兩條解：

- `pip install voxpress[gpu]` ── 自動補 `nvidia-cublas-cu12` + `nvidia-cudnn-cu12`
- 或在 `~/.voxpress.toml` 設 `device = "cpu"`

### Whisper 回空字串

- 講大聲一點、靠近麥克風。
- `sounddevice.query_devices()` 確認系統預設輸入裝置是對的（VoxPress 用系統預設）。
- 講中文設 `initial_prompt = "請用繁體中文。"` 強制繁中。

### 沒貼進去但剪貼簿有文字

`Ctrl+V` 被 focus 的 app 擋了（某些遊戲、某些 sandboxed UI）。改 `paste_method = "clipboard_only"`、手動 Ctrl+V。

## 隱私

VoxPress 不蒐集任何東西、不上傳任何東西。識別完全在你電腦上跑（`faster-whisper`）。**唯一的網路活動**是第一次跑時從 Hugging Face 下載 Whisper 模型。

## 包成 .exe

```powershell
pip install voxpress[dev]
.\scripts\build_exe.ps1
# 產出：dist\voxpress.exe
```

## License

MIT、詳見 [LICENSE](LICENSE)。

## 致謝

- [openai/whisper](https://github.com/openai/whisper) ── 模型
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) ── 快速推理 runtime
- [boppreh/keyboard](https://github.com/boppreh/keyboard) ── 全域熱鍵
- [moses-palmer/pystray](https://github.com/moses-palmer/pystray) ── 系統列圖示
