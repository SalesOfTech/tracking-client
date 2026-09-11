const {test} = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(platform) {
  let ready, options, window;
  const input = new EventEmitter();
  input.setEncoding = () => {};
  const app = new EventEmitter();
  app.setName = app.setAppUserModelId = app.quit = () => {};
  app.whenReady = () => ({then: callback => {ready = callback;}});
  const nativeTheme = new EventEmitter();
  nativeTheme.shouldUseDarkColors = true;
  class BrowserWindow extends EventEmitter {
    constructor(opts) {
      super(); options = opts; window = this;
      this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = () => {};
      this.webContents.session = {setPermissionRequestHandler() {}, setPermissionCheckHandler() {}};
    }
    loadFile() {}
    isDestroyed() {return false;}
    setBackgroundColor(color) {this.background = color;}
  }
  const electron = {app, BrowserWindow, nativeTheme, ipcMain: {handle() {}}, Menu: {setApplicationMenu() {}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8'), {
    require: name => name === 'electron' ? electron : name === 'node:fs' ? {realpathSync: {native: p => p}, createReadStream: () => input} : name === './protocol.cjs' ? require('../protocol.cjs') : require(name),
    __dirname: path.join(__dirname, '..'), process: {platform, env: {}, stdin: input, on() {}, stdout: {write() {}}},
    Buffer, setTimeout, clearTimeout,
  });
  ready();
  return {nativeTheme, options, window};
}

test('desktop follows OS appearance and gives macOS/Linux native window controls', () => {
  for (const platform of ['win32', 'darwin', 'linux']) {
    const state = fixture(platform);
    assert.equal(state.nativeTheme.themeSource, 'system');
    assert.equal(state.options.frame, platform !== 'win32');
    assert.equal(state.options.backgroundColor, '#191b1f');
    state.nativeTheme.shouldUseDarkColors = false;
    state.nativeTheme.emit('updated');
    assert.equal(state.window.background, '#ffffff');
    state.nativeTheme.shouldUseDarkColors = true;
    state.nativeTheme.emit('updated');
    assert.equal(state.window.background, '#191b1f');
  }
});
