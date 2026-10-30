# Hermes Omnigent Plugin

讓 Hermes 透過自己的 Plugins 頁安裝 Omnigent 連線插件，啟用後開啟配對面板。每次新安裝／重新啟用都重新要求 HTTPS Server、帳號與遮罩密碼；當下存在的 Hermes／Claude Code／Codex／Pi 都必須收到實際回覆，才標記 ready。不補裝缺少的引擎。

目前為 **v0.1.0-rc.14 候選版**，固定使用 Omnigent 0.19.1（與 PaaS 的 Omnigent 伺服器同版）。rc.14 在 Hermes 0.22.5 測試站由 rc.13 就地升級（私有 Omnigent 0.16.0 → 0.19.1，登入與直接執行同意保留），並通過 Hermes 真實對話；其餘驗收範圍沿用 rc.13：主站的 Plugins 設定頁、Omnigent 配對、模型選擇及 Sol／Luna 真實對話已驗收；Gateway 的一次直接 API 測試通過授權與路由，但命中測試後端，尚未證明真實供應商推論。第二測試站的本機模型完整對話仍待驗收；詳見 [驗收紀錄](docs/commercial-readiness.md)。

## 從 Hermes Web 安裝

1. 開啟 Plugins 頁，選擇 Install from Git。
2. 輸入 `WOOWTECH/hermes-omnigent-plugin`，勾選啟用。
3. 首次安裝或更新後端程式後，Hermes 0.19 需要重載 Dashboard，再重新整理瀏覽器。這是該版本的插件 API 冷掛載限制；不要以 Gateway 重啟替代。
4. 配對面板自動出現。輸入你的 Server、帳號與遮罩密碼。
5. 等待依賴下載、主機連線與實際對話驗證；成功後從 Omnigent 頁選擇引擎與模型，按「開啟已驗證的對話」，直接到同一主機與所選模型的 Omnigent 對話。此對話保留健康檢查歷史，後續可繼續輸入訊息。

配對視窗關閉後，進入 **Plugins → Omnigent 設定 → Open** 可再次開啟持續設定頁，管理連線、重新配對、現有引擎、模型及對話驗證。

公開 repo 不需要 GitHub token。CLI 也能安裝：

```bash
hermes plugins install WOOWTECH/hermes-omnigent-plugin --enable
```

CLI 安裝與上述驗收命令請使用執行 Hermes 的服務帳號。管理者以 root 操作其他使用者擁有的 profile 時，插件會在寫入前回報 `PROFILE_USER_MISMATCH`，避免建立服務讀不到的0600檔案。

首次新 API 掛載仍遵循 Dashboard 重載要求。Hermes 原生啟用由 `plugins.enabled` 管理，不以安裝當作登入同意。已掛載的 API 會即時遵守停用設定，重新啟用或 Git 強制重裝時配對核准、回條及直接執行同意失效。每次安裝由 checkout 中不納入 Git 的唯一識別檔及 Git clone 時間戳記辨識。Hermes 0.22 強制重裝即使保留未追蹤檔案，也會重新要求配對；一般 Git pull 更新保留原有核准。

## 操作

- Hermes 的 **Omnigent 設定**分頁（Plugins 頁按 **Open**）：重新配對、取消連結、實際對話驗證、模型／模組清單與 Omnigent 連結。
- `/omnigent status`、`/omnigent catalog`、`/omnigent verify`、`/omnigent open`、`/omnigent setup`。
- 模型工具 `omnigent_status`、`omnigent_catalog`、`omnigent_verify`、`omnigent_open`。工具沒有密碼／帳號欄位；登入只能透過配對面板。

密碼提交後即清空，既不寫入對話、插件狀態、瀏覽器 storage，也不在 API 驗證錯誤裡回顯。Omnigent 官方 token store 放在此插件的私有資料目錄。

## 執行環境與依賴

Linux x64／arm64，Python >=3.11，Hermes Agent 插件 API 與 Dashboard SDK 1.1。指定測試站已驗證登入、配對、模型選擇與真實對話；其他平台仍是候選支援。rc.8 曾在 Hermes 0.19 通過指定站驗收；本次相容性修正以 Hermes 0.22.5／Python 3.14 容器實測，詳細範圍以驗收紀錄為準。

Omnigent 固定下載 0.19.1。優先沿用 PATH 或 Hermes bundle 中的 uv；兩者皆無時以私有 pip target 安裝 uv 0.12.10。Omnigent/Python/uv 放在 `$HERMES_HOME/omnigent-plugin`，保留現有工具版本。

原生對話需要 tmux >=3.3 與符合既有 sandbox 政策的 bubblewrap。已有則沿用；Debian 容器缺少時，使用容器現有已簽章 APT index 的 SHA256 從 Debian HTTPS 套件源下載，解包到插件私有目錄。函式庫搜尋只套用 private native wrapper，不修改系統套件或其他引擎。索引缺少或引用已移除套件時，只在插件目錄更新官方簽章索引，同時支援 Debian security 套件來源。沒有適用的套件索引或平台時，會回報需要的依賴。

Hermes 自身使用既有 provider、模型與 profile。本機 llama.cpp 模型會共用原 profile 已啟動、經 Hermes 擁有權驗證的 loopback 推論服務；未啟動時須先在 Hermes 的 Local models 設定啟動。Omnigent 官方 per-session home 會私有複用原有 `.env`／`auth.json`，不修改來源；配對密碼不放入這些資料。缺少訂閱／供應商登入的引擎會明確驗證失敗。

