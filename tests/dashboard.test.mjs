import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../dashboard/dist/index.js', import.meta.url), 'utf8');
test('registers the native Dashboard tab and overlay in the actual SDK argument order', () => {
  const tabs = [], slots = [];
  const window = {__HERMES_BASE_PATH__: '/hermes', __HERMES_PLUGIN_SDK__: {React: {}, hooks: {}, fetchJSON() {}},
    __HERMES_PLUGINS__: {register: (...args) => tabs.push(args), registerSlot: (...args) => slots.push(args)}};
  vm.runInNewContext(source, {window, URL, setInterval, clearInterval});
  assert.equal(tabs[0][0], 'hermes-omnigent');
  assert.deepEqual(slots[0].slice(0, 2), ['hermes-omnigent', 'overlay']);
  assert.equal(typeof tabs[0][1], 'function');
  assert.equal(typeof slots[0][2], 'function');
});

test('declares an Omnigent settings page that Hermes can open from Plugins', () => {
  const manifest = JSON.parse(fs.readFileSync(new URL('../dashboard/manifest.json', import.meta.url), 'utf8'));
  assert.equal(manifest.name, 'hermes-omnigent');
  assert.equal(manifest.label, 'Omnigent 設定');
  assert.equal(manifest.tab.path, '/omnigent');
  assert.equal(manifest.tab.position, 'after:plugins');
  const pluginVersion = fs.readFileSync(new URL('../plugin.yaml', import.meta.url), 'utf8')
    .match(/^version:\s*"([^"]+)"$/m)?.[1];
  assert.equal(manifest.version, '0.1.0-rc.14');
  assert.equal(manifest.version, pluginVersion);
  assert.match(source, /h\('h1', null, 'Omnigent 設定'\)/);
});

test('pairing secrets use masked inputs and stay out of browser persistence and error logs', () => {
  assert.match(source, /type: 'password'/);
  assert.match(source, /setPassword\(''\)/);
  for (const forbidden of [/localStorage/, /sessionStorage/, /console\./, /__HERMES_SESSION_TOKEN__/, /innerHTML/]) {
    assert.doesNotMatch(source, forbidden);
  }
  // Host fetchJSON adds the base path and auth. The plugin must not repeat it.
  assert.match(source, /const endpoint = '\/api\/plugins\/'/);
  assert.doesNotMatch(source, /__HERMES_BASE_PATH__/);
});

test('chat link binds to the selected verified model, host, engine version and server', () => {
  const window = {__HERMES_PLUGIN_SDK__: {React: {}, hooks: {}, fetchJSON() {}},
    __HERMES_PLUGINS__: {register() {}, registerSlot() {}}};
  vm.runInNewContext(source.replace('  function PairForm(', '  window.testChatURL = verifiedChatURL;\n  function PairForm('),
    {window, URL, setInterval, clearInterval});
  const state = {phase: 'ready', server: 'https://server.example', host: {id: 'own-host', connected: true},
    engines: {hermes: {version: '0.19.0'}}, verification: {hermes: {ok: true, reply: 'OMNIGENT_CONNECTION_OK',
      cleanup: 'stopped', model: 'selected-model', hostId: 'own-host', server: 'https://server.example',
      cliVersion: '0.19.0', sessionId: 'own-session', url: 'https://server.example/c/own-session'}}};
  assert.equal(window.testChatURL(state, 'hermes', 'selected-model'), state.verification.hermes.url);
  assert.equal(window.testChatURL(state, 'hermes', 'other-model'), null);
  assert.equal(window.testChatURL(state, 'hermes', ''), null);
  for (const replacement of [{hostId: 'other-host'}, {ok: false}, {cleanup: 'stop_failed'},
    {cliVersion: 'old'}, {url: 'https://other.example/c/own-session'}, {url: 'javascript:alert(1)'},
    {url: 'https://server.example/c/other-session'}, {url: 'https://server.example/c/own-session?token=secret'}]) {
    const modified = {...state, verification: {hermes: {...state.verification.hermes, ...replacement}}};
    assert.equal(window.testChatURL(modified, 'hermes', 'selected-model'), null);
  }
});


test('slow status requests do not overlap and polling recovers after failure', async () => {
  let calls = 0, resolve, reject;
  const window = {__HERMES_PLUGIN_SDK__: {React: {}, hooks: {}, fetchJSON() {
    calls++;
    return new Promise((yes, no) => { resolve = yes; reject = no; });
  }}, __HERMES_PLUGINS__: {register() {}, registerSlot() {}}};
  vm.runInNewContext(source.replace('  function PairForm(', '  window.refreshForTest = refresh;\n  function PairForm('),
    {window, URL, setInterval, clearInterval});
  const first = window.refreshForTest();
  const second = window.refreshForTest();
  assert.equal(calls, 1);
  resolve({pairingRequired: true});
  await Promise.all([first, second]);
  const failed = window.refreshForTest();
  assert.equal(calls, 2);
  reject(new Error('temporary network failure'));
  await failed;
  const recovered = window.refreshForTest();
  assert.equal(calls, 3);
  resolve({pairingRequired: false});
  await recovered;
});

test('verification shows the primary failure and cleanup failure without raw error text', () => {
  const window = {__HERMES_PLUGIN_SDK__: {React: {}, hooks: {}, fetchJSON() {}},
    __HERMES_PLUGINS__: {register() {}, registerSlot() {}}};
  vm.runInNewContext(source.replace('  function Page()', '  window.detailsForTest = verificationDetails;\n  function Page()'),
    {window, URL, setInterval, clearInterval});
  const details = window.detailsForTest({ok: false, reason: 'NATIVE_TERMINAL_START_FAILED', cleanup: 'stop_failed'});
  assert.match(details, /原生終端啟動失敗/);
  assert.match(details, /另外.*未能確認停止/);
  assert.doesNotMatch(window.detailsForTest({ok: false, reason: 'password=private'}), /password|private/);
  assert.equal(window.detailsForTest({ok: true}), '');
});
