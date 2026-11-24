// Background status icon for NeuroCade started with `./scripts/run.sh start -d`.
//
// A browser-only launch leaves no window behind, so this small Electron
// process shows a macOS menu-bar / Linux (AppIndicator) icon that reports
// whether the application is up, how many container workflows are running,
// and offers "Open NeuroCade" and "Stop NeuroCade". The launcher owns its
// lifecycle (scripts/lib/tray.sh); it also exits on its own once the launch
// session ends, so it never outlives the application it describes.
import { app, dialog, Menu, nativeImage, shell, Tray } from 'electron';
import { spawn } from 'node:child_process';
import { mkdirSync, openSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { TRAY_POLL_MS, trayView, trayViewKey } from './trayStatus.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = process.env.NEUROCADE_ROOT || path.resolve(__dirname, '..', '..');
const runtimeDir = process.env.NEUROCADE_RUNTIME_DIR || path.join(repoRoot, '.runtime');
const bridgePort = process.env.NEUROCADE_BRIDGE_PORT || '8765';
const launcher = path.join(repoRoot, 'scripts', 'run.sh');
const appUrlPath = path.join(runtimeDir, 'app-url');
const launchIdPath = path.join(runtimeDir, 'launch-id');
const bridgeTokenPath = path.join(runtimeDir, 'bridge-token');
const stopLogPath = path.join(runtimeDir, 'tray-stop.log');
// A missing launch session for this many consecutive polls means NeuroCade
// was stopped by other means (terminal, desktop launcher, uninstall).
const sessionGoneLimit = 2;

// Keep Chromium state out of the desktop window's profile, so the window
// launcher and this icon never contend for the same single-instance lock.
const trayDataDir = path.join(runtimeDir, 'electron', 'tray');
mkdirSync(trayDataDir, { recursive: true });
app.setPath('userData', path.join(trayDataDir, 'user-data'));
app.setPath('sessionData', path.join(trayDataDir, 'session-data'));
app.setName('NeuroCade');

if (!app.requestSingleInstanceLock()) {
  app.exit(0);
}

let tray = null;
let pollTimer = null;
let lastViewKey = '';
let sessionGoneCount = 0;
const state = {
  health: 'starting',
  activeRuns: null,
  appUrl: null,
  stopping: false,
};

async function readTrimmed(filePath) {
  try {
    const value = (await readFile(filePath, 'utf8')).trim();
    return value || null;
  } catch {
    return null;
  }
}

async function applicationHealthy(appUrl) {
  if (!appUrl) return false;
  try {
    const response = await fetch(new URL('/api/app/healthz', appUrl), { signal: AbortSignal.timeout(2500) });
    return response.ok;
  } catch {
    return false;
  }
}

async function bridgeActiveRuns(launchId) {
  const token = await readTrimmed(bridgeTokenPath);
  if (!token || !/^\d+$/.test(bridgePort)) return null;
  try {
    const response = await fetch(`http://127.0.0.1:${bridgePort}/v1/health`, {
      headers: { Authorization: `Bearer ${token}`, 'X-NeuroCade-Launch-ID': launchId },
      signal: AbortSignal.timeout(2500),
    });
    if (!response.ok) return null;
    const payload = await response.json();
    return Number.isInteger(payload?.active_runs) ? payload.active_runs : null;
  } catch {
    return null;
  }
}

function trayImage() {
  const name = process.platform === 'darwin' ? 'trayTemplate.png' : 'tray-linux.png';
  const image = nativeImage.createFromPath(path.join(__dirname, 'assets', name));
  // Template images follow the macOS menu bar's light/dark appearance.
  if (process.platform === 'darwin') image.setTemplateImage(true);
  return image;
}

function render() {
  if (!tray) return;
  const view = trayView(state);
  const key = trayViewKey(view);
  if (key === lastViewKey) return;
  lastViewKey = key;
  tray.setToolTip(view.tooltip);
  const template = [{ label: view.statusLabel, enabled: false }];
  if (view.runsLabel) template.push({ label: view.runsLabel, enabled: false });
  template.push(
    { type: 'separator' },
    { label: 'Open NeuroCade', enabled: view.canOpen, click: openInBrowser },
    { type: 'separator' },
    { label: 'Stop NeuroCade…', enabled: view.canStop, click: () => void stopNeuroCade(view) },
    { label: 'Hide This Icon', click: () => app.quit() },
  );
  // Linux AppIndicator menus are only refreshed by installing a new menu.
  tray.setContextMenu(Menu.buildFromTemplate(template));
}

function openInBrowser() {
  if (state.appUrl) void shell.openExternal(state.appUrl);
}

async function stopNeuroCade(view) {
  if (view.stopNeedsConfirmation) {
    const { response } = await dialog.showMessageBox({
      type: 'warning',
      title: 'Stop NeuroCade',
      message: 'Stop NeuroCade while workflows are running?',
      detail: view.stopConfirmationDetail ?? undefined,
      buttons: ['Stop NeuroCade', 'Cancel'],
      defaultId: 1,
      cancelId: 1,
    });
    if (response !== 0) return;
  }
  state.stopping = true;
  render();
  // The launcher stops the application, the bridge and finally this icon. Run
  // it detached so it survives this process being terminated by that stop.
  const log = openSync(stopLogPath, 'a');
  const child = spawn('bash', [launcher, 'stop'], {
    cwd: repoRoot,
    detached: true,
    stdio: ['ignore', log, log],
  });
  child.on('error', (error) => reportStopFailure(error.message));
  child.on('exit', (code) => {
    if (code !== 0) reportStopFailure(`The launcher exited with code ${code}.`);
  });
  child.unref();
}

function reportStopFailure(detail) {
  state.stopping = false;
  render();
  dialog.showErrorBox(
    'NeuroCade could not be stopped',
    `${detail}\n\nSee ${stopLogPath}, or run ./scripts/run.sh stop from the repository root.`,
  );
}

async function poll() {
  const launchId = await readTrimmed(launchIdPath);
  if (!launchId) {
    sessionGoneCount += 1;
    if (sessionGoneCount >= sessionGoneLimit) {
      app.quit();
      return;
    }
  } else {
    sessionGoneCount = 0;
  }
  state.appUrl = await readTrimmed(appUrlPath);
  const healthy = await applicationHealthy(state.appUrl);
  if (healthy) state.health = 'running';
  else if (state.health !== 'starting') state.health = 'unresponsive';
  state.activeRuns = launchId ? await bridgeActiveRuns(launchId) : null;
  render();
}

async function pollLoop() {
  try {
    await poll();
  } finally {
    pollTimer = setTimeout(() => void pollLoop(), TRAY_POLL_MS);
  }
}

// A tray-only app has no windows; dialogs must not end it.
app.on('window-all-closed', () => {});
app.on('before-quit', () => {
  if (pollTimer) clearTimeout(pollTimer);
});

app.whenReady().then(() => {
  app.dock?.hide();
  tray = new Tray(trayImage());
  render();
  void pollLoop();
});
