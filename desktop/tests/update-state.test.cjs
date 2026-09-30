const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const {BROWSER_PAGES} = require('../protocol.cjs');
const NOW = 1800000000;

function sourceModule(file, globals = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const context = vm.createContext({exports: {}, URLSearchParams, setTimeout, Date: class extends Date {static now() {return NOW * 1000;}}, require: name => {assert.equal(name, '../package.json'); return require('../package.json');}, ...globals});
  vm.runInContext(code, context);
  return context.exports;
}
const {updateState} = sourceModule('update-state.ts');
const {connectionState} = sourceModule('connection-state.ts');
const {text} = sourceModule('locale.ts');
const view = {mode: 'desktop', enrolled: true, updateAvailable: true, updateChecking: false, update: 'active', updateCheckedAt: NOW - 60};

for (const [state, label, busy] of [
  ['checking', 'checking', true], ['active', 'installed', false], ['installed', 'installed', false],
  ['downloading', 'downloading', true], ['rolled_back', 'rollback', false], ['error', 'updateError', false], ['registration', 'updateNotChecked', false],
]) {
  test(`update snapshot: ${state}`, () => {
    const result = updateState({...view, update: state, updateChecking: state === 'checking'}, false, false, NOW);
    assert.equal(result.label, label);
    assert.equal(result.busy, busy);
    assert.equal(result.disabled, busy);
    assert.equal(result.checkedAt, NOW - 60);
  });
}

test('pending request and updater flags override cached success without affecting connection state', () => {
  assert.equal(updateState(view, true, false, NOW).busy, true);
  assert.equal(updateState(view, true, false, NOW).label, 'checking');
  assert.equal(updateState({...view, updateChecking: true}, false, false, NOW).disabled, true);
  assert.equal(updateState({...view, updateChecking: true}, false, true, NOW).label, 'checking');
  assert.equal(updateState({...view, update: 'downloading'}, true, false, NOW).label, 'downloading');
  assert.equal(updateState({...view, busy: true}, false, false, NOW).disabled, false, 'Connection work is independent of updater work');
  const connection = {...view, collection: 'recording', browsers: [{connected: true}], receipt: {hostname: 'demo.kommo.com', timestamp: NOW - 60, end_timestamp: NOW - 10, confirmed_at: NOW - 5}};
  for (const update of ['checking', 'downloading', 'error']) assert.equal(connectionState({...connection, update, updateChecking: true}, false, false, NOW).tone, 'success');
});

test('unavailable, unenrolled and installer snapshots cannot request an update', () => {
  for (const patch of [{enrolled: false}, {mode: 'installer'}, {updateAvailable: false}, {updateAvailable: undefined}]) {
    assert.equal(updateState({...view, ...patch}, false, false, NOW).disabled, true);
  }
  assert.equal(updateState({...view, enrolled: false}, false, false, NOW).label, 'updateRegistration');
  assert.equal(updateState({...view, updateAvailable: undefined}, false, false, NOW).label, 'updateUnavailable');
});

test('stale registration and unknown initial checking do not block an enrolled available updater', () => {
  for (const update of ['registration', 'checking']) {
    const result = updateState({...view, update, updateChecking: false}, false, false, NOW);
    assert.equal(result.disabled, false);
    assert.equal(result.busy, false);
    assert.equal(result.label, 'updateNotChecked');
    assert.equal(updateState({...view, update, updateChecking: true}, false, false, NOW).disabled, true);
  }
  assert.equal(updateState({...view, update: 'checking', updateChecking: undefined}, false, false, NOW).busy, true);
  assert.equal(updateState({...view, update: 'registration', updateAvailable: false}, false, false, NOW).disabled, true);
  assert.equal(updateState({...view, update: 'registration', enrolled: false}, false, false, NOW).disabled, true);
  assert.equal(updateState({...view, update: 'downloading', updateChecking: false}, false, false, NOW).disabled, true);
});

test('update failure can be retried and cannot claim the cached installed status', () => {
  const failed = updateState(view, false, true, NOW);
  assert.equal(failed.label, 'updateCheckFailed');
  assert.equal(failed.failed, true);
  assert.equal(failed.disabled, false);
  assert.equal(updateState({...view, update: undefined}, false, false, NOW).label, 'updateNotChecked');
});

test('optional update check time is displayed only when valid', () => {
  for (const value of [undefined, 0, -1, NaN, Infinity, String(NOW), NOW + 60]) assert.equal(updateState({...view, updateCheckedAt: value}, false, false, NOW).checkedAt, undefined);
  assert.equal(updateState({...view, update: 'error'}, false, false, NOW).checkedAt, NOW - 60);
});

