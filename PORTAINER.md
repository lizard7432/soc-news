# Portainer 部署（Linux / Docker Standalone）

使用 `compose.portainer.yaml` 在 Portainer 建立 Stack。此設定直接使用 image 內建的 app，不掛載主機程式目錄，不需要上傳 app 資料夾至 Docker 主機。適用 Linux / Docker Standalone，不是 Swarm 設定。

## 1. 準備映像

先確認 Portainer 所選 Docker 環境的 Images 中已有 `soc-news:local`。映像可在該環境建置，或在其他相容 CPU 架構的 Docker 主機建置後匯入：

```sh
# 在持有完整專案的建置主機執行
cd soc-news
docker build -t soc-news:local .
docker save -o soc-news-image.tar soc-news:local

# 在目標 Docker 主機匯入，或使用 Portainer 的 Images 匯入功能
docker load -i soc-news-image.tar
```

只有建置時需要完整原始碼；執行容器時不需要主機上的 app。Dockerfile 的 `COPY app ./app` 會將程式放入映像。若在 Portainer 透過 URL 建置，須提供可讀取的完整 Git 儲存庫或建置上下文，不能只提供 Dockerfile 單檔；私人 Git 儲存庫不能直接當作公開 URL 使用。

## 2. 建立 Stack

1. 在 Portainer 選擇目標 Docker Standalone 環境。
2. 開啟 Stacks → Add stack，名稱填入 `soc-news`。
3. 選擇 Web editor，貼上 `compose.portainer.yaml` 的完整內容。
4. 預設只綁定 `127.0.0.1:7879`，適合伺服器本機測試或本機反向代理。若同事需直接透過內網連線，在 Stack 環境變數設定 `BIND_ADDRESS` 為伺服器實際的內網 IP，並確認防火牆允許該連線。平台目前無登入，能連線的人可操作新聞與設定。
5. 按 Deploy the stack。`soc-news:local` 是主機本地映像，不要要求從遠端 registry 強制拉取。

若從舊版更新，請在原 Stack 編輯器以新版 YAML 替換，確定已刪除整段 `/opt/soc-news/app` bind mount，再更新部署。保留 Stack 名稱、既有環境變數與資料 volume。這次只改掛載設定，已有映像包含所需程式時不必重新 build。

## 3. 驗證

在 Portainer 查看容器 Logs，確認 Uvicorn 啟動且沒有程式載入錯誤。在伺服器本機測試：

```sh
curl http://127.0.0.1:7879/health
```

若綁定內網 IP，請改用 `http://伺服器內網IP:7879/health`。預期回傳 `{"ok":true}`，再開啟同位址首頁。健康端點通過不代表新聞來源或模型已驗證；首次模型下載後會開始蒐集，請另查看網頁蒐集狀態。

## 4. 日後更新

- 網頁上的來源、搜尋詞與排除詞：直接修改並保存，不必 build 或重啟。
- 修改 app 的 HTML、CSS、JavaScript、Python，或 requirements.txt / Dockerfile：重新建置 image 並在目標環境匯入或取得新版，再透過 Portainer 重建容器。僅 Restart 不會切換映像。使用本地映像時，不要啟用強制拉取映像選項。
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
