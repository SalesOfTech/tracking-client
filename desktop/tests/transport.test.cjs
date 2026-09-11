const {test} = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function start(platform) {
  const input = new EventEmitter();
  input.setEncoding = value => assert.equal(value, 'utf8');
  const app = new EventEmitter();
  app.setName = app.setAppUserModelId = () => {};
  app.whenReady = () => new Promise(() => {});
  let quits = 0;
  app.quit = () => {quits++;};
  const writes = [], reads = [];
  const process = {platform, env: {}, on() {}, stdout: {write: line => writes.push(JSON.parse(line))}};
  Object.defineProperty(process, 'stdin', {get() {
    assert.notEqual(platform, 'win32', 'Windows must not read Electron\'s EOF-only stdin');
    return input;
  }});
  const requireMock = name => {
    if (name === 'electron') return {app, nativeTheme: new EventEmitter(), ipcMain: {handle() {}}};
    if (name === 'node:fs') return {realpathSync: {native: filename => filename}, createReadStream: (...args) => {reads.push(args); return input;}};
    if (name === './protocol.cjs') return require('../protocol.cjs');
    return require(name);
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8'), {
    require: requireMock, __dirname: path.join(__dirname, '..'), process, Buffer, setTimeout, clearTimeout,
  });
  return {app, input, writes, reads, quits: () => quits};
}

test('Windows reads the inherited pipe and releases it through one closing notification', () => {
  const state = start('win32');
  assert.equal(JSON.stringify(state.reads), JSON.stringify([[null, {fd: 0, autoClose: false}]]));
  assert.equal(state.quits(), 0);
  state.app.emit('before-quit');
  assert.deepEqual(state.writes, [], 'An attempt to quit can still be cancelled by a busy installer');
  state.app.emit('will-quit');
  state.app.emit('will-quit');
  assert.deepEqual(state.writes, [{event: 'closing'}]);
  state.input.readableEnded = true;
  state.input.emit('end');
  assert.equal(state.quits(), 1);
});

test('POSIX keeps native stdio and parent disconnect does not send a closing notification', () => {
  const state = start('linux');
  assert.deepEqual(state.reads, []);
  state.input.readableEnded = true;
  state.input.emit('end');
  state.app.emit('will-quit');
  assert.deepEqual(state.writes, []);
  assert.equal(state.quits(), 1);
});
