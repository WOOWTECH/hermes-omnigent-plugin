(function () {
  'use strict';
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry || !sdk.fetchJSON) return;
  const {createElement: h} = sdk.React;
  const {useState, useEffect, useRef} = sdk.hooks;
  const name = 'hermes-omnigent';
  const endpoint = '/api/plugins/' + name;
  let current = null, failure = '', busy = false, dismissed = false, listeners = new Set();
  let polling = null, users = 0;
  function emit() { for (const listener of listeners) listener(); }
  function safeServer(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && !url.username && !url.password && !url.search && !url.hash ? url.href : null;
    } catch (_) { return null; }
  }
  function verifiedChatURL(state, engine, model) {
    const receipt = state?.verification?.[engine];
    if (!model || state?.phase !== 'ready' || state?.host?.connected !== true || receipt?.ok !== true
        || receipt.reply !== 'OMNIGENT_CONNECTION_OK' || receipt.cleanup !== 'stopped'
        || receipt.model !== model || receipt.hostId !== state.host.id || receipt.server !== state.server
        || receipt.cliVersion !== state.engines?.[engine]?.version || typeof receipt.sessionId !== 'string') return null;
    try {
      const server = safeServer(state.server);
      if (!server) return null;
      const base = new URL(server), url = new URL(receipt.url);
      const path = base.pathname.replace(/\/$/, '') + '/c/' + encodeURIComponent(receipt.sessionId);
      return url.origin === base.origin && url.pathname === path && !url.search && !url.hash
        && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  async function request(path, body) {
    return sdk.fetchJSON(endpoint + path, body === undefined ? undefined : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
  }
  let refreshPending = null;
  function refresh() {
    if (refreshPending) return refreshPending;
    refreshPending = readStatus().finally(() => { refreshPending = null; });
    return refreshPending;
  }
  async function readStatus() {
    try {
      const previous = current;
      current = await request('/status');
      failure = '';
      if (current.pairingRequired && previous && !previous.pairingRequired) dismissed = false;
      busy = current.operation?.running === true;
      emit();
    } catch (_) {
      current = null;
      busy = false;
      dismissed = false;
      failure = '配對介面尚未載入，或目前登入無法存取。首次安裝請重載 Hermes Dashboard，再重新整理頁面。';
      emit();
    }
  }
  function useRuntime() {
    const [, setCount] = useState(0);
    useEffect(() => {
      const listener = () => setCount(value => value + 1);
      listeners.add(listener);
      if (users++ === 0) {
        refresh();
        polling = setInterval(refresh, 3000);
      }
      return () => {
        listeners.delete(listener);
        if (--users === 0) { clearInterval(polling); polling = null; }
      };
    }, []);
    return current;
  }
  function button(text, onClick, disabled, secondary) {
    return h('button', {type: 'button', onClick, disabled, className: secondary ? 'og-secondary' : ''}, text);
  }
  function PairForm({close}) {
    const [server, setServer] = useState('');
    const [account, setAccount] = useState('');
    const [password, setPassword] = useState('');
    const [allowDirectHermes, setDirect] = useState(false);
    const [error, setError] = useState('');
    const [sending, setSending] = useState(false);
    const passwordInput = useRef(null);
    useEffect(() => () => { if (passwordInput.current) passwordInput.current.value = ''; }, []);
    async function submit(event) {
      event.preventDefault();
      if (sending) return;
      if (!safeServer(server) || !account.trim() || !password) {
        setError('請輸入 HTTPS Server 網址、帳號與密碼。'); return;
      }
      setSending(true); setError('');
      const secret = password;
      setPassword('');
      if (passwordInput.current) passwordInput.current.value = '';
      try {
        await request('/pair', {server: server.trim(), username: account.trim(), password: secret, allowDirectHermes});
        dismissed = true; await refresh(); close();
      } catch (_) {
        setError('配對未啟動；請確認插件已啟用、登入有效，且沒有其他操作執行中。密碼請重新輸入。');
      } finally { setSending(false); }
    }
    return h('form', {className: 'og-pair', onSubmit: submit},
      h('div', {className: 'og-eyebrow'}, 'WOOWTECH · HERMES'),
      h('h2', null, '配對 Omnigent'),
      h('p', null, '輸入你要連結的 Server 與登入資訊。完成後會驗證這個容器當下已有的引擎。'),
      h('label', null, 'Omnigent Server', h('input', {type: 'url', required: true, value: server,
        placeholder: 'https://your-omnigent-server', autoComplete: 'url', onChange: e => setServer(e.target.value)})),
      h('label', null, '帳號', h('input', {required: true, value: account, autoComplete: 'username', onChange: e => setAccount(e.target.value)})),
      h('label', null, '密碼', h('input', {ref: passwordInput, type: 'password', required: true, value: password,
        autoComplete: 'current-password', onChange: e => setPassword(e.target.value)})),
      current?.sandbox?.namespaceSupported === false ? h('div', {className: 'og-sandbox-note'},
        h('p', null, '這個容器不支援 Bubblewrap namespace。你可以換到支援隔離的環境，或明確允許此插件的 Hermes 對話直接在容器執行。'),
        h('label', null, h('input', {type: 'checkbox', checked: allowDirectHermes, onChange: e => setDirect(e.target.checked)}), '允許 Hermes 直接執行：這些對話不使用 Bubblewrap 隔離，沿用容器的檔案與網路權限。')) : null,
      h('p', {className: 'og-note'}, '密碼只用於本次登入，提交後即清空；不寫入聊天或插件狀態。'),
      error ? h('p', {role: 'alert', className: 'og-error'}, error) : null,
      h('div', {className: 'og-actions'}, h('button', {type: 'submit', disabled: sending || busy}, sending ? '正在開始配對…' : '開始配對'),
        button('稍後設定', () => {setPassword(''); dismissed = true; close();}, sending, true)));
  }
  const phases = {'needs-login': '等待你完成配對', installing: '準備所需套件', connecting: '連結主機',
    verifying: '驗證實際對話', ready: '已連結並通過對話驗證', 'needs-verification': '需要重新驗證',
    error: '操作需要處理', disabled: '插件已停用', disconnecting: '正在取消連結'};
  function verificationDetails(receipt) {
    if (!receipt || receipt.ok) return '';
    const reasons = {
      ENGINE_NOT_READY: '原生引擎尚未就緒，請先完成模型供應商設定與登入。',
      MODELS_UNAVAILABLE: '目前無法取得模型，請檢查原生引擎的供應商設定。',
      NATIVE_TERMINAL_START_FAILED: '原生終端啟動失敗，請檢查容器執行環境與插件診斷紀錄。',
      REPLY_TIMEOUT: '等待模型回覆逾時，請確認模型服務可用後重試。',
      SERVER_UNREACHABLE: '無法連上 Omnigent Server，請檢查網路與 Server 狀態。',
      SESSION_STOP_FAILED: '測試對話未能確認停止，尚未通過驗證。'
    };
    const detail = reasons[receipt.reason] || '對話驗證未完成，請檢查原生引擎與模型服務後重試。';
    return detail + (receipt.cleanup === 'stop_failed' && receipt.reason !== 'SESSION_STOP_FAILED'
      ? ' 另外，測試對話未能確認停止。' : '');
  }
  function Page() {
    const state = useRuntime();
    const [pair, setPair] = useState(false), [catalog, setCatalog] = useState(null);
    const [engine, setEngine] = useState(''), [model, setModel] = useState(''), [error, setError] = useState('');
    useEffect(() => { if (!state?.enabled) setPair(false); }, [state?.enabled]);
    const engineData = catalog?.engines?.[engine];
    async function act(path, data) {
      setError('');
      try { await request(path, data); await refresh(); }
      catch (_) { setError('操作未完成，請檢查登入與插件狀態後再試。'); }
    }
    async function loadCatalog() {
      setError('');
      try {
        const result = await request('/catalog');
        if (!result.ok) throw new Error();
        setCatalog(result.data);
        const available = state?.installedEngines || [];
        if (!engine && available[0]) setEngine(available[0]);
      } catch (_) { setError('目前無法取得模型與模組，請先完成配對。'); }
    }
    const server = safeServer(state?.server);
    const chat = verifiedChatURL(state, engine, model);
    return h('section', {className: 'og-root og-page'},
      h('div', {className: 'og-eyebrow'}, 'WOOWTECH · HERMES PLUGIN'), h('h1', null, 'Omnigent 設定'),
      h('p', null, '管理 Server 配對、主機連線、這個環境已有的引擎、模型與實際對話驗證。'),
      h('div', {className: 'og-card'}, h('h2', null, '連線與配對'),
        h('span', {className: 'og-chip', role: 'status'}, phases[state?.phase] || '正在讀取狀態'),
        state?.operation?.error || state?.issue ? h('p', {role: 'alert'}, '操作回報：' + (state.issue || state.operation.error)) : null,
        failure ? h('p', {role: 'alert'}, failure) : null,
        h('p', null, state?.host?.connected ? '主機在線' : '等待主機連線'),
        h('div', {className: 'og-actions'},
          button('重新配對', async () => {await act('/pairing/start', {}); dismissed = true; setPair(true);}, busy),
          button('更新狀態', refresh, busy, true),
          button('驗證全部引擎', () => act('/verify', {}), busy || !state?.loginConfirmed, true),
          button('取消連結', () => {if (window.confirm('取消此 Hermes 對 Omnigent 的連結？重新連結會再要求登入資訊。')) act('/disconnect', {});}, busy || !state?.server, true)),
      server ? h('a', {href: server, target: '_blank', rel: 'noopener noreferrer', className: 'og-link'}, '開啟 Omnigent') : null),
      h('div', {className: 'og-card'}, h('h2', null, '引擎、模型與對話'),
        h('p', null, '只連結當下存在的引擎；驗證收到回覆並停止測試對話後，才算完成。'),
        button('取得可用模型', loadCatalog, busy || !state?.loginConfirmed, true),
        catalog ? h('div', {className: 'og-selectors'},
          h('label', null, '原生引擎／模組', h('select', {value: engine, onChange: e => {setEngine(e.target.value);setModel('');}},
            ...(state?.installedEngines || []).map(key => h('option', {key, value: key}, key + ' · ' + (catalog.engines[key]?.agentId ? '模組可用' : '等待模組'))))),
          h('label', null, '模型', h('select', {value: model, onChange: e => setModel(e.target.value)},
            h('option', {value: ''}, '沿用原生引擎設定'), ...(engineData?.models || []).map(item => h('option', {key: item.id, value: item.id}, item.displayName || item.id)))),
          button('驗證所選模型', () => act('/verify', {engine, ...(model ? {model} : {})}), busy || !engine),
          chat ? h('a', {href: chat, target: '_blank', rel: 'noopener noreferrer', className: 'og-link'}, '開啟已驗證的對話') : null) : null,
        h('div', {className: 'og-engines'}, ...(state?.installedEngines || []).map(key => h('div', {className: 'og-engine', key},
          h('strong', null, key), h('span', null, state.verification?.[key]?.ok ? '實際對話已驗證' : '尚未通過對話驗證'),
          verificationDetails(state.verification?.[key]) ? h('p', {role: 'alert'}, verificationDetails(state.verification[key])) : null)))),
      error ? h('p', {role: 'alert', className: 'og-error'}, error) : null,
      pair && state?.enabled ? h('div', {className: 'og-modal'}, h(PairForm, {close: () => setPair(false)})) : null);
  }
  function Overlay() {
    const state = useRuntime();
    const [, setCount] = useState(0);
    if (!state?.pairingRequired || dismissed || busy) return null;
    return h('div', {className: 'og-root og-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': '配對 Omnigent'},
      h(PairForm, {close: () => {dismissed = true;setCount(x => x + 1);emit();}}));
  }
  registry.register(name, Page);
  registry.registerSlot(name, 'overlay', Overlay);
})();
