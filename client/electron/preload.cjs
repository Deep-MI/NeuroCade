const { contextBridge, ipcRenderer } = require('electron');

const platform = process.platform;

window.addEventListener('DOMContentLoaded', () => {
  document.documentElement.classList.add('nc-electron', `nc-electron-${platform}`);
});

contextBridge.exposeInMainWorld('neurocadeElectron', {
  platform,
  setTitlebarTheme(theme) {
    ipcRenderer.send('neurocade:titlebar-theme', theme === 'light' ? 'light' : 'dark');
  },
});
