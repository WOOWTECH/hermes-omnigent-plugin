# Hermes Omnigent 候選版驗收紀錄

版本：v0.1.0-rc.5。日期：2026-10-12 UTC。公開來源：WOOWTECH/hermes-omnigent-plugin。

指定 Hermes0.19 Linux x64 容器已在使用者明確同意直接執行後通過連線、模型選擇與真實對話驗證。其他平台、引擎及多人負載仍需驗收，維持候選版。

## 實機結果

- 真正 Hermes Web Cookie登入與原生 Plugins頁的公開 Git 安裝、停用／啟用、Open tab導覽已驗證。初始配對網址、帳號、密碼均空白，密碼遮罩；直接執行預設不勾選。
- 本容器仍拒絕 Bubblewrap namespace。使用者明確同意後，從正式配對面板勾選直接執行並重新輸入 Omnigent登入資料；此選項只影響本插件私有 SDK 啟動的 Hermes，保留現有容器與 UID10000。
- 本插件 host 在線。只連結現有 Hermes，沒有下載缺少的 Claude Code／Codex／Pi。
- 插件模型選單選取 `chatgpt/gpt-5.6-sol`，原生 Hermes回覆 `OMNIGENT_CONNECTION_OK`，停止請求成功。Omnigent正式網頁能看到該回覆；透過網頁文字輸入又收到 `HERMES_WEB_CHAT_OK`。
- 同一 provider 的 `chatgpt/gpt-5.6-luna` 也收到驗證回覆且停止成功。模型目錄現在能取得該 endpoint 實際提供的13個ID。目錄不代表即時容量；不同模型可分別驗證。
- 這次僅受控重載 Dashboard與本插件自己的Omnigent host。原有 Hermes Gateway PID保持151，容器沒有重建。
- 78個Python回歸測試與2個Node介面契約測試。真實瀏覽器驗收另列，不計入80個自動測試。

## 修復與回歸

1. root 管理終端曾建立服務帳號無法讀取的0600狀態。只調整本插件 checkout／私有資料的擁有者；新增 `PROFILE_USER_MISMATCH` 寫入前防護，不放寬檔案權限。
2. Plugins卡片相對tab路徑曾導到`/plugins/omnigent`；改成宿主絕對路徑`/omnigent`。
3. Omnigent host→runner的環境白名單曾丟掉Hermes profile及直接執行旗標，造成新的TUI判定沒有供應商設定。現在只明確傳遞三個選擇器，保留既有 passthrough；不用整個`HERMES_*`前綴放行。
4. Hermes0.19對直接設定於`model`的custom endpoint，以空金鑰探索`/models`，只留下預設ID。插件改用原生runtime provider解析與模型探索函式，在服務帳號內使用同一憑證取得替代模型。失敗時保留已設定模型，原始錯誤與憑證不輸出。
5. 慢速host查詢期間驗證可能已完成；status在查詢後重新讀取狀態與回條，避免回傳舊的`verifying`。
6. 原生Git強制重裝替換checkout但不一定切換enabled。生命週期監看安裝目錄身分，重新要求登入、清空驗證回條及直接執行同意，不沿用舊ready。

## 私有相容層與資料

私有Omnigent固定0.16.0，測試server為0.17.0。Hermes原生支援的模型目錄／模型事件／launch override及profile路徑由插件在自己的uv venv補上。只修改4個SDK檔案，先驗證版本、原碼錨點及編譯；保留原碼備份、SHA256與generation回條。非私有SDK、不同版本或被其他程式修改的來源皆拒絕。

供應商憑證只在Hermes服務帳號與其私有per-session home使用。配對密碼不寫入對話、插件狀態、browser storage或API驗證錯誤；登入token由Omnigent官方私有store管理。公開repo不包含帳密、token或auth雜湊。

新Dashboard API在Hermes0.19需首次重載Dashboard才能掛載；不能把安裝成功誤稱API已載入。插件停用後API即時拒絕操作，重新啟用需重新配對。

## 尚未驗收的範圍

- 支援Bubblewrap的環境、Linux arm64及Hermes Desktop。
- 目前不存在於測試容器的Claude Code、Codex、Pi，以及其他供應商。
- MCP／skills的模型實際工具呼叫、所有13個模型的工具／圖片能力、多人併發與長時間負載。
- 完整Pod重建復原。僅插件自有host重連可另行驗證；不以整個Gateway重啟替代。

此版本預設保留Bubblewrap。明確opt-in只跳過Hermes內層沙箱；不修改Pod capability、sysctl、AppArmor、host namespace或k3s設定。
