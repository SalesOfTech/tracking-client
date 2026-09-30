'use strict';
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const vm = require('node:vm');

// Execute the complete main process with real protocol validation. No Electron,
// clipboard, OS commands, parent process, files or real timers are touched.
function fixture({platform = 'win32', env = {}, trayFailure = '', emptyIcon = false,
                  clipboardFailure = false} = {}) {
  const events = [], writes = [], phases = [], clipboard = [], probes = [];
  const handlers = new Map(), timers = new Map(), immediates = [];
  const windows = [], trays = [];
  let ready, timerId = 0;
  const input = new EventEmitter();
  input.setEncoding = encoding => assert.equal(encoding, 'utf8');
  input.readableEnded = false;
  const app = new EventEmitter();
  app.setName = app.setAppUserModelId = () => {};
  app.whenReady = () => ({then(callback) {ready = callback;}});
  app.quit = () => events.push('quit');
  app.exit = code => events.push(['exit', code]);
  app.isPackaged = true;
  const nativeTheme = new EventEmitter();
  nativeTheme.shouldUseDarkColors = false;
  class BrowserWindow extends EventEmitter {
    constructor(options) {
      super();
      windows.push(this);
      this.options = options;
      this.destroyed = false;
      this.visible = options.show;
      this.minimized = false;
      this.webContents = new EventEmitter();
      this.webContents.mainFrame = {url: ''};
      this.webContents.setWindowOpenHandler = callback => {this.popup = callback;};
      this.webContents.session = {
        setPermissionRequestHandler: callback => {this.permissionRequest = callback;},
        setPermissionCheckHandler: callback => {this.permissionCheck = callback;},
      };
    }
    loadFile(filename, options) {
      this.webContents.mainFrame.url = pathToFileURL(filename).href + '?mode=' + options.query.mode;
      events.push('load');
    }
    isDestroyed() {return this.destroyed;}
    isMinimized() {return this.minimized;}
    isVisible() {return this.visible;}
    show() {events.push('show'); this.visible = true;}
    focus() {events.push('focus');}
    hide() {events.push('hide'); this.visible = false;}
    minimize() {events.push('minimize'); this.minimized = true;}
    restore() {events.push('restore'); this.minimized = false; this.emit('restore');}
    setBackgroundColor() {}
  }
  class Tray extends EventEmitter {
    constructor() {
      super();
      if (trayFailure === 'create') throw Error('tray unavailable');
      this.destroyed = false;
      trays.push(this);
    }
    setToolTip() {}
    setContextMenu(menu) {
      if (trayFailure === 'menu') throw Error('tray menu unavailable');
      this.menu = menu;
    }
    isDestroyed() {return this.destroyed;}
    destroy() {this.destroyed = true;}
  }
  const icon = {isEmpty: () => emptyIcon, resize: () => icon};
  const electron = {app, BrowserWindow, Tray, nativeTheme,
    nativeImage: {createFromPath: () => icon},
    Menu: {setApplicationMenu() {}, buildFromTemplate: entries => entries},
    ipcMain: {handle: (name, callback) => handlers.set(name, callback)},
    clipboard: {writeText: text => {
      if (clipboardFailure) throw Error('clipboard unavailable');
      assert.equal(typeof text, 'string');
      clipboard.push(text);
      events.push(['clipboard', text]);
    }},
  };
  const process = new EventEmitter();
  process.platform = platform;
  process.env = {...env};
  process.stdin = input;
  process.stdout = {write(line) {
    const value = JSON.parse(line);
    writes.push(value);
    events.push(['parent', value.action || value.event]);
  }};
  const context = vm.createContext({__dirname: path.join(__dirname, '..'), process, Buffer,
    setTimeout(callback, delay) {const id = ++timerId; timers.set(id, {callback, delay}); return id;},
    clearTimeout: id => timers.delete(id), setImmediate: callback => immediates.push(callback),
    require(name) {
      if (name === 'electron') return electron;
      if (name === './protocol.cjs') return require('../protocol.cjs');
      if (name === 'node:fs') return {
        realpathSync: {native: filename => filename}, createReadStream: () => input,
        appendFileSync: (_filename, line) => phases.push(line.trim()),
      };
      if (name === 'node:child_process') return {execFile: (file, args, options, callback) => {
        probes.push({file, args, options, callback});
      }};
      assert.ok(['node:path', 'node:url'].includes(name), 'Unexpected dependency: ' + name);
      return require(name);
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8'), context,
                  {filename: 'main.cjs'});
  function event() {return {sender: windows[0].webContents, senderFrame: windows[0].webContents.mainFrame};}
  return {app, input, process, events, writes, phases, clipboard, probes, timers, windows, trays,
    start() {ready(); return windows[0];},
    ready() {windows[0].emit('ready-to-show');},
    message: value => input.emit('data', JSON.stringify(value) + '\n'),
    command: (action, data = {}, source = event()) => handlers.get('tracking:command')(source, action, data),
    windowCommand: (action, source = event()) => handlers.get('tracking:window')(source, action),
    nativeClose() {const event = {preventDefault() {this.prevented = true;}}; windows[0].emit('close', event); return event;},
    flushImmediate() {while (immediates.length) immediates.shift()();},
    visibility: () => events.filter(event => ['show', 'focus', 'restore', 'minimize', 'hide'].includes(event)),
  };
}

module.exports = {fixture};
