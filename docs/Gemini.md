# 白龍桌寵：Gemini 與 Google Drive 同步版

本版沿用 Google Drive 同步版，新增 Gemini 通道，並保留 ChatGPT、動畫、人設、長期記憶、關係心情、觀察日記、日常動畫與 Codex 通知。

## 第一次使用 Gemini

1. 到 [Google AI Studio](https://aistudio.google.com/apikey) 登入自己的 Google 帳號，建立 Gemini API Key。這是另一組設定，Google Drive 的 OAuth JSON 無法代替。
2. 右鍵白龍 →「參數設定」，將服務切換為「Gemini · 使用自己的 API Key」。
3. 按「設定 Gemini API Key」，貼上金鑰並儲存，關閉設定視窗。
4. 按「重新載入模型」，選擇有免費額度且支援圖片輸入的 Gemini Flash 或 Flash-Lite 模型，再傳送簡短訊息測試。
5. 若曾使用 ChatGPT，白龍會優先沿用當前帳號的紀錄。也可以從紀錄下拉選單選擇「Gemini · 個人白龍紀錄」建立獨立紀錄。

金鑰格式修正：支援新版授權金鑰中的句點及較長內容，不再使用舊版的 256 字元限制。請用 AI Studio 的複製功能取得完整金鑰，貼上金鑰本身，勿貼入網頁網址。儲存成功只表示已在本機加密保存；載入模型及傳送測試訊息才會由 Google 驗證金鑰與配額。

模型清單依 Google API 回傳，不代表所有模型均有免費額度。請先查看 [官方定價](https://ai.google.dev/gemini-api/docs/pricing) 與 AI Studio 的實際配額。若不想付費，請使用未啟用付費計費的專案及其可用免費模型；沒有可用配額時，程式會提示失敗，不會自行切換其他模型或服務。

## 記憶與跨電腦使用

- 切換服務會保留同一個白龍紀錄的人設、對話、記憶、關係及日記；兩家的模型選擇分開記住。
- Gemini 使用者不需要 ChatGPT 登入，也可以開啟「同步記憶 · Google Drive」。
- 換電腦時仍須設定 Gemini API Key；Google Drive 同步另須匯入相同的桌面 OAuth JSON，登入同一 Google 帳號，選取原有雲端白龍版本，再決定採用本機或雲端紀錄。
- API Key 使用 Windows DPAPI 加密保存在該電腦的 Private 資料夾，不會包含在對話、記憶、Google Drive 同步或乾淨發佈壓縮包。
- 同步功能仍以一次一台電腦為前提。切換服務或紀錄前，先完成目前同步作業。

## 隱私與限制

- Gemini 免費服務可能將輸入與輸出用於改善產品，並可能接受人工審閱。詳見 [Google 條款](https://ai.google.dev/gemini-api/terms)。
- 回覆時會傳送角色核心、相關記憶及對話；整理舊對話也會使用所選模型。
- 隨機事件會傳送通過本機敏感資訊檢查的桌面圖片；自動辨識無法保證找出所有敏感內容。可在「隨機事件與白龍的觀察日記」停用隨機事件，保留每分鐘日常動畫。
- 免費額度有每日及速率限制，亦受帳號、地區及模型影響；無法保證全天聊天皆免費或永久維持免費。
- 若顯示額度不足，請稍後重試或查看 AI Studio。若模型下架，重新載入模型再選擇。
- 原有五小時額度提醒對應 Codex／ChatGPT，並不是 Gemini 剩餘配額顯示。

## 驗證範圍

已完成模擬 API、圖片格式、人設傳遞、服務切換、模型偏好分離、Windows 加密儲存與既有功能回歸測試。實際 Gemini 授權、帳號免費配額、角色回覆品質及圖片事件，仍需使用者填入自己的 API Key 後驗收。