test('updater and clipboard messages cover all four languages', () => {
  for (const language of ['en', 'ru', 'cs', 'uz']) {
    for (const key of ['update', 'checkUpdate', 'updateCheckedAt', 'updateRegistration', 'updateUnavailable', 'updateNotChecked', 'updateCheckFailed', 'checking', 'downloading', 'installed', 'rollback', 'updateError', 'copyAddress', 'addressCopied', 'copyFailed', 'copyBrowserInstructions']) assert.ok(text(language, key)?.trim(), `${language}/${key}`);
    assert.ok(text(language, 'copyBrowserInstructions').includes('Enter'));
  }
});

function preview(search = '', globals = {}) {
  return sourceModule('bridge.ts', {window: {}, location: {search: `?preview=1&lang=en${search}`}, ...globals});
}

test('manual preview update check is independent, pending, rejects duplicate requests, then completes', async () => {
  let finish;
  const bridge = preview('', {setTimeout: callback => {finish = callback;}});
  const before = await bridge.invoke('status');
  const request = bridge.invoke('check-update', {});
  const pending = await bridge.invoke('status');
  assert.equal(pending.updateChecking, true);
  assert.equal(pending.busy, before.busy);
  assert.equal(pending.message, before.message);
  assert.equal(pending.error, before.error);
  assert.equal(connectionState(pending, false, false, NOW).tone, 'success');
  await assert.rejects(bridge.invoke('check-update', {}), /invalid_request/);
  finish();
  const after = await request;
  assert.equal(after.updateChecking, false);
  assert.equal(after.updateCheckedAt, NOW);
  assert.equal(after.update, 'active');
  assert.equal(JSON.stringify(after.receipt), JSON.stringify(before.receipt));
});

test('preview update command fails closed without availability and rejects extra input', async () => {
  for (const query of ['&enroll=1', '&mode=installer', '&updateAvailable=0', '&updateChecking=1', '&update=checking', '&update=downloading']) await assert.rejects(preview(query).invoke('check-update', {}), /invalid_request/);
  await assert.rejects(preview().invoke('check-update', {url: 'https://untrusted.example'}), /invalid_request/);
});

test('preview updater error stays in update state only', async () => {
  let finish;
  const bridge = preview('&updateResult=error', {setTimeout: callback => {finish = callback;}});
  const request = bridge.invoke('check-update');
  finish();
  const state = await request;
  assert.equal(state.update, 'error');
  assert.equal(state.error, '');
  assert.equal(connectionState(state, false, false, NOW).tone, 'success');
});

test('manual preview check bypasses stale registration and explicit nonbusy initial checking', async () => {
  for (const query of ['&update=registration', '&update=checking&updateChecking=0']) {
    let finish;
    const bridge = preview(query, {setTimeout: callback => {finish = callback;}});
    const request = bridge.invoke('check-update', {});
    assert.equal((await bridge.invoke('status')).updateChecking, true);
    finish();
    assert.equal((await request).update, 'active');
  }
});

test('preview clipboard copies every fixed address without a browser or clipboard API', async () => {
  const bridge = preview('', {navigator: {get clipboard() {throw Error('must_not_use_browser_clipboard');}}});
  assert.deepEqual(JSON.parse(JSON.stringify(bridge.browserPages)), BROWSER_PAGES);
  const before = JSON.stringify(await bridge.invoke('status'));
  for (const [browser, address] of Object.entries(BROWSER_PAGES)) {
    assert.equal(JSON.stringify(await bridge.invoke('copy-browser-page', {browser})), '{"copied":true}');
    assert.equal(bridge.previewClipboard, address);
    assert.equal(JSON.stringify(await bridge.invoke('status')), before);
  }
  for (const input of [{}, {browser: 'Safari'}, {browser: 'toString'}, {browser: '__proto__'}, {browser: 'Chrome', url: 'https://example.test'}]) await assert.rejects(bridge.invoke('copy-browser-page', input), /invalid_request/);
});

test('native clipboard and update requests delegate to the bridge without using the preview mock', async () => {
  const calls = [];
  const bridge = preview('', {window: {tracking: {invoke: async (action, input) => {calls.push({action, input}); return action === 'copy-browser-page' ? {copied: true} : view;}}}});
  assert.deepEqual(await bridge.invoke('copy-browser-page', {browser: 'Firefox'}), {copied: true});
  await bridge.invoke('check-update', {});
  assert.deepEqual(calls, [{action: 'copy-browser-page', input: {browser: 'Firefox'}}, {action: 'check-update', input: {}}]);
  assert.equal(bridge.previewClipboard, '');
});
