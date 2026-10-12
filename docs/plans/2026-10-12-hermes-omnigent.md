# Hermes Omnigent Plugin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 從 Hermes Web 插件頁安裝／啟用後開始新的配對綁定流程，連結 Omnigent 並證明本機既有引擎可對話。

**Architecture:** 一個 Hermes Python 插件搭配 Dashboard SDK 的配對頁與 overlay。狀態、私有 Omnigent 依賴與 host 放在 HERMES_HOME/omnigent-plugin；沿用已驗證的 Pi 版 Omnigent adapter，增加 Hermes 原生引擎，所有操作先重新核驗 enable／登入核准。

**Tech Stack:** Python 3.11+、Hermes 0.19+ PluginContext/FastAPI、Dashboard SDK 1.1、Omnigent 0.16、uv 0.12.10、React host SDK。

---

## Task 1: 部署／API 偵察
- 讀已部署 Hermes plugins.py、plugins_cmd.py、web_server.py、Dashboard registry。
- 只讀核驗 woowtech Hermes 與其他工作的資源所有權，正式目標為 paas-ws-052c6889/svc-6e4171af-hermes-agent。
- 找出首次安裝後 API 冷掛載限制；不能宣稱核心不支援的自動生命週期。

## Task 2: 後端與原生引擎
- Create: hermes_omnigent/runtime.py, adapter.py, cli.py, __init__.py, plugin.yaml。
- 狀態寫入0600/fsync/flock；背景工作只回安全進度，不保存密碼。
- Omnigent/uv 只安裝至私有目錄，保留現有 Hermes/Claude/Codex/Pi；不安裝引擎。
- 新網址／新登入／原生重新啟用清除核准與回條；錯誤帳密不可復用舊 ready。
- 驗證完整 sentinel、host ID、CLI version、成功停止；預设不發模型訊息。

## Task 3: Dashboard UI
- Create: dashboard/manifest.json, dist/index.js, dist/style.css, api.py。
- 首次載入及重新啟用自動 overlay；一律重新問 server/account/password。
- 密碼僅在 UI 記憶體與一次 request body，提交後清空，不在工具／對話／storage／錯誤回顯。
- 以 host SDK fetchJSON 使用原生認證；顯示依賴／配對／驗證進度、真模型／引擎選擇、Omnigent 連結。

## Task 4: 合約與實機驗收
- Create tests/test_runtime.py,test_dashboard.py,test_plugin.py；重用 adapter 合約測試並加 Hermes case。
- 核驗 fresh auth、disabled拒絕、錯誤帳密、競態、timeout、singleton host、卸載／重啟。
- 本機／隔離狀態測試後從 Hermes Plugins 頁公開 GitHub source 安裝。
- 原生 Dashboard 在必要時重載其 Web 程序；不升級 Hermes、不修改其他 session/排程/Gateway。
- 指定 server 帳密沿用使用者原授權，只直接送入配對 UI。

## Task 5: 公開交付
- Create README.md、docs/commercial-readiness.md、CI、versioned release。
- 公開 WOOWTECH/hermes-omnigent-plugin；匿名下載驗證，API/瀏覽器實際配對、模型／模組及 sentinel 回覆。
- 官方目錄非必要；上架需另向上游提交審核。未實測平台與已知冷掛載限制誠實記錄。
