# Hermes Omnigent 候選版驗收紀錄

版本：v0.1.0-rc.8。日期：2026-10-12 UTC。公開來源：WOOWTECH/hermes-omnigent-plugin。

指定 Hermes0.19 Linux x64 容器已在使用者明確同意直接執行後通過連線、模型選擇與真實對話驗證。其他平台、引擎及多人負載仍需驗收，維持候選版。

## 2026-10-25 重新驗收（進行中，公開版仍為 rc.8）

現場 Hermes 已升級至 **0.22.5／Python 3.14／Linux x64**，與 10 月 12 日的 0.19 不同。這次發現並修正新版的依賴、原生 Gateway、MCP 路徑與對話啟停相容性；以下僅列已取得的證據。

| 驗收項目 | 結果 |
| --- | --- |
| 原核准測試站 052c6889 | 已恢復公開插件安裝與配對，host 在線 |
| 公開 HTTPS Dashboard 登入 | 用部署注入的現有登入資料通過正常登入；未重設或繞過驗證 |
| Plugins → Omnigent 設定 → Open | 正式外部網址通過；1440／360／320px 設定頁先前檢查無水平溢出 |
| 模型目錄 | 取得既有 provider 的 14 個實際模型 ID；不代表所有模型的即時容量或功能均已驗收 |
| Sol 真實對話 | `chatgpt/gpt-5.6-sol` 收到 `OMNIGENT_CONNECTION_OK`，停止成功，專用 Gateway 確認已退出 |
| Luna 正式 UI 驗證 | 從設定頁選取 `chatgpt/gpt-5.6-luna`，實際回覆與停止皆成功 |
| Omnigent 對話入口 | 開啟正確對話；正常登入後能看到真實驗證回覆 |
| 後續網頁對話／工具／MCP | 已收到全新識別字的續聊回覆；`terminal` 執行 `pwd`，Omnigent `tool_describe`／`tool_call` 讀取本測試對話，服務端工具回條與輸出皆存在 |
| 截圖測試站 782fd73d | 插件已配對、host 在線；原生 Hermes 缺少有效模型 provider 憑證，尚不能驗收實際對話 |
| 停止／重連後的上下文 | 重啟插件 host 後，從網頁恢復同一對話，正確回憶停止前的測試識別字；對話轉送進度保留 |
| 停用／重新啟用 | 原生 Plugins 停用後 API 拒絕操作（404）；重新啟用後空白配對欄位、直接執行未勾選，重新配對及實際回覆成功 |
| 取消連結 | 正式 UI 取消後 host 離線、登入核准失效；空白欄位重新配對後收到實際回覆並恢復 ready |
| GitHub | 公開 repo；這批修正尚未發布新版本 |

兩台皆只連結當下已安裝的 Hermes；沒有下載 Claude Code、Codex 或 Pi。兩台皆有 `/opt/data` PVC。原站曾缺少 checkout 與插件資料的原因仍未知，不能因此推斷 PVC 故障。

目前 109 項 Python 回歸與 5 項 Node 介面測試通過；現場測試另外列示。

### 本次修正與證據

- Hermes 新版以 `hermes_yaml` 與內附 uv 提供執行環境，插件現在能沿用；私有 SDK 的 Python import path 與 Hermes 自身依賴隔離，解決 MCP 套件版本衝突。
- 精簡 Debian 映像沒有 APT index 時，在插件目錄下載官方簽章索引；支援 Debian security 套件來源，持續檢查索引提供的 SHA256。現場缺少 tmux／bubblewrap 時已成功下載與探測。
- 保留原生設定 schema 版本；Omnigent transcript observer 不再提前建立空 `state.db`，避免 Hermes 初始化失敗。
- 在明確同意直接執行時，以 Hermes 官方 unmanaged Gateway 支援每個 Omnigent 暫存 profile。只接受服務 UID 擁有的該對話目錄，以 PID／啟動時間／boot ID／HERMES_HOME 核對自己的 Gateway。使用程序限定的 `HERMES_GATEWAY_NO_SUPERVISE`，原 profile 的 s6 Gateway 不受影響。
- 新版 Hermes 給工具設定個別 TMPDIR；MCP 現在明確使用建立 bridge 時的目錄，保留原 SDK 的目錄擁有者與私有權限驗證。
- 初次對話提交允許有界的原生啟動等待；網路不可達與請求逾時分開回報。停止已退出的 terminal 可重複執行，缺少 tmux 或傳輸錯誤不算停止成功。
- 原生背景色探測的延遲 OSC11 回應曾被當成使用者訊息。插件終端改用 Hermes 支援的明確暗色設定，避免發出該探測；送達確認必須匹配新寫入的使用者全文。已從正式網頁重跑續聊、原生工具、MCP 工具成功。
- 前端共用進行中的 status 請求，避免慢回應期間疊加輪詢；失敗後可再次查詢。這是重疊請求修正，尚非多人壓力或長時間測試結論。
- 原生 `on_unload` 在停用時留下持久化撤銷識別；即使操作持鎖、快速停用又啟用，舊登入與直接執行同意也不再有效。一般 hot reload 保留核准。
- 新版強制重裝會保留未追蹤檔案。安裝識別加入 `.git/config` 時間戳記：一般 Git pull／原生 staging copy 保留識別，新 clone 使登入與直接執行同意失效。舊 marker 首次升級保留同意。已通過回歸測試，仍待正式強制重裝實測。

尚待完成：公開新版強制重裝、重建恢復、併發及長時間測試。模型來源缺失的部署需要其管理者完成既有 provider 設定，不能以其他部署的金鑰代填。

