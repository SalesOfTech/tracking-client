const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const NOW = 1800000000;
function sourceModule(file, globals = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const context = vm.createContext({exports: {}, URLSearchParams, setTimeout, require: name => {assert.equal(name, '../package.json'); return require('../package.json');}, ...globals});
  vm.runInContext(code, context);
  return context.exports;
}
const {connectionState, receiptState, viewIsBusy} = sourceModule('connection-state.ts');
const {text} = sourceModule('locale.ts');
const receipt = {hostname: 'demo.kommo.com', timestamp: NOW - 90, end_timestamp: NOW - 15, confirmed_at: NOW - 5};
const browser = {family: 'Chrome', connected: true, version: 'test', last_seen: NOW};
const healthy = {mode: 'desktop', language: 'en', theme: 'system', version: 'test', code: '', busy: false, phase: 'waiting', error: '', message: '', enrolled: true, collection: 'recording', pending: 0, rejected: 0, browsers: [browser], receipt};
const resolve = (patch = {}, busy = false, failed = false) => connectionState({...healthy, ...patch}, busy, failed, NOW);

const priorities = [
  ['healthy', {}, false, false, 'success', 'connectionHealthy', 'check'],
  ['local operation before all failures', {error: 'offline', rejected: 3, collection: 'paused_local', browsers: [], receipt: {}}, true, true, 'accent', 'connectionChecking', 'check'],
  ['backend busy before a cached error', {busy: true, errorCode: 'ST-TEST'}, false, false, 'accent', 'connectionChecking', 'check'],
  ['transport failure before a healthy cached receipt', {}, false, true, 'danger', 'connectionProblem', 'check'],
  ['action error before pause', {error: 'failed', collection: 'paused_local'}, false, false, 'danger', 'connectionProblem', 'check'],
  ['support code without other error flags', {errorCode: 'ST-TEST'}, false, false, 'danger', 'connectionProblem', 'check'],
  ['error text without other error flags', {errorText: 'failed'}, false, false, 'danger', 'connectionProblem', 'check'],
  ['failed phase without error flags', {phase: 'failed'}, false, false, 'danger', 'connectionProblem', 'check'],
  ['delivery failure before rejected events', {deliveryError: true, rejected: 2}, false, false, 'danger', 'connectionProblem', 'check'],
  ['failed check without error flags', {message: 'check_offline'}, false, false, 'danger', 'connectionProblem', 'check'],
  ['rejected events before browser failure', {rejected: 2, browsers: []}, false, false, 'danger', 'connectionProblem', 'retry'],
  ['activation before a cached healthy receipt', {enrolled: false}, false, false, 'danger', 'enterCode', 'check'],
  ['pause before browser failure', {collection: 'paused_local', browsers: []}, false, false, 'danger', 'stopped', 'resume'],
  ['policy stop is not locally resumable', {collection: 'disabled_policy'}, false, false, 'danger', 'collectionUnavailable', 'check'],
  ['unknown collection is not healthy', {collection: undefined}, false, false, 'danger', 'collectionUnavailable', 'check'],
  ['browser error even with another healthy browser', {browsers: [browser, {...browser, family: 'Edge', error: 'storage'}]}, false, false, 'danger', 'connectionProblem', 'browsers'],
  ['browser missing before missing receipt', {browsers: [], receipt: {}}, false, false, 'danger', 'browserDisconnected', 'browsers'],
  ['disconnected browser', {browsers: [{...browser, connected: false}]}, false, false, 'danger', 'browserDisconnected', 'browsers'],
  ['missing browser snapshot', {browsers: undefined}, false, false, 'danger', 'browserDisconnected', 'browsers'],
  ['truthy non-boolean browser flag is not trusted', {browsers: [{...browser, connected: 'true'}]}, false, false, 'danger', 'browserDisconnected', 'browsers'],
  ['old disconnected browser does not invalidate the active one', {browsers: [browser, {...browser, family: 'Edge', connected: false}]}, false, false, 'success', 'connectionHealthy', 'check'],
  ['check pending without busy is not endless loading', {message: 'check_pending'}, false, false, 'danger', 'connectionProblem', 'check'],
  ['legacy cleanup warning is not full success', {message: 'legacy_cleanup_warning'}, false, false, 'danger', 'connectionProblem', 'check'],
  ['successful check cannot override missing receipt', {message: 'check_server_ok', receipt: {}}, false, false, 'danger', 'awaitingSession', 'check'],
  ['connected message cannot override invalid receipt', {message: 'connected', receipt: {end_timestamp: NOW - 5}}, false, false, 'danger', 'invalidReceipt', 'check'],
  ['changed employee cannot inherit success', {message: 'employee_changed', receipt: {}}, false, false, 'danger', 'awaitingSession', 'check'],
  ['normal pending queue can coexist with confirmed delivery', {pending: 3}, false, false, 'success', 'connectionHealthy', 'check'],
  ['recent confirmation of an old session is still stale', {receipt: {...receipt, timestamp: NOW - 600, end_timestamp: NOW - 300, confirmed_at: NOW}}, false, false, 'danger', 'oldReceipt', 'check'],
];
for (const [name, patch, busy, failed, tone, title, action] of priorities) {
  test(`connection priority: ${name}`, () => {
    const status = resolve(patch, busy, failed);
    assert.equal(status.tone, tone);
    assert.equal(status.title, title);
    assert.equal(status.action, action);
  });
}

test('delivery and rejection errors have meaningful details without a support code', () => {
  assert.equal(resolve({deliveryError: true}).detail, 'deliveryFailed');
  assert.equal(resolve({rejected: 2}).detail, 'rejectedDetail');
  assert.notEqual(resolve({browsers: []}).detail, 'check_server_ok');
});

