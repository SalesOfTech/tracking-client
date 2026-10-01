const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');
function sourceModule(file, globals = {}) {
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src', file), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
  const context = vm.createContext({exports: {}, URLSearchParams, setTimeout, require: () => require('../package.json'), ...globals});
  vm.runInContext(code, context);
  return context.exports;
}
const {text} = sourceModule('locale.ts');

test('stop and uninstall are unavailable in browser preview, even with forged capability parameters', async () => {
  for (const query of ['', '&canUninstall=1&canManage=1', '&mode=installer', '&state=busy']) {
    const bridge = sourceModule('bridge.ts', {window: {}, location: {search: '?preview=1' + query}});
    const view = await bridge.invoke('status');
    assert.equal(view.canUninstall, false);
    assert.equal(view.canManage, false);
    assert.equal(view.admin.admin_required, false);
    for (const action of ['stop-agent', 'uninstall-agent']) {
      await assert.rejects(bridge.invoke(action, {}), /invalid_request/);
    }
  }
});

test('native lifecycle commands retain empty payloads and backend capability fields', async () => {
  const calls = [];
  const state = {canUninstall: true, uninstalling: true, busy: true};
  const bridge = sourceModule('bridge.ts', {window: {tracking: {invoke: async (action, input) => {calls.push({action, input}); return state;}}}, location: {search: ''}});
  for (const action of ['stop-agent', 'uninstall-agent']) {
    assert.equal(await bridge.invoke(action, {}), state);
  }
  assert.deepEqual(calls, [{action: 'stop-agent', input: {}}, {action: 'uninstall-agent', input: {}}]);
});

test('four languages have ordinary lifecycle labels and matching guide instructions', () => {
  const guide = JSON.parse(fs.readFileSync(path.join(root, 'agent/agent_tracker/assets/guide.json'), 'utf8'));
  for (const language of ['en', 'ru', 'cs', 'uz']) {
    for (const key of ['stopAgent', 'stopTitle', 'stopConfirm', 'stopNotice', 'stopCancelled', 'stoppingAgent', 'stopFailed', 'controlBusy', 'uninstallAgent', 'uninstallOpening', 'uninstallStarted', 'uninstallRequested', 'uninstallCancelled', 'uninstallFailed', 'uninstallUnavailable']) {
      const value = text(language, key);
      assert.ok(value?.trim());
      assert.doesNotMatch(value, /admin|správce|администратор|runas|pkexec|osascript/i);
    }
    const permissions = guide[language].sections.find(section => section.id === 'permissions');
    assert.ok(permissions.steps[3].includes(text(language, 'stopAgent')));
    assert.ok(permissions.steps[3].includes(text(language, 'stopConfirm')));
    assert.ok(permissions.steps[4].includes(text(language, 'uninstallAgent')));
  }
});

test('Qt stop is behind one ordinary confirmation; uninstall invokes native confirmation directly', () => {
  const qml = fs.readFileSync(path.join(root, 'agent/agent_tracker/ui/quick/Main.qml'), 'utf8');
  assert.match(qml, /objectName: "stopAgent"[^\n]+onClicked: stopDialog.open\(\)/);
  assert.match(qml, /objectName: "confirmStop"[^\n]+stopDialog.close\(\); bridge.requestStop\(\);/);
  assert.equal((qml.match(/bridge\.requestStop\(/g) || []).length, 1);
  assert.match(qml, /onOpened: cancelStop.forceActiveFocus\(\)/);
  assert.match(qml, /objectName: "uninstallAgent"[^\n]+window.vm.canUninstall === true[^\n]+onClicked: bridge.requestUninstall\(\)/);
  assert.equal((qml.match(/bridge\.requestUninstall\(/g) || []).length, 1);
  assert.match(qml, /objectName: "uninstallStatus"[^\n]+window.vm.uninstalling === true/);
});