本機服務可連線仍須通過完整 Hermes 對話驗證。本次第二站的 Qwen 0.8B 可回覆最小 API 請求，但完整對話在關閉思考模式及只載入 terminal 工具的比較中仍逾時，因此該站維持 `needs-verification`。主站 Sol／Luna 的實際對話已通過；完整測試條件與限制見驗收紀錄。

Omnigent 0.19 的 Hermes 原生支援仍缺模型目錄／切換與 `HERMES_HOME` 路徑處理，本插件只在自己安裝的 Omnigent venv 補上這些分支。相容層要求精確 0.19.1 與原碼錨點，拒絕修改系統或其他插件的 Omnigent；原始碼私有備份與SHA256收據保存在插件狀態目錄。模型由 Hermes 官方 inventory 載入，只列出當前 provider 的識別碼。對直接設定於 `model` 的 custom endpoint，使用原生 runtime provider 憑證解析與 `/models` 探索，避免原生空金鑰探測只顯示預設模型。清單保留供應商提供的替代模型；模型滿載時可手動選擇另一個模型再驗證。模型出現在清單不代表當下容量或所有工具功能已通過。

### 容器隔離能力

安裝先探測 Bubblewrap namespace。容器不支援時，停止在配對面板並顯示具體原因；不更動 sysctl、Pod capability、AppArmor 或 sandbox 政策。

預設保留隔離。若使用者明確勾選「允許 Hermes 直接執行」，只將此插件的 Hermes 原生對話設為 `sandbox=none`，沿用該容器原本檔案／網路權限；其他引擎的隔離不變。勾選不是預設，也不是模型工具可傳入的欄位；新登入／重新啟用要重新選擇。已執行的 host 模式維持到取消連結或受控重新載入。

Hermes 0.22 的直接執行模式會為插件對話建立專用 Gateway。只管理該服務 UID 與對話目錄內的程序，停止對話時連同專用 Gateway 清理；原 Hermes profile 的 Gateway 不受影響。新對話的原生資料與轉送進度保存在 `$HERMES_HOME/omnigent-plugin/sessions`，應隨 Hermes profile 一起掛載持久化磁碟。重新開啟對話沿用原生對話 ID 與轉送進度，保留上下文。升級時可將仍存在於舊容器暫存目錄、已停止且轉送完成的對話，透過 Hermes 原生匯出／匯入介面搬到私有持久化目錄，重新建立原生 profile 綁定；原始暫存資料保留，已被刪除的舊暫存資料無法復原。插件終端使用明確暗色設定，避免瀏覽器延遲回應背景色探測時被誤當成聊天文字。

PVC 掛載可能增加群組權限。插件會以不跟隨符號連結的方式開啟並核對自身目錄擁有者，再恢復 `0700`；不修改其他使用者目錄。驗證失敗時，設定頁分別呈現對話錯誤與停止結果。Hermes 冷啟動版本探測期限為 30 秒，host 同一輪只探測一次 Hermes，各別引擎仍需實際回覆才算通過。

首次更新相容層／執行模式時，插件核驗 PID、server、UID 與設定目錄，只重新載入自己管理的 host，保留登入。外部管理器的 host 不會被插件重啟。

## 部署驗收

在插件目錄執行：

```bash
python -m hermes_omnigent.cli status
python -m hermes_omnigent.cli acceptance
python -m hermes_omnigent.cli acceptance --chat
python -m hermes_omnigent.cli verify --engine hermes --model MODEL_FROM_CATALOG
```

預設 acceptance 不發送模型提示；`--chat` 才建立固定無工具測試對話。退出碼0表示所有已裝引擎都已通過、主機在線且模型／模組清單可用。對話完成後停止，歷史保留供查核。

取消連結清除此插件對指定server的本機登入，等待本機 host 離線。取消連結後 server 仍保留該 host 的註冊（顯示離線），註冊ID與歷史保留。重新配對要重新輸入登入資料。

## 開發驗證

```bash
python -m pip install omnigent==0.19.1
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
node --check dashboard/dist/index.js
```

### 相容性監看

- `tests/server-contract/run.py` 在本機啟動真正的 Omnigent server（只聽 loopback、不接模型或外部服務），用插件自己的 adapter 走完連結、主機上線、重連、重啟、取消連結。CI 的 `server-contract` 工作對 server 0.16.0 與 0.19.1 各跑一次；本機執行：`python tests/server-contract/run.py --expect-sdk-version 0.16.0`，`--server-omnigent PATH` 可改測另一個版本的 server。
- 每週一的 `omnigent-canary` 工作對固定版本及其後發行的新版重複上述檢查，並用 `.github/scripts/check-omnigent-compat.py` 檢查相容層錨點是否還能套用。結果集中在一則標籤為 `omnigent-compat` 的 issue，只有結果改變或出現新版時才留言。錨點不符不影響固定版本的插件，只表示升級 SDK 前要先改相容層。
- GitHub 會在公開 repo 連續 60 天沒有活動時停用排程工作；issue 內的「Last run」日期可用來確認它還在跑。

Omnigent adapter 以 MIT 授權重用 [Pi Omnigent Plugin](https://github.com/WOOWTECH/pi-omnigent-plugin) v0.2.1，Hermes 配對及 Dashboard 為本專案功能。
