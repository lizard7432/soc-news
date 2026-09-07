# Portainer 部署（Linux / Docker Standalone）

使用 `compose.portainer.yaml` 在 Portainer 建立 Stack。此檔使用預先建置的映像與主機絕對路徑，適合直接在主機更新程式碼。不是 Swarm 部署設定。

## 1. 準備專案與映像

將最新版完整專案放到**目標 Docker 主機**的 `/opt/soc-news`。必須包含 Dockerfile、requirements.txt 及完整的 app 資料夾；不要只上傳 YAML。若使用其他目錄，請同步修改 YAML 的 `source`。

在該主機執行：

```sh
cd /opt/soc-news
docker build -t soc-news:local .
```

映像需建置在 Portainer 所選環境的 Docker Engine，不能只建在另一台電腦。主機 app 目錄及檔案必須讓容器使用者 UID 10001 可讀取。

## 2. 建立 Stack

1. 在 Portainer 選擇目標 Docker Standalone 環境。
2. 開啟 Stacks → Add stack，名稱填入 `soc-news`。
3. 選擇 Web editor，貼上 `compose.portainer.yaml` 的完整內容。
4. 預設只綁定 `127.0.0.1:7879`，適合伺服器本機測試或本機反向代理。若同事需直接透過內網連線，在 Stack 環境變數設定 `BIND_ADDRESS` 為伺服器實際的內網 IP，並確認防火牆允許該連線。平台目前無登入，能連線的人可操作新聞與設定。
5. 按 Deploy the stack。`soc-news:local` 是主機本地映像，不要要求從遠端 registry 強制拉取。

此設定不會替主機建立缺少的 app 目錄。若目錄不存在，部署會報錯；若目錄已存在但為空或不完整，仍會遮住映像內的程式並造成啟動失敗。Build 不會回填主機 app 目錄。

## 3. 驗證

在 Portainer 查看容器 Logs，確認 Uvicorn 啟動且沒有程式載入錯誤。在伺服器本機測試：

```sh
curl http://127.0.0.1:7879/health
```

若綁定內網 IP，請改用 `http://伺服器內網IP:7879/health`。預期回傳 `{"ok":true}`，再開啟同位址首頁。健康端點通過不代表新聞來源或模型已驗證；首次模型下載後會開始蒐集，請另查看網頁蒐集狀態。

## 4. 日後更新

- 修改 `/opt/soc-news/app/index.html`：重新整理網頁。
- 修改 app 內 Python 程式：在 Portainer 重新啟動該容器。重啟會重新載入模型並觸發一次蒐集。
- 修改 requirements.txt 或 Dockerfile：重新執行 `docker build -t soc-news:local .`，再透過 Portainer 重建容器以使用新映像；僅 Restart 不會切換映像。使用本地映像，不要啟用強制拉取映像選項。
- 變更 Stack YAML：更新 Stack，僅 Restart 不會套用新的掛載或連接埠設定。

## 資料保留

`soc-data` 與 `soc-models` 仍為具名 volume，實際名稱通常帶有 Stack 名稱前綴。若已有部署，請先在 Portainer Volumes 核對原本名稱並備份 SQLite，再決定是否重用。不要假設換 Stack 名稱仍會使用同一份資料，也不要刪除原本 volume。

若要明確重用既有 volume，可將檔案底部宣告改為以下形式，將佔位名稱替換成實際名稱；`external: true` 要求該 volume 已存在：

```yaml
volumes:
  soc-data:
    external: true
    name: REPLACE_WITH_EXISTING_DATA_VOLUME
  soc-models:
    external: true
    name: REPLACE_WITH_EXISTING_MODELS_VOLUME
```

本部署檔已進行 YAML 與掛載設定檢查，但尚未在 Docker 或 Portainer 實際部署驗證。
