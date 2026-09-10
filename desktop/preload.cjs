'use strict';
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('tracking', Object.freeze({
  invoke: (action, input = {}) => ipcRenderer.invoke('tracking:command', action, input),
  window: action => ipcRenderer.invoke('tracking:window', action),
  onShow: listener => {
    const handler = () => listener();
    ipcRenderer.on('tracking:show', handler);
    return () => ipcRenderer.removeListener('tracking:show', handler);
  },
}));
