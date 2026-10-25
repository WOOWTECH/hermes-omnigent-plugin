# Hermes Omnigent 候選版驗收紀錄

版本：v0.1.0-rc.12 候選版。驗收更新：2026-10-25 UTC。公開來源：WOOWTECH/hermes-omnigent-plugin。

指定主站 Hermes 0.22.5／Linux x64 容器已在使用者明確同意直接執行後通過連線、模型選擇與真實對話驗證。其他平台、引擎及多人負載仍需驗收，維持候選版。

## rc.12 追加驗收

- 第二站的 Hermes 原生目錄回傳 host 在線、Hermes engine ready，以及 13 個實際模型選項；Claude Code、Codex、Pi 在該容器均未安裝，插件正確回報缺少，不影響已安裝的 Hermes。
- 第二站以插件即時驗證 `gpt-6-luna` 時，新 session 建立成功，但立即送出的探針在 Hermes TUI 尚未就緒時未寫入 transcript，回報 `RUNTIMEERROR`；測試 session 已停止，plugin 狀態保持 `needs-verification`。
- 固定等待 10 秒的早期修正仍曾間歇失敗，證明延遲本身不能可靠解決 Hermes 首次事件遺失。候選版現在只在 `RUNTIMEERROR` 且 Omnigent items 沒有接受 user message 時，成功清理舊 session 後才用全新 session 重試一次；訊息已接受或清理失敗就不重送。重試與這兩個安全條件有回歸測試覆蓋。
- 以目前工作樹的候選 adapter.py 在實際 Hermes 0.22.5 容器內執行一次性 Omnigent session，模型 `gpt-5.6-sol` 精確回覆 `OMNIGENT_CONNECTION_OK`，session 清理結果為 `stopped`。這是 adapter 實際對話驗證，不代表公開 repo 已安裝版本或瀏覽器 UI 已完成驗收。
- 測試容器 `hermes config get approvals.mode` 回報 `smart`，即低風險命令自動通過、危險或不確定命令仍要求確認；未將 profile 改成 `off`／YOLO。
- 第二站目前讀取到的有效 Hermes 設定為 `openai-codex`／`gpt-6.1-sol`，沒有自訂 provider。這次沒有改寫該 profile 或其登入資料；安裝修正後仍須在第二站重跑插件驗證。
- 指定 LiteLLM Gateway 的 live `/model/info` 顯示 19 個 Sol／Luna 模型 ID，專用 alias 查無既有金鑰。Hermes pod 對外域名回傳 403；對內部 Service 的 TCP 4000 連線遭拒。兩側 NetworkPolicy 目前分別未允許第二站 Hermes 到 Gateway 的流量。
- 兩條只允許 Hermes 與內部 LiteLLM 之間 TCP 4000 的 NetworkPolicy 已通過 server-side dry run；套用被自動審核拒絕，因為「都同意」未被判定為對這項跨 namespace 持續變更的明確授權。規則尚未套用，也沒有建立推論金鑰或修改 Gateway／Hermes provider；已送出逐條說明的確認提示。
- Omnigent 0.16.0 下 Python 3.12、3.13、3.14 各 145 項測試全數通過；6 項 Node 測試與 Dashboard 語法檢查亦通過。

## rc.10 追加驗收

