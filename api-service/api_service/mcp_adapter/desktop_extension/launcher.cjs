'use strict';

// No shell, dependency downloads, runtime startup, or second MCP implementation.
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const config = require('./installation.json');

function fail(message) {
  process.stderr.write(`NeuroCade: ${message}\n`);
  process.exitCode = 1;
}

async function main() {
  try {
    fs.accessSync(config.executable, fs.constants.X_OK);
  } catch {
    throw new Error('The NeuroCade connector is missing or the installation moved. Start NeuroCade with local agents enabled, then download the extension again.');
  }
  let health;
  try {
    const response = await fetch(new URL('/api/app/healthz', config.url), {
      signal: AbortSignal.timeout(5000), redirect: 'error',
    });
    if (!response.ok) throw new Error('unavailable');
    health = await response.json();
  } catch {
    throw new Error('NeuroCade is not reachable. Start NeuroCade on this Mac, then reconnect the extension.');
  }
  if (!health.mcp_enabled) throw new Error('Local agents are disabled. Restart NeuroCade with local agents enabled, then reconnect.');
  // The executable is pinned by the app that built this bundle.
  // The shared connector handles pairing, private storage, and live identity.
  const child = spawn(config.executable, ['connect-paired', '--pairing-file', path.join(__dirname, 'installation.json')], {
    stdio: ['pipe', 'pipe', 'pipe'], shell: false,
  });
  process.stdin.pipe(child.stdin);
  child.stdout.pipe(process.stdout);
  // Credentials and backend tracebacks must never enter the desktop logs.
  child.stderr.resume();
  child.stdin.on('error', () => {});
  child.on('error', () => {
    fail('Could not launch the installed connector. Restart NeuroCade and reconnect.');
    process.exit(1);
  });
  child.on('exit', (code, signal) => {
    if (code && !signal) fail('Connection ended. If first-time pairing expired or was already used, download a new extension from NeuroCade and install it within 10 minutes. Otherwise check that the connection has not been revoked.');
    process.exit(code || (signal ? 1 : 0));
  });
  process.stdin.on('end', () => child.kill('SIGTERM'));
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
}

main().catch(error => fail(error.message));