## 實機結果（2026-10-12 歷史紀錄）

- 真正 Hermes Web Cookie登入與原生 Plugins頁的公開 Git 安裝、停用／啟用、Open tab導覽已驗證。初始配對網址、帳號、密碼均空白，密碼遮罩；直接執行預設不勾選。
- 本容器仍拒絕 Bubblewrap namespace。使用者明確同意後，從正式配對面板勾選直接執行並重新輸入 Omnigent登入資料；此選項只影響本插件私有 SDK 啟動的 Hermes，保留現有容器與 UID10000。
- 本插件 host 在線。只連結現有 Hermes，沒有下載缺少的 Claude Code／Codex／Pi。
- 插件模型選單選取 `chatgpt/gpt-5.6-sol`，原生 Hermes回覆 `OMNIGENT_CONNECTION_OK`，停止請求成功。Omnigent正式網頁能看到該回覆；透過網頁文字輸入又收到 `HERMES_WEB_CHAT_OK`。
- 同一 provider 的 `chatgpt/gpt-5.6-luna` 也收到驗證回覆且停止成功。模型目錄現在能取得該 endpoint 實際提供的13個ID。目錄不代表即時容量；不同模型可分別驗證。
- 這次僅受控重載 Dashboard與本插件自己的Omnigent host。原有 Hermes Gateway PID保持151，容器沒有重建。
- 79個Python回歸測試與4個Node介面契約測試。真實瀏覽器驗收另列。

- 新增「開啟已驗證的對話」入口。正式瀏覽器從插件選取Luna後，開啟同一host/model的Omnigent對話並看到真實回覆；320px無水平溢出。連結核對server、host、模型、CLI版本、確切回覆及停止回條，不會開到其他主機或模型。

- 公開rc.7從原生Plugins頁再次強制重裝，實機確認ready與直接執行同意失效：登入核准false、回條清空、直接執行false，三個配對欄位重新空白。重新填寫資訊與明確勾選後，再次收到驗證回覆並恢復ready。
- 對本插件host先查核沒有running對話，再受控停止與恢復；PID確實改變，主機重新在線，原有同意保留，恢復流程未發送模型提示。恢復後另由正式插件頁選取Luna，取得新的回覆與停止回條，狀態ready。沒有重啟Gateway。
- rc.8 在核准的 Hermes 0.19 測試站從原生 Plugins 頁執行一般 Git pull 更新（沒有 force reinstall）；清單顯示「Omnigent 設定」，按 Open 實際進入 `/omnigent`，頁面顯示「連線與配對」及「引擎、模型與對話」兩區。更新前後 API 維持 ready、登入核准保留；沒有重配對或重啟 Gateway。

## 修復與回歸

1. root 管理終端曾建立服務帳號無法讀取的0600狀態。只調整本插件 checkout／私有資料的擁有者；新增 `PROFILE_USER_MISMATCH` 寫入前防護，不放寬檔案權限。
2. Plugins卡片相對tab路徑曾導到`/plugins/omnigent`；改成宿主絕對路徑`/omnigent`。
3. Omnigent host→runner的環境白名單曾丟掉Hermes profile及直接執行旗標，造成新的TUI判定沒有供應商設定。現在只明確傳遞三個選擇器，保留既有 passthrough；不用整個`HERMES_*`前綴放行。
4. Hermes0.19對直接設定於`model`的custom endpoint，以空金鑰探索`/models`，只留下預設ID。插件改用原生runtime provider解析與模型探索函式，在服務帳號內使用同一憑證取得替代模型。失敗時保留已設定模型，原始錯誤與憑證不輸出。
5. 慢速host查詢期間驗證可能已完成；status在查詢後重新讀取狀態與回條，避免回傳舊的`verifying`。
6. 原生Git強制重裝替換checkout但不一定切換enabled。實測目錄inode在重裝後重用；改由checkout內0600、Git忽略的唯一識別檔監看每次安裝，重新要求登入、清空驗證回條及直接執行同意，不沿用舊ready。

## 私有相容層與資料

私有Omnigent固定0.16.0，測試server為0.17.0。Hermes原生支援的模型目錄／模型事件／launch override及profile路徑由插件在自己的uv venv補上。新版相容層只修改5個SDK檔案，先驗證版本、原碼錨點及編譯；保留原碼備份、SHA256與generation回條。非私有SDK、不同版本或被其他程式修改的來源皆拒絕。

供應商憑證只在Hermes服務帳號與其私有per-session home使用。配對密碼不寫入對話、插件狀態、browser storage或API驗證錯誤；登入token由Omnigent官方私有store管理。公開repo不包含帳密、token或auth雜湊。

新Dashboard API或後端程式更新在Hermes0.19需重載Dashboard才能掛載；不能把安裝成功誤稱API已載入。插件停用後API即時拒絕操作，重新啟用需重新配對。

## 尚未驗收的範圍

- 支援Bubblewrap的環境、Linux arm64及Hermes Desktop。
- 目前不存在於測試容器的Claude Code、Codex、Pi，以及其他供應商。
- MCP／skills的模型實際工具呼叫、所有13個模型的工具／圖片能力、多人併發與長時間負載。
- 完整Pod重建復原。插件自有host重連已驗證；不以整個Gateway重啟替代。

此版本預設保留Bubblewrap。明確opt-in只跳過Hermes內層沙箱；不修改Pod capability、sysctl、AppArmor、host namespace或k3s設定。
