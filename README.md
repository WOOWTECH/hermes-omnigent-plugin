# Hermes Omnigent Plugin

讓 Hermes 透過自己的 Plugins 頁安裝 Omnigent 連線插件，啟用後開啟配對面板。每次新安裝／重新啟用都重新要求 HTTPS Server、帳號與遮罩密碼；當下存在的 Hermes／Claude Code／Codex／Pi 都必須收到實際回覆，才標記 ready。不補裝缺少的引擎。

## 從 Hermes Web 安裝

1. 開啟 Plugins 頁，選擇 Install from Git。
2. 輸入 `WOOWTECH/hermes-omnigent-plugin`，勾選啟用。
3. 第一次安裝新 Dashboard API 後，Hermes 0.19 需要重載 Dashboard，再重新整理瀏覽器。這是該版本的插件 API 冷掛載限制；不要以 Gateway 重啟替代。
4. 配對面板自動出現。輸入你的 Server、帳號與遮罩密碼。
5. 等待依賴下載、主機連線與實際對話驗證；成功後從 Omnigent 頁選擇引擎與模型，打開 Omnigent。

公開 repo 不需要 GitHub token。CLI 也能安裝：

```bash
hermes plugins install WOOWTECH/hermes-omnigent-plugin --enable
```

首次新 API 掛載仍遵循 Dashboard 重載要求。Hermes 原生啟用由 `plugins.enabled` 管理，不以安裝當作登入同意。已掛載的 API 會即時遵守停用設定，重新啟用時配對核准與回條失效。

## 操作

- Hermes 的 Omnigent 頁：重新配對、取消連結、實際對話驗證、模型／模組清單與 Omnigent 連結。
- `/omnigent status`、`/omnigent catalog`、`/omnigent verify`、`/omnigent open`、`/omnigent setup`。
- 模型工具 `omnigent_status`、`omnigent_catalog`、`omnigent_verify`、`omnigent_open`。工具沒有密碼／帳號欄位；登入只能透過配對面板。

密碼提交後即清空，既不寫入對話、插件狀態、瀏覽器 storage，也不在 API 驗證錯誤裡回顯。Omnigent 官方 token store 放在此插件的私有資料目錄。

## 執行環境與依賴

Linux x64／arm64，Python >=3.11，Hermes Agent 插件 API 與 Dashboard SDK 1.1。開發以已部署的 Hermes 0.19 為合約基準。

Omnigent 固定下載 0.16.0，缺少 uv 時以私有 pip target 安裝 uv 0.12.10。Omnigent/Python/uv 放在 `$HERMES_HOME/omnigent-plugin`，保留現有工具版本。

原生對話需要 tmux >=3.3。已有則沿用；Debian 容器缺少時，使用容器現有已簽章 APT index 的 SHA256 從 Debian HTTPS 套件源下載，解包到插件私有目錄。函式庫搜尋只套用 tmux wrapper，不修改系統套件或其他引擎。沒有適用的套件索引或平台時，會回報需要的依賴。

Hermes 自身使用既有 provider、模型與 profile。Omnigent 官方 per-session home 會私有複用原有 `.env`／`auth.json`，不修改來源；配對密碼不放入這些資料。缺少訂閱／供應商登入的引擎會明確驗證失敗。

Omnigent0.16 的 Hermes 原生支援尚缺模型目錄／切換與 `HERMES_HOME` 路徑處理，本插件只在自己安裝的 Omnigent venv 補上這些分支。相容層要求精確0.16.0與原碼錨點，拒絕修改系統或其他插件的 Omnigent；原始碼私有備份與SHA256收據保存在插件狀態目錄。模型由 Hermes 官方 inventory 載入，只列出當前 provider 可用的識別碼；不把未登入供應商的模型混入。

## 部署驗收

在插件目錄執行：

```bash
python -m hermes_omnigent.cli status
python -m hermes_omnigent.cli acceptance
python -m hermes_omnigent.cli acceptance --chat
python -m hermes_omnigent.cli verify --engine hermes --model MODEL_FROM_CATALOG
```

預設 acceptance 不發送模型提示；`--chat` 才建立固定無工具測試對話。退出碼0表示所有已裝引擎都已通過、主機在線且模型／模組清單可用。對話完成後停止，歷史保留供查核。

取消連結清除此插件對指定server的本機登入，等待本機 host 離線。Omnigent 0.16 未提供公開 DELETE host API，註冊ID與歷史保留。重新配對要重新輸入登入資料。

## 開發驗證

```bash
python -m pip install omnigent==0.16.0
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
node --check dashboard/dist/index.js
```

Omnigent adapter 以 MIT 授權重用 [Pi Omnigent Plugin](https://github.com/WOOWTECH/pi-omnigent-plugin) v0.2.1，Hermes 配對及 Dashboard 為本專案功能。
