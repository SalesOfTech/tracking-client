'use strict';
const {app, BrowserWindow, ipcMain, nativeTheme, nativeImage, Menu, Tray, clipboard} = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');
const {validate, BROWSER_PAGES} = require('./protocol.cjs');

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
let windowReady = false, showRequested = false, trayAvailable = false;
let parentNotified = false;
const pending = new Map();
// PyInstaller can use an 8.3 extraction path; Chromium expands it before IPC.
const index = fs.realpathSync.native(path.join(__dirname, 'dist', 'index.html'));
const page = pathToFileURL(index).href;
app.setName('SOFT Tracking');
app.setAppUserModelId('com.soft.tracking');
nativeTheme.themeSource = 'system';
function showWindow() {
  if (health || closing) return;
  showRequested = true;
  if (!windowReady || !window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}
function canHideToTray() {
  return trayAvailable && tray && !tray.isDestroyed();
}
function backgroundWindow() {
  if (health || closing || !window || window.isDestroyed()) return;
  if (canHideToTray()) window.hide();
  else window.minimize();
}
function checkLinuxTray() {
  // A Linux Tray can exist even when both native backends failed. Only hide
  // after a host is confirmed; missing tooling/GTK-only desktops keep a taskbar entry.
  const {execFile} = require('node:child_process');
  execFile('/usr/bin/dbus-send', ['--session', '--print-reply=literal', '--reply-timeout=1000',
    '--dest=org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher',
    'org.freedesktop.DBus.Properties.Get', 'string:org.kde.StatusNotifierWatcher',
    'string:IsStatusNotifierHostRegistered'], {timeout: 1500, maxBuffer: 1024, encoding: 'utf8'}, (error, output) => {
    if (error || !/^\s*variant\s+boolean true\s*$/.test(output)) return;
    trayAvailable = true;
    if (hidden && windowReady && !showRequested && window && !window.isDestroyed()
        && (!window.isVisible() || window.isMinimized())) backgroundWindow();
  });
}
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
    if (response.event === 'show') {showWindow(); continue;}
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
  if (action === 'copy-browser-page') {
    if (health) throw Error('invalid_request');
    clipboard.writeText(BROWSER_PAGES[input.browser]);
    return {copied: true};
  }
  const result = await request(action, input);
  if (action === 'ready' && health) {closing = true; setImmediate(() => app.quit());}
  return result;
});
ipcMain.handle('tracking:window', async (event, action) => {
  if (!authorized(event) || !['minimize', 'close'].includes(action)) throw Error('invalid_request');
  if (health || closing) return;
  if (action === 'minimize') window.minimize();
  else if (mode === 'installer') {
    const state = await request('status');
    if (!state.busy) {closing = true; app.quit();}
  } else backgroundWindow();
});
app.whenReady().then(() => {
  healthPhase('app-ready');
  Menu.setApplicationMenu(null);
  window = new BrowserWindow({
    width: mode === 'installer' ? 560 : 1024, height: mode === 'installer' ? 610 : 760,
    minWidth: mode === 'installer' ? 480 : 700, minHeight: mode === 'installer' ? 560 : 560,
    frame: process.platform !== 'win32', title: 'SOFT Tracking', show: false, resizable: mode !== 'installer', backgroundColor: nativeTheme.shouldUseDarkColors ? '#191b1f' : '#ffffff',
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
    if (!closing) {event.preventDefault(); if (health) return; if (mode === 'installer') request('status').then(state => {if (!state.busy) {closing = true; app.quit();}}).catch(() => {}); else backgroundWindow();}
  });
  window.on('restore', () => {showRequested = true;});
  if (mode === 'desktop' && !health) {
    try {
      const icon = nativeImage.createFromPath(path.join(__dirname, 'brand.png'));
      if (icon.isEmpty()) throw Error('tray_icon_missing');
      tray = new Tray(process.platform === 'darwin' ? icon.resize({width: 18, height: 18}) : icon);
      tray.setToolTip('SOFT Tracking');
      const openFromTray = () => {trayAvailable = true; showWindow();};
      tray.on('click', openFromTray);
      tray.setContextMenu(Menu.buildFromTemplate([{label: 'SOFT Tracking', click: openFromTray}]));
      trayAvailable = process.platform !== 'linux';
      if (process.platform === 'linux') checkLinuxTray();
    } catch {tray?.destroy(); tray = null; trayAvailable = false;}
  }
  window.once('ready-to-show', () => {
    healthPhase('page-ready');
    windowReady = true;
    if (showRequested || !hidden) showWindow();
    else backgroundWindow();
  });
  window.loadFile(index, {query: {mode}});
});
nativeTheme.on('updated', () => {
  if (window && !window.isDestroyed()) window.setBackgroundColor(nativeTheme.shouldUseDarkColors ? '#191b1f' : '#ffffff');
});
app.on('activate', () => {
  // macOS also activates on initial launch, before the hidden window is ready.
  // Later Dock activation is an explicit request, not a permanent hidden policy.
  if (hidden && !windowReady) return;
  showWindow();
});
app.on('window-all-closed', () => {if (closing) app.quit();});
