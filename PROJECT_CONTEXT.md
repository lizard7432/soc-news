# 開發交接（2026-09-06）

## 架構
FastAPI + SQLite；app/main.py 為收集、排程、資料與API；app/source_policy.py 為來源、原文語言及日期驗證；app/dedup.py 為去重；app/relevance.py 為相關性篩選；app/index.html 為介面。

## 最新行為
每整點收集，使用者自行設定分享日期、班別、開始與結束時間。日期標題轉民國年。開始不含、結束包含，時區 Asia/Taipei。複製不改變資料；确认交班才標記已交班並隱藏。日期選項在頁首。無登入。

## 未完成與限制
- 正文語意／共同事實自動去重尚未實作。現行標題與摘要比對會漏掉同素材改寫；已有人工作業合併的案例。
- 相關性是啟發式關鍵字，金融排除可能誤判，需增加真實回歸案例。
- 來源頁面失敗或無法確認日期的文章自動暫緩；來源擷取不保證全覆蓋。
- 正式資料庫不在版本庫；換機的事件、合併、排除及交班紀錄需另行備份遷移。
- 本機測試62項通過；尚未實測 Docker build 與公司伺服器部署。
- 前端 CSS 有多輪覆寫，後續宜整理，但不可改回使用者已否決的日期/時間操作。

## 換機
安裝 Python 3.10+，建立虛擬環境，pip install -r requirements.txt；設定 DATA_DIR 與 MODEL_CACHE 後執行 uvicorn app.main:app。或按 README 使用 Docker Compose。正式部署使用持久 volume 並獨立備份 SQLite。

## ML 回饋入口整合（2026-09-09）

人工修正集中在 SOCNEWS「查看模型結果／選用修正」，經 outbox 傳至 ML。ML 管理台只讀回饋。每輪背景分析同步最多 20 筆舊分析的正文去重結果，依分析時間輪替；保留相關性及交班資格，不自動合併事件。

## 職責修正
SOCNEWS 先以資安規則與排除詞過濾，ML 僅查重（analysis_task=dedup）。交班資格不使用 ML relevance decision。舊紀錄保留，ML 停用時仍使用 SOCNEWS 篩選。
