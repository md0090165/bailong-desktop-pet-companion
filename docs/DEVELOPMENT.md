# 開發與建置

## 環境

Windows x64、Python 3.12，以及原作者提供的動畫素材。原始碼倉庫不附動畫與大型執行環境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item Character.example.md Character.md
```

請將合法取得的素材置於 `outputs/白龙桌宠素材/`，至少包含 `素材清单.json` 及其引用的動畫檔。檔名、播放校準及動作 ID 依素材清單，不建議手動猜測替換。

```powershell
.\.venv\Scripts\python.exe app\prepare_assets.py
.\.venv\Scripts\python.exe app\pet.py
```

## 建置可攜版

```powershell
.\.venv\Scripts\python.exe -m pip install PyInstaller==6.22.3
.\Build-BailongPet.ps1
```

輸出於 `release-work/gemini-drive/dist/BailongPet/`。Build 腳本建置主程式與通知助手，並複製素材與必要執行環境；若需要 Codex 額度功能，另須在本機配置自己的 Codex 或合法取得可攜版所需的 Codex runtime。建置輸出不是含私人記憶的日常使用資料夾。

## 測試

完整測試需要動畫資源。Gemini 測試使用合成金鑰與模擬 API，不消耗實際模型額度；加密測試使用 Windows DPAPI。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s app -p 'test_*.py'
```

主要模組：`chat_window.py` 管理聊天 UI；`gemini_connection.py` 管理服務切換與 Gemini；`character_context.py`、`long_memory.py`、`relationship.py` 管理角色資料；`event_controller.py` 管理桌面事件；`cloud_sync_core.py` 與 `google_drive_sync.py` 管理同步；`codex_monitor.py` 管理工作與額度通知。

## 發佈前

只發佈乾淨程式及設定範例。勿將日常使用資料夾直接整包加入 Git，亦勿加入個人 OAuth JSON、API Key、DPAPI 憑證、對話、記憶或桌面快照。`Character.md` 不追蹤；範例使用 `Character.example.md`。