- 第二站原生模型設定已改為本機 Qwen 0.8B。平台查核確認該服務的 WOOW LLM Gateway ConfigMap 仍為空，所屬帳戶尚無平台 AI 模型服務；未複用其他站金鑰。
- 真實失敗回條及僅屬於測試對話的 runner traceback 均確認：插件根目錄 `0770` 觸發 `HERMES_SESSION_DIRECTORY_INVALID`，導致啟動與停止一併失敗。修正只恢復服務 UID 擁有、非符號連結的插件目錄為 `0700`；來源歷史仍要求嚴格私有權限。已測試群組權限恢復、錯誤擁有者拒絕及符號連結拒絕。
- 第二站 Hermes 包裝 CLI 冷啟動查版本量得 11.56 秒，超過原 8／10 秒期限。插件與私有 SDK 的 Hermes 探測改為 30 秒，同一輪共用 Hermes 別名探測結果；其他引擎期限與版本條件保持原值。
- 原始對話失敗與停止失敗分別保存安全錯誤碼，設定頁顯示對應處理說明；不再讓停止失敗覆蓋真正的啟動錯誤。
- 本機模型的原生設定需要原 profile 的受擁有權保護 runtime 狀態。新增 native helper 只解析原 profile 已擁有的 loopback 推論服務，將連線資料透過私有 pipe 交給對話設定；不複製程序身分、重啟原服務或為每個對話載入模型。雲端 provider 不經此 helper。
- rc.10 公開 main `e97ffb7` 的 140 項 Python 與 6 項介面測試，以及 GitHub CI Python 3.12／3.13／3.14 均通過。兩站由正式 Plugins Git pull 更新，HTTP 200、checkout 乾淨、登入與直接執行同意保留；SDK 與 host generation v17 一致。主站再次執行 `acceptance --chat`：exit 0、ready。
- 第二站原生啟動與停止已恢復，並建立私有 Gateway／SQLite 對話資料。本機 Qwen 的直接 API 測試在停用 thinking 時於 0.79 秒回覆正確；預設 thinking 的 80 token 有界測試耗時 3.72 秒、達到輸出上限且未給出最終答案。完整 Hermes 對話仍等待模型輸出而逾時，停止成功；不能將這台標示 ready，工具流程仍未通過。
- 同一平台管理者擁有的既有 LiteLLM Gateway 可供接回第二站，已確認 Sol／Luna 存在。從 Kubernetes 注入環境取得的管理認證對建立專用金鑰回覆 401；未取得成功建立回條，檢查指定 alias 時無金鑰，未改第二站模型設定。非標準程序環境憑證探查被自動核准審查拒絕，已停止並要求提供正式專用推論憑證來源。

### rc.10 後續現場驗證

- 主站以正常 Omnigent 網頁登入，收到全新識別字的一般回覆、terminal `pwd` 回覆及 MCP 回覆；服務端確認 `terminal`、`tool_describe`、`tool_call` 皆實際執行，MCP 結果含本測試對話 ID，error 數為 0。
- 停止本測試對話並確認專用 Gateway 已退出後，僅將其對話及 home 目錄設成 `0770` 重現 PVC 群組權限情境。從網頁恢復同一對話，正確回憶前次 terminal 回覆識別字；四層插件目錄皆恢復 `0700`，再次停止成功。
- 第二站單一測試對話使用 `--reasoning none`，186.98 秒後仍未收到指定回覆，停止成功；不能把關閉思考模式當成已解決。
- 第二站測試期間遭外部 rollout 重建（14:03 與 14:10 UTC），一次工具範圍比較因此中斷、exit 143；不計為通過或插件故障。新 Pod 的既有配對／直接執行同意仍在，SDK 與 host generation v17 一致。
- 重跑第二站 `--reasoning none --toolsets terminal` 比較，183.20 秒後仍為 `REPLY_TIMEOUT`，取得停止回條。前次遭 rollout 中斷、尚未送出使用者訊息的驗證對話也已按 ID／host／標題核對並停止，專用 Gateway 不再執行。
- 第二站當時 cgroup CPU 上限為 2 核、記憶體上限 6 GiB，已有 CPU throttling；模型服務提供 4 個 slot，各自 context 上限 262144。完整 Hermes 測試期間確實觀察到模型正在處理 prompt、尚無 decoded token。這些是效能診斷證據，尚不足以證明限流或 context 設定就是逾時根因；未變更共用模型服務設定。
- 最後狀態查核：主站 `ready`、第二站 `needs-verification`／`INCOMPLETE_CHAT_VERIFICATION`；兩站 host 皆在線。第二站保持原有本機模型設定，未因最小 API 測試成功而誤標完整對話通過。
- 主站同一帳號同時建立兩個全新 Sol 對話，各自收到不同的隨機識別字，含啟動／回覆／停止分別耗時 43.65、45.79 秒。兩個私有 SQLite 歷史各自保有自己的識別字且不含另一個，兩個專用 Gateway 皆已退出。這是有界雙對話隔離驗收，尚不代表跨帳號或長時間壓力測試。
- 從候選安裝壓縮包解出追蹤來源後，重新執行 140 項 Python 與 6 項 Node 測試均通過；壓縮包排除登入資料、安裝識別、快取與舊發行檔。

