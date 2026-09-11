const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const {validate} = require('../protocol.cjs');

function fixture(health = true) {
  const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  const phases = [], writes = [], handlers = {}, parentInput = new EventEmitter();
  const context = vm.createContext({health, parentInput, validate,
    process: {env: {SOFT_TRACKING_HEALTH: '/fixture-health'}, stdout: {write: line => writes.push(JSON.parse(line))}},
    require: name => {assert.equal(name, 'node:fs'); return {appendFileSync: (_path, line) => phases.push(line.trim())};},
    setTimeout: () => 1, clearTimeout: () => {}, setImmediate: fn => fn(),
    app: {quit: () => {}, exit: () => {throw Error('invalid response');}},
    nativeTheme: {}, authorized: event => event.allowed === true,
    ipcMain: {handle: (name, fn) => {handlers[name] = fn;}}, Buffer,
  });
  const phase = source.match(/function healthPhase\(phase\) \{[\s\S]*?^\}/m)[0];
  const request = source.match(/function request\(action, input = \{\}\) \{[\s\S]*?^\}/m)[0];
  const response = source.slice(source.indexOf("parentInput.on('data'"), source.indexOf("parentInput.on('end'"));
  const command = source.slice(source.indexOf("ipcMain.handle('tracking:command'"), source.indexOf("ipcMain.handle('tracking:window'"));
  vm.runInContext('let sequence=0, buffer="", closing=false; const pending=new Map();\n' + phase + request + response + command, context);
  return {phases, writes, command: handlers['tracking:command'], respond: (ok, data = {}) => {
    parentInput.emit('data', JSON.stringify({id: writes.at(-1).id, ok, data, error: 'connect_failed'}) + '\n');
  }};
}

test('health phases distinguish status/ready requests, responses and denied IPC without payloads', async () => {
  const f = fixture();
  for (const action of ['status', 'ready']) {
    const result = f.command({allowed: true}, action, {});
    f.respond(true, {privateValue: 'must-not-enter-diagnostics'});
    await result;
  }
  const failed = f.command({allowed: true}, 'status', {});
  f.respond(false);
  await assert.rejects(failed, /connect_failed/);
  await assert.rejects(f.command({allowed: false}, 'status', {}), /unauthorized/);
  assert.deepEqual(f.phases, ['request-status', 'response-status-ok', 'request-ready', 'response-ready-ok',
    'request-status', 'response-status-error', 'ipc-unauthorized']);
  assert.equal(f.writes.length, 3);
});

test('ordinary operation does not write health phases', async () => {
  const f = fixture(false);
  const result = f.command({allowed: true}, 'status', {});
  f.respond(true);
  await result;
  await assert.rejects(f.command({allowed: false}, 'ready', {}), /unauthorized/);
  assert.deepEqual(f.phases, []);
});
