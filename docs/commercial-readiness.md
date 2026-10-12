# Hermes Omnigent 候選版驗收紀錄

版本：v0.1.0-rc.3。日期：2026-10-12 UTC。公開來源：WOOWTECH/hermes-omnigent-plugin。此候選版尚未完成指定正式環境的對話與登入 UI 驗收，不宣稱商業驗收完成。

## 已驗證

- 原生 Hermes plugin 安裝函式從公開 GitHub repo 下載、辨識 manifest 並啟用成功。
- 私有 Omnigent0.16.0、tmux、bubblewrap 安裝完成；沒有安裝或更新 Hermes／Claude Code／Codex／Pi。指定容器只有 Hermes0.19.0。
- 首次新帳密透過 stdin 經插件 backend 登入測試 Omnigent；當時確認此插件獨立 host 在線。其他容器登入及 host 不受影響。
- 私有相容層修復 Hermes host model-options HTTP502，能取得當前 provider 真實的 `chatgpt/gpt-5.6-sol` 與 hermes-native-ui 模組。未將其他供應商模型誤列為可用。
- Dashboard 已受控重載，Gateway PID 前後同為148；沒有重啟 Gateway/Discord。
- 72個 Python 合約／安全測試、2個 Node Dashboard 契約測試通過。CI 對 Python3.12/3.13 執行同樣測試。
- 真實 React/Chromium 的合成 API 渲染測試，1440、360、320px均確認自動配對 overlay、空白初始登入欄位、密碼遮罩、browser storage無秘密、停用／認證失效後三個登入欄位全部清除、直接執行 opt-in 預設不勾選、模型選擇與無水平溢出。這是 UI fixture，不代表已登入正式 Hermes Web。

## 實機阻擋與待決定

- 指定容器 kernel/runtime 拒絕 Bubblewrap namespace；私有 bwrap 啟動探測退出1且回報 permission denied。只有下載執行檔不足以解除。
- 實際 Hermes test session啟動在原生 terminal 的 sandbox 階段失敗，沒有 sentinel；停止請求也未完成。插件未標記 ready；已核驗 host/title 後清理這筆失敗測試對話。
- 使用者需選擇保留隔離並改用支援環境，或明確允許僅插件的 Hermes 直接執行。尚未更動容器 capability、sysctl、AppArmor 或目前 host 的隔離模式。
- Hermes Web 的登入帳密尚未提供。實際 Plugins 頁點選、正式 cookie auth、配對面板填寫、Omnigent內模型切換、實際回覆／清理與重啟持久化仍待完整驗收。
- 一般引擎／模組選擇、skills/MCP的實際工具呼叫、Linux arm64及Hermes Desktop並未實機驗證。

## 私有相容層

Omnigent0.16的原生Hermes實作缺少host/session模型目錄、模型事件、模型 launch override，且設定與登入資料路徑寫死為`~/.hermes`。本插件只對自己uv venv的4個SDK檔案套用已編譯／錨點驗證的相容改動；保存原始碼備份及 SHA256/generation 回條。不同SDK版本、非插件私有SDK、已被其他程式修改的來源都拒絕處理。

Hermes供應商API資料只由原生Hermes inventory解析並回傳白名單model IDs。SDK的per-session home按原生流程複用`.env`及`auth.json`，仍是私有資料；不修改來源。Omnigent帳密不寫入聊天、工具參數、browser storage或狀態檔。

新 Dashboard API 在 Hermes0.19只於啟動掛載。第一次安裝後需重載 Dashboard並重新整理；此版本無法完全省略這個宿主要求。單純 install/enable 不能誤稱 API 已掛載。

## 下載與狀態

Repo公開可直接下載候選原始碼；正式部署前須完成上述驗收。候選版不預設移除隔離，並會在缺少namespace時先回報`SANDBOX_NAMESPACE_UNAVAILABLE`，避免建立已知不能啟動的測試對話。

## 2026-10-12 權限修復

實機 Dashboard／Gateway 使用 UID10000；先前管理終端以 root 建立的插件私有狀態是0600，導致正式 Cookie登入後 `/status` 回傳400 `INVALID_STATE`，自動配對視窗無法讀取狀態。用 UID10000 重現此錯誤後，只移交本插件 checkout 與私有資料的擁有者，服務帳號狀態讀取已恢復。沒有放寬檔案權限，也沒有更動其他插件或 Gateway。

新增回歸防護：root 不得寫入其他使用者擁有的 Hermes profile／插件資料；在任何寫入前回報 `PROFILE_USER_MISMATCH`。合法的非 root 服務帳號仍可使用透過群組授權的 root-owned mount。72個Python測試與2個Dashboard測試通過。Cookie登入曾成功驗證；完整自動視窗／配對／對話流程仍需重新驗收。

此站 Kubernetes Secret 與執行中容器注入的密碼設定不一致，提供的密碼也不等於該 Secret；未修改帳密或認證設定。
