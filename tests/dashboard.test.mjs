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