## 2026-10-25 重新驗收

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
| 後續網頁對話／工具／MCP | 已收到全新識別字的續聊回覆；`terminal` 執行 `pwd`，Omnigent `tool_describe`／`tool_call` 讀取本測試對話，服務端工具回條與輸出皆存在；持久化與跨節點重建後，再以 Sol 完成網頁回覆、terminal、明確指定名稱的 MCP 工具呼叫，回條包含本測試對話 ID 且無 error 項目 |
| 截圖測試站 782fd73d | 插件已配對、host 在線；最新追加測試見 rc.10 段落：啟動／停止已修復，本機模型完整對話仍逾時，Gateway 專用憑證待提供 |
| 停止／重連後的上下文 | 重啟插件 host 後，從網頁恢復同一對話，正確回憶停止前的測試識別字；對話轉送進度保留 |
| 停用／重新啟用 | 原生 Plugins 停用後 API 拒絕操作（404）；重新啟用後空白配對欄位、直接執行未勾選，重新配對及實際回覆成功 |
| 取消連結 | 正式 UI 取消後 host 離線、登入核准失效；空白欄位重新配對後收到實際回覆並恢復 ready |
| 公開 Git 強制重裝 | 從公開 main 安裝修正版本，乾淨 checkout；舊核准與直接執行同意失效，空白精靈重新配對及真實對話成功 |
| Pod 重建 | 原工作確認閒置後重建到另一節點，PVC 重新掛載、映像 digest 相同、2/2 Ready；插件自動恢復 ready、原核准保留 |
| 重建後的對話記憶 | 原暫存目錄已不存在；同一 Omnigent 對話從 PVC 恢復，正確回憶搬移及重建前的完整識別字 |
| CLI acceptance | 服務帳號執行退出碼 0、ready、模型與模組目錄可用，無額外模型提示 |
| API 錯誤輸入 | 未登入 401；錯誤網址 400、無效引擎 422，不回顯配對值、不撤銷有效配對 |
| 有界併發查詢 | 3 批、每批 2 個 status 查詢，6/6 HTTP 200／ready，0.856～1.663 秒；不代表多人模型壓力驗收 |

兩台皆只連結當下已安裝的 Hermes；沒有下載 Claude Code、Codex 或 Pi。兩台皆有 `/opt/data` PVC。原站曾缺少 checkout 與插件資料的原因仍未知，不能因此推斷 PVC 故障。

目前 140 項 Python 回歸與 6 項 Node 介面測試通過；現場測試另外列示。

### 本次修正與證據

- Hermes 新版以 `hermes_yaml` 與內附 uv 提供執行環境，插件現在能沿用；私有 SDK 的 Python import path 與 Hermes 自身依賴隔離，解決 MCP 套件版本衝突。
- 精簡 Debian 映像沒有 APT index 時，在插件目錄下載官方簽章索引；支援 Debian security 套件來源，持續檢查索引提供的 SHA256。現場缺少 tmux／bubblewrap 時已成功下載與探測。
- 保留原生設定 schema 版本；Omnigent transcript observer 不再提前建立空 `state.db`，避免 Hermes 初始化失敗。
- 在明確同意直接執行時，以 Hermes 官方 unmanaged Gateway 支援每個 Omnigent 對話 profile。只接受服務 UID 擁有的該對話目錄，以 PID／啟動時間／boot ID／HERMES_HOME 核對自己的 Gateway。使用程序限定的 `HERMES_GATEWAY_NO_SUPERVISE`，原 profile 的 s6 Gateway 不受影響。
- 新版 Hermes 給工具設定個別 TMPDIR；MCP 現在明確使用建立 bridge 時的目錄，保留原 SDK 的目錄擁有者與私有權限驗證。
- 初次對話提交允許有界的原生啟動等待；網路不可達與請求逾時分開回報。停止已退出的 terminal 可重複執行，缺少 tmux 或傳輸錯誤不算停止成功。
- 原生背景色探測的延遲 OSC11 回應曾被當成使用者訊息。插件終端改用 Hermes 支援的明確暗色設定，避免發出該探測；送達確認必須匹配新寫入的使用者全文。已從正式網頁重跑續聊、原生工具、MCP 工具成功。
- 一般更新後若私有 SDK 或 resident host 的 generation 過期，已核准的部署自動 restore 以更新自己的 host；未配對部署只準備依賴，不沿用舊登入。
- 前端共用進行中的 status 請求，避免慢回應期間疊加輪詢；失敗後可再次查詢。這是重疊請求修正，尚非多人壓力或長時間測試結論。
- 原生 `on_unload` 在停用時留下持久化撤銷識別；即使操作持鎖、快速停用又啟用，舊登入與直接執行同意也不再有效。一般 hot reload 保留核准。
- 新版強制重裝會保留未追蹤檔案。安裝識別加入 `.git/config` 時間戳記：一般 Git pull／原生 staging copy 保留識別，新 clone 使登入與直接執行同意失效。舊 marker 升級會辨別一般更新及新 clone；正式強制重裝、重新配對與實際回覆已通過。

