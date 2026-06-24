import { app, BrowserWindow, dialog, ipcMain, nativeImage, shell } from 'electron';
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..', '..');
const envPath = path.join(repoRoot, '.env');
const backendScript = path.join(repoRoot, 'scripts', 'desktop', 'run_backend.sh');
const appIconPath = path.join(__dirname, 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon.png');
const electronRuntimeDir = path.join(repoRoot, '.runtime', 'electron');
const healthPollMs = 1500;
const healthTimeoutMs = 600_000;
const appDisplayName = 'NeuroCade';
const appDesktopFileName = 'neurocade.desktop';
const appBundleIdentifier = 'org.neurocade.app';
const titlebarHeight = 44;
const titlebarThemes = {
  dark: {
    color: '#262626',
    symbolColor: '#f4f4f4',
  },
  light: {
    color: '#ffffff',
    symbolColor: '#161616',
  },
};

function appendSwitchIfMissing(name) {
  if (!app.commandLine.hasSwitch(name)) {
    app.commandLine.appendSwitch(name);
  }
}

const chromiumSandboxDisabled = app.commandLine.hasSwitch('no-sandbox');

if (chromiumSandboxDisabled) {
  appendSwitchIfMissing('disable-gpu-sandbox');
}

appendSwitchIfMissing('disable-dev-shm-usage');

let mainWindow = null;
let startedStack = false;
let quitting = false;
let stopping = false;
let backendChild = null;

function setLocalAppPath(name, target) {
  mkdirSync(target, { recursive: true });
  app.setPath(name, target);
}

function configureLocalElectronStorage() {
  setLocalAppPath('appData', path.join(electronRuntimeDir, 'app-data'));
  setLocalAppPath('userData', path.join(electronRuntimeDir, 'user-data'));
  setLocalAppPath('sessionData', path.join(electronRuntimeDir, 'session-data'));
  setLocalAppPath('crashDumps', path.join(electronRuntimeDir, 'crash-dumps'));
  mkdirSync(path.join(electronRuntimeDir, 'logs'), { recursive: true });
  app.setAppLogsPath(path.join(electronRuntimeDir, 'logs'));
}

configureLocalElectronStorage();

function titlebarOptions(theme = 'dark') {
  const titlebarTheme = titlebarThemes[theme] ?? titlebarThemes.dark;
  return {
    color: titlebarTheme.color,
    symbolColor: titlebarTheme.symbolColor,
    height: titlebarHeight,
  };
}

function windowChromeOptions() {
  if (process.platform === 'darwin') {
    return {
      titleBarStyle: 'hiddenInset',
      trafficLightPosition: { x: 14, y: 14 },
    };
  }
  return {
    titleBarStyle: 'hidden',
    titleBarOverlay: titlebarOptions('dark'),
  };
}

function configureAppIdentity() {
  app.setName(appDisplayName);
  if (process.platform === 'linux') {
    app.setDesktopName(appDesktopFileName);
  }
  if (process.platform === 'win32') {
    app.setAppUserModelId(appBundleIdentifier);
  }
  if (process.platform !== 'darwin' || !app.dock) return;
  const dockIcon = nativeImage.createFromPath(appIconPath);
  if (!dockIcon.isEmpty()) {
    app.dock.setIcon(dockIcon);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function appendLog(message) {
  const line = String(message ?? '').trimEnd();
  if (!line || !mainWindow || mainWindow.isDestroyed()) return;
  void mainWindow.webContents.executeJavaScript(
    `window.__appendNeuroCadeLog?.(${JSON.stringify(line)});`,
    true,
  ).catch(() => {});
}

function formatElapsed(ms) {
  return `${(ms / 1000).toFixed(3)}s`;
}

function timingLogger(label) {
  const startedAt = performance.now();
  return () => appendLog(`[startup timing] ${label}: ${formatElapsed(performance.now() - startedAt)}`);
}

function startupHtml() {
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${appDisplayName}</title>
  <style>
    :root { color-scheme: light; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f4f7f8; color: #18252b; }
    main { width: min(680px, calc(100vw - 48px)); }
    h1 { margin: 0 0 10px; font-size: 28px; font-weight: 650; letter-spacing: 0; }
    p { margin: 0 0 22px; color: #52646d; line-height: 1.5; }
    .bar { height: 8px; overflow: hidden; border-radius: 999px; background: #dce7ea; }
    .bar::before { content: ""; display: block; width: 38%; height: 100%; border-radius: inherit; background: #247a84; animation: slide 1.35s infinite ease-in-out; }
    details { margin-top: 24px; border: 1px solid #d5e0e3; border-radius: 8px; background: white; }
    summary { cursor: pointer; padding: 12px 14px; color: #28454d; font-size: 14px; }
    pre { box-sizing: border-box; width: 100%; max-height: 280px; margin: 0; overflow: auto; padding: 0 14px 14px; white-space: pre-wrap; color: #293b42; font-size: 12px; line-height: 1.45; }
    @keyframes slide { 0% { transform: translateX(-110%); } 55% { transform: translateX(120%); } 100% { transform: translateX(265%); } }
  </style>
</head>
<body>
  <main>
    <h1>Starting ${appDisplayName}</h1>
    <p>The local analysis services are starting. This window will open the workspace when the backend is ready.</p>
    <div class="bar" aria-hidden="true"></div>
    <details>
      <summary>Startup logs</summary>
      <pre id="logs"></pre>
    </details>
  </main>
  <script>
    window.__appendNeuroCadeLog = (line) => {
      const logs = document.getElementById('logs');
      logs.textContent += line + "\\n";
      logs.scrollTop = logs.scrollHeight;
    };
  </script>
</body>
</html>`;
}

async function readEnvFile() {
  const values = {};
  let raw = '';
  try {
    raw = await readFile(envPath, 'utf8');
  } catch {
    return values;
  }
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const separator = trimmed.indexOf('=');
    if (separator < 0) continue;
    const key = trimmed.slice(0, separator).trim();
    let value = trimmed.slice(separator + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function healthUrlFor(appBaseUrl) {
  return new URL('/api/app/healthz', appBaseUrl).toString();
}

async function isHealthy(healthUrl) {
  try {
    const response = await fetch(healthUrl, { signal: AbortSignal.timeout(2500) });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForBackendHealth(healthUrl) {
  const finishTiming = timingLogger('Electron backend health wait');
  const deadline = Date.now() + healthTimeoutMs;
  while (Date.now() < deadline) {
    if (await isHealthy(healthUrl)) {
      finishTiming();
      return;
    }
    if (backendChild && backendChild.exitCode !== null) {
      throw new Error(`${appDisplayName} backend exited (code ${backendChild.exitCode}) before becoming healthy.`);
    }
    await sleep(healthPollMs);
  }
  throw new Error(`Timed out waiting for ${healthUrl}`);
}

async function startBackendIfNeeded(healthUrl) {
  const finishInitialHealthTiming = timingLogger('Electron initial health check');
  appendLog(`Checking ${healthUrl}`);
  if (await isHealthy(healthUrl)) {
    finishInitialHealthTiming();
    appendLog('Local backend is already running.');
    return false;
  }
  finishInitialHealthTiming();
  appendLog('Starting local NeuroCade backend.');
  startedStack = true;
  // Spawn the monolith as a long-running child (own process group so we can
  // terminate it and any tool subprocesses on quit).
  backendChild = spawn('bash', [backendScript], {
    cwd: repoRoot,
    env: process.env,
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backendChild.stdout?.on('data', (chunk) => appendLog(chunk.toString()));
  backendChild.stderr?.on('data', (chunk) => appendLog(chunk.toString()));
  backendChild.on('exit', (code) => appendLog(`Backend process exited with code ${code}.`));
  appendLog(`Waiting for ${appDisplayName} backend.`);
  await waitForBackendHealth(healthUrl);
  appendLog(`${appDisplayName} backend is ready.`);
  return true;
}

async function stopBackendIfOwned() {
  if (!startedStack || stopping) return;
  stopping = true;
  appendLog('Stopping local NeuroCade backend.');
  try {
    if (backendChild && backendChild.pid && backendChild.exitCode === null) {
      // Kill the whole process group (negative pid) so tool subprocesses stop too.
      try {
        process.kill(-backendChild.pid, 'SIGTERM');
      } catch {
        backendChild.kill('SIGTERM');
      }
    }
  } catch (error) {
    appendLog(`Failed to stop backend: ${error.message}`);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 920,
    minWidth: 1024,
    minHeight: 720,
    show: false,
    title: appDisplayName,
    icon: appIconPath,
    ...windowChromeOptions(),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: !chromiumSandboxDisabled,
      preload: path.join(__dirname, 'preload.cjs'),
    },
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.once('ready-to-show', () => mainWindow?.show());
  void mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(startupHtml())}`);
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });
}

ipcMain.on('neurocade:titlebar-theme', (event, theme) => {
  if (process.platform === 'darwin') return;
  const targetWindow = BrowserWindow.fromWebContents(event.sender);
  if (!targetWindow || targetWindow.isDestroyed()) return;
  targetWindow.setTitleBarOverlay(titlebarOptions(theme));
});

async function boot() {
  const finishBackendReadyTiming = timingLogger('Electron backend-ready total');
  createWindow();
  const env = await readEnvFile();
  const appBaseUrl = env.APP_BASE_URL || 'http://localhost:8000';
  const healthUrl = healthUrlFor(appBaseUrl);
  try {
    await startBackendIfNeeded(healthUrl);
    finishBackendReadyTiming();
    await mainWindow?.loadURL(appBaseUrl);
  } catch (error) {
    appendLog(error.stack || error.message);
    await dialog.showMessageBox(mainWindow, {
      type: 'error',
      title: `${appDisplayName} could not start`,
      message: `The local ${appDisplayName} backend did not start.`,
      detail: `${error.message}\n\nRun ./scripts/desktop/run_backend.sh from the repo root to see backend logs.`,
    });
  }
}

app.whenReady().then(() => {
  configureAppIdentity();
  return boot();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void boot();
  }
});

app.on('before-quit', (event) => {
  if (!startedStack || quitting) return;
  event.preventDefault();
  quitting = true;
  void stopBackendIfOwned().finally(() => app.exit(0));
});

app.on('window-all-closed', () => {
  app.quit();
});
