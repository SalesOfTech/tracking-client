const {test} = require('node:test');
const assert = require('node:assert/strict');
const {validate} = require('../protocol.cjs');
const fs = require('node:fs');
const path = require('node:path');

test('renderer may only issue narrow, typed commands', () => {
  for (const action of ['status', 'check', 'repair', 'retry', 'resume', 'ready']) assert.equal(validate(action).action, action);
  for (const input of [{paused: false}, {policy: {}}, {enabled: true}]) assert.throws(() => validate('resume', input));
  assert.throws(() => validate('pause'));
  assert.throws(() => validate('exec', {command: 'arbitrary'}));
  assert.throws(() => validate('open', {target: 'https://untrusted.example'}));
  assert.throws(() => validate('preferences', {language: 'invalid'}));
  assert.throws(() => validate('preferences', {theme: 'url()'}));
  assert.throws(() => validate('status', {secret: 'x'}));
  assert.throws(() => validate('enroll', {code: 'a'.repeat(32), key: 'invalid'}));
  assert.equal(validate('enroll', {code: 'a'.repeat(32), key: 'b'.repeat(64)}).action, 'enroll');
});

function sourceModule(filename, globals = {}) {
  const ts = require('typescript'), vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../src', filename), 'utf8');
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const context = vm.createContext({exports: {}, URLSearchParams, setTimeout, ...globals});
  vm.runInContext(code, context);
  return context.exports;
}

test('preview resumes only an explicitly paused enrolled desktop; status never auto-resumes', async () => {
  const bridge = sourceModule('bridge.ts', {window: {}, location: {search: '?preview=1&paused=1'}});
  const before = await bridge.invoke('status');
  assert.equal(before.collection, 'paused_local');
  assert.equal((await bridge.invoke('status')).collection, 'paused_local');
  await assert.rejects(bridge.invoke('resume', {policy: {tracking: true}}), /invalid_request/);
  const after = await bridge.invoke('resume');
  assert.equal(after.collection, 'recording');
  assert.equal(JSON.stringify({...after, collection: before.collection}), JSON.stringify(before));
  await assert.rejects(bridge.invoke('resume'), /invalid_request/);
  for (const search of ['?preview=1', '?preview=1&paused=1&enroll=1', '?preview=1&paused=1&mode=installer']) {
    const other = sourceModule('bridge.ts', {window: {}, location: {search}});
    const state = JSON.stringify(await other.invoke('status'));
    await assert.rejects(other.invoke('resume'), /invalid_request/);
    assert.equal(JSON.stringify(await other.invoke('status')), state);
  }
});

test('resume caption has four languages and the action is conditional on local pause', () => {
  const locale = sourceModule('locale.ts');
  for (const [language, caption] of Object.entries({en: 'Resume collection', ru: 'Возобновить сбор', cs: 'Obnovit sběr', uz: "Yig'ishni davom ettirish"})) {
    assert.equal(locale.text(language, 'resume'), caption);
  }
  const ui = fs.readFileSync(path.join(__dirname, '../src/main.tsx'), 'utf8');
  assert.match(ui, /view\.collection === 'paused_local' && <Button onPress=\{\(\) => void act\('resume'\)\} isDisabled=\{busy\}/);
  assert.ok(!ui.includes("act('pause')"));
});
test('Electron has a sandbox, isolated preload and no remote content or arbitrary shell API', () => {
  const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  for (const entry of ['sandbox: true', 'contextIsolation: true', 'nodeIntegration: false', 'webSecurity: true', "action: 'deny'", 'event.senderFrame === window.webContents.mainFrame']) assert.ok(source.includes(entry), entry);
  assert.ok(!source.includes('shell.openExternal'));
  assert.ok(!source.includes('loadURL('));
  const preload = fs.readFileSync(path.join(__dirname, '../preload.cjs'), 'utf8');
  assert.ok(!preload.includes('ipcRenderer.send('));
});