for (const phase of ['detecting', 'migrating', 'installing']) {
  test(`implicit loading: ${phase} without busy cannot be green`, () => {
    assert.equal(viewIsBusy({busy: false, phase}), true);
    assert.equal(resolve({phase}).tone, 'accent');
  });
}

const invalidReceipts = [
  ['missing hostname', {hostname: undefined}], ['empty hostname', {hostname: ''}],
  ['whitespace hostname', {hostname: '  '}], ['URL instead of hostname', {hostname: 'https://demo.kommo.com'}],
  ['start after end', {timestamp: NOW - 10}], ['confirmation before end', {confirmed_at: NOW - 30}],
  ['unconfirmed', {confirmed_at: undefined}], ['missing start', {timestamp: undefined}], ['missing end', {end_timestamp: undefined}],
];
for (const field of ['timestamp', 'end_timestamp', 'confirmed_at']) {
  for (const [label, value] of [['zero', 0], ['negative', -1], ['NaN', NaN], ['infinite', Infinity], ['string', String(NOW - 15)], ['null', null], ['future', NOW + 31]]) {
    invalidReceipts.push([`${field} ${label}`, {[field]: value}]);
  }
}
for (const [name, patch] of invalidReceipts) {
  test(`invalid receipt: ${name}`, () => {
    const status = resolve({receipt: {...receipt, ...patch}});
    assert.equal(status.receipt, 'invalid');
    assert.equal(status.tone, 'danger');
    assert.equal(status.title, 'invalidReceipt');
  });
}

test('receipt time boundaries and missing data are deterministic', () => {
  assert.equal(receiptState(undefined, NOW), 'missing');
  assert.equal(receiptState({}, NOW), 'missing');
  assert.equal(receiptState({...receipt, timestamp: NOW - 200, end_timestamp: NOW - 179.999}, NOW), 'recent');
  assert.equal(receiptState({...receipt, timestamp: NOW - 200, end_timestamp: NOW - 180}, NOW), 'stale');
  assert.equal(receiptState({...receipt, end_timestamp: NOW + 30, confirmed_at: NOW + 30}, NOW), 'recent');
  assert.equal(receiptState({...receipt, end_timestamp: NOW - 5.1, confirmed_at: NOW - 6}, NOW), 'recent');
  assert.equal(receiptState(receipt, NaN), 'invalid');
});

test('status ages out without mutation or a backend error', () => {
  const snapshot = structuredClone(healthy);
  assert.equal(connectionState(snapshot, false, false, NOW).tone, 'success');
  assert.equal(connectionState(snapshot, false, false, NOW + 165).tone, 'danger');
  assert.deepEqual(snapshot, healthy);
});

test('every new status and migration message exists in all four languages', () => {
  const ids = new Set(['connectionState', 'refreshStatus', 'noConfirmedSession', 'setupDetecting', 'setupMigrating', 'setupMigrationFailed']);
  for (const [, patch, busy, failed] of priorities) {
    const status = resolve(patch, busy, failed);
    ids.add(status.title); ids.add(status.detail);
  }
  for (const language of ['en', 'ru', 'cs', 'uz']) {
    for (const id of ids) assert.ok(text(language, id)?.trim(), `${language}: ${id}`);
  }
});

function preview(state, globals = {}) {
  return sourceModule('bridge.ts', {window: {}, location: {search: `?preview=1&lang=en&state=${state}`}, Date: class extends Date {static now() {return NOW * 1000;}}, ...globals});
}
for (const [name, tone, title] of [
  ['healthy', 'success', 'connectionHealthy'], ['busy', 'accent', 'connectionChecking'],
  ['browser-missing', 'danger', 'browserDisconnected'], ['failed', 'danger', 'connectionProblem'],
  ['delivery-error', 'danger', 'connectionProblem'], ['browser-error', 'danger', 'connectionProblem'],
  ['awaiting-session', 'danger', 'awaitingSession'], ['invalid-receipt', 'danger', 'invalidReceipt'],
  ['stale', 'danger', 'oldReceipt'], ['paused', 'danger', 'stopped'],
]) {
  test(`preview query state: ${name}`, async () => {
    const state = await preview(name).invoke('status');
    const result = connectionState(state, false, false, NOW);
    assert.equal(result.tone, tone); assert.equal(result.title, title);
  });
}

test('preview check exposes loading but cannot turn failed or invalid data green', async () => {
  for (const name of ['failed', 'invalid-receipt', 'stale', 'browser-missing']) {
    let complete;
    const bridge = preview(name, {setTimeout: callback => {complete = callback;}});
    const check = bridge.invoke('check');
    assert.equal(connectionState(await bridge.invoke('status'), false, false, NOW).tone, 'accent');
    complete();
    assert.equal(connectionState(await check, false, false, NOW).tone, 'danger');
  }
});

test('preview states never replace the native bridge or bypass explicit opt-in', async () => {
  const native = preview('healthy', {window: {tracking: {nativeFrame: true, invoke: async () => ({...healthy, error: 'native_failure'})}}});
  assert.equal(native.isPreview, false);
  assert.equal(resolve(await native.invoke('status')).tone, 'danger');
  const disabled = preview('healthy', {location: {search: '?state=healthy'}});
  await assert.rejects(disabled.invoke('status'), /desktop_bridge_unavailable/);
});

test('preview displays the existing package version without separate release metadata', async () => {
  assert.equal((await preview('healthy').invoke('status')).version, require('../package.json').version);
});