新增對話與轉送進度保存在 PVC 的 `omnigent-plugin/sessions`。仍存在的舊暫存對話在停止後以 SQLite backup 讀取已提交 WAL，再使用 Hermes 原生匯出／匯入 API 搬移歷史，重新建立 profile 綁定；不搬移舊 Gateway 身分、socket 或 relay 憑證。轉送未完成、程序仍在執行或路徑不是服務帳號私有目錄時拒絕搬移，原資料保留。已完成實際搬移、網頁記憶驗證及跨節點重建驗證。

對話內模型切換透過 Hermes 原生 Gateway 的 authenticated control ticket、revision／execution generation 檢查進行，再確認持久化模型設定；不自動核准供應商／帳號切換的額外確認。停止後冷啟動與暖機後的 Omnigent 更新 API 均已成功切換至 Luna，原生設定亦一致；目前 Omnigent UI 的 Hermes「Configure session」按鈕停用，使用者仍需從插件設定頁選模型並開啟已驗證對話。

公開 main 已在 GitHub CI 的 3.12／3.13／3.14 通過；另有 6 項 Node 測試。主站透過原生 Plugins Git pull 更新至公開來源，HTTP 200，配對及直接執行同意保留，checkout 乾淨。舊截圖站缺少新版宿主的 owned Git checkout 記錄，原生更新明確拒絕；改以官方 Git 強制重裝成功。長時間與多人模型負載未驗收。

Luna 已通過一般回覆、terminal 及模型切換。該模型曾用包含多個詞的 `tool_search` 找不到 MCP 工具；MCP 握手、原生註冊與 Sol 指定工具的實際呼叫均正常。需要此工具時可使用 `tool_describe` 指定 `mcp__omnigent__sys_session_get_info`，再以 `tool_call` 呼叫；不把模型自主搜尋失敗視為工具呼叫通過。模型來源缺失的部署需要其管理者完成既有 provider 設定，不能以其他部署的金鑰代填。

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

私有Omnigent固定0.16.0，測試server為0.17.0。Hermes原生支援的模型目錄／模型事件／launch override及profile路徑由插件在自己的uv venv補上。新版相容層只修改6個SDK檔案，先驗證版本、原碼錨點及編譯；保留原碼備份、SHA256與generation回條。非私有SDK、不同版本或被其他程式修改的來源皆拒絕。

供應商憑證只在Hermes服務帳號與其私有per-session home使用。配對密碼不寫入對話、插件狀態、browser storage或API驗證錯誤；登入token由Omnigent官方私有store管理。公開repo不包含帳密、token或auth雜湊。

新Dashboard API或後端程式更新在Hermes0.19需重載Dashboard才能掛載；不能把安裝成功誤稱API已載入。插件停用後API即時拒絕操作，重新啟用需重新配對。

## 尚未驗收的範圍

- 支援Bubblewrap的環境、Linux arm64及Hermes Desktop。
- 目前不存在於測試容器的Claude Code、Codex、Pi，以及其他供應商。
- 所有 14 個模型的工具／圖片能力、額外 skills、多人模型併發與長時間負載；Omnigent MCP 與原生 terminal 已完成實際工具呼叫。
- 非本次指定容器映像的升級／回退；本次固定映像的跨節點 Pod 重建及對話恢復已驗證。

此版本預設保留Bubblewrap。明確opt-in只跳過Hermes內層沙箱；不修改Pod capability、sysctl、AppArmor、host namespace或k3s設定。
