'use strict';
const {app, BrowserWindow, ipcMain, nativeTheme, Menu, Tray} = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const {validate} = require('./protocol.cjs');

const mode = process.env.SOFT_TRACKING_UI_MODE === 'installer' ? 'installer' : 'desktop';
const health = process.env.SOFT_TRACKING_UI_HEALTH === '1';
function healthPhase(phase) {
  if (health && process.env.SOFT_TRACKING_HEALTH) {
    require('node:fs').appendFileSync(process.env.SOFT_TRACKING_HEALTH + '.phase', phase + '\n');
  }
}
healthPhase('main');
const hidden = process.env.SOFT_TRACKING_UI_HIDDEN === '1';
// Electron replaces process.stdin with an EOF-only stream on Windows.
const parentInput = process.platform === 'win32' ? fs.createReadStream(null, {fd: 0, autoClose: false}) : process.stdin;
let window, tray, closing = false, sequence = 0, buffer = '';
let parentNotified = false;
const pending = new Map();
// PyInstaller can use an 8.3 extraction path; Chromium expands it before IPC.
const index = fs.realpathSync.native(path.join(__dirname, 'dist', 'index.html'));
const page = pathToFileURL(index).href;
app.setName('SOFT Tracking');
app.setAppUserModelId('com.soft.tracking');
// The Python parent owns the durable runtime; there is no listening TCP port.
function request(action, input = {}) {
  validate(action, input);
  if (pending.size >= 16) return Promise.reject(Error('busy'));
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {pending.delete(id); reject(Error('server_unavailable'));}, 35000);
    pending.set(id, {resolve, reject, timer, action});
    if (action === 'status') healthPhase('request-status');
    if (action === 'ready') healthPhase('request-ready');
    process.stdout.write(JSON.stringify({id, action, input}) + '\n');
  });
}
parentInput.setEncoding('utf8');
parentInput.on('error', () => {healthPhase('parent-stream-failed'); app.exit(1);});
parentInput.on('data', chunk => {
  buffer += chunk;
  if (Buffer.byteLength(buffer) > 1024 * 1024) return app.exit(1);
  let end;
  while ((end = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
    let response;
    try {response = JSON.parse(line);} catch {return app.exit(1);}
    if (response.event === 'shutdown') {closing = true; app.quit(); continue;}
    if (response.event === 'show') {window?.show(); window?.focus(); continue;}
    const call = pending.get(response.id);
    if (!call) continue;
    pending.delete(response.id); clearTimeout(call.timer);
    if (call.action === 'status') healthPhase(response.ok ? 'response-status-ok' : 'response-status-error');
    if (call.action === 'ready') healthPhase(response.ok ? 'response-ready-ok' : 'response-ready-error');
    response.ok ? call.resolve(response.data) : call.reject(Error(response.error || 'connect_failed'));
  }
});
parentInput.on('end', () => {healthPhase('parent-disconnected'); closing = true; app.quit();});
process.on('SIGTERM', () => {closing = true; app.quit();});
app.on('will-quit', () => {
  // The parent closes its pipe to release Windows' pending native read.
  if (!parentNotified && !parentInput.readableEnded) {
    parentNotified = true;
    process.stdout.write(JSON.stringify({event: 'closing'}) + '\n');
  }
});

function authorized(event) {
  return window && event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame && event.senderFrame.url.split('?')[0] === page;
}
ipcMain.handle('tracking:command', async (event, action, input) => {
  if (!authorized(event)) {healthPhase('ipc-unauthorized'); throw Error('unauthorized');}
  validate(action, input);
  if (action === 'preferences' && input.theme) nativeTheme.themeSource = input.theme;
  const result = await request(action, input);
  if (action === 'status' && result.theme) nativeTheme.themeSource = result.theme;
  if (action === 'ready' && health) {closing = true; setImmediate(() => app.quit());}
  return result;
});
ipcMain.handle('tracking:window', async (event, action) => {
  if (!authorized(event) || !['minimize', 'close'].includes(action)) throw Error('invalid_request');
  if (action === 'minimize') window.minimize();
  else if (mode === 'installer') {
    const state = await request('status');
    if (!state.busy) {closing = true; app.quit();}
  } else if (tray) window.hide();
  else window.minimize();
});
app.whenReady().then(() => {
  healthPhase('app-ready');
  Menu.setApplicationMenu(null);
  window = new BrowserWindow({
    width: mode === 'installer' ? 560 : 1024, height: mode === 'installer' ? 610 : 760,
    minWidth: mode === 'installer' ? 480 : 700, minHeight: mode === 'installer' ? 560 : 560,
    frame: false, show: false, resizable: mode !== 'installer', backgroundColor: nativeTheme.shouldUseDarkColors ? '#17191c' : '#ffffff',
    icon: path.join(__dirname, 'brand.png'),
    webPreferences: {preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, devTools: !app.isPackaged},
  });
  window.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  window.webContents.session.setPermissionCheckHandler(() => false);
  window.webContents.on('render-process-gone', () => app.exit(1));
  window.webContents.on('did-fail-load', () => healthPhase('page-load-failed'));
  window.webContents.on('preload-error', () => healthPhase('preload-failed'));
  window.on('close', event => {
    if (!closing) {event.preventDefault(); if (mode === 'installer') request('status').then(state => {if (!state.busy) {closing = true; app.quit();}}).catch(() => {}); else if (tray) window.hide(); else window.minimize();}
  });
  if (mode === 'desktop' && !health) {
    try {
      tray = new Tray(path.join(__dirname, 'brand.png'));
      tray.setToolTip('SOFT Tracking');
      tray.on('click', () => {window.show(); window.focus();});
      tray.setContextMenu(Menu.buildFromTemplate([{label: 'SOFT Tracking', click: () => {window.show(); window.focus();}}]));
    } catch {tray = null;}
  }
  window.once('ready-to-show', () => {healthPhase('page-ready'); if (!health && !hidden) window.show();});
  window.loadFile(index, {query: {mode}});
});
app.on('activate', () => {window?.show(); window?.focus();});
app.on('window-all-closed', () => {if (closing) app.quit();});
