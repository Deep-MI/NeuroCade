// Pure presentation logic for the background status icon (electron/tray.mjs).
// Kept free of Electron imports so it can be unit tested under plain Node.

export const TRAY_POLL_MS = 5000;

/**
 * Describe what the status icon shows for one poll result.
 *
 * @param {object} state
 * @param {'starting' | 'running' | 'unresponsive'} state.health
 *   'starting' until the first successful health check, then 'running' or
 *   'unresponsive' depending on the latest check.
 * @param {number | null} state.activeRuns Bridge-reported container runs, or
 *   null when the bridge could not be asked.
 * @param {string | null} state.appUrl Browser URL of the running application.
 * @param {boolean} state.stopping True once the user asked to stop NeuroCade.
 */
export function trayView({ health, activeRuns, appUrl, stopping }) {
  let statusLabel;
  if (stopping) statusLabel = 'NeuroCade is stopping…';
  else if (health === 'running') statusLabel = 'NeuroCade is running';
  else if (health === 'unresponsive') statusLabel = 'NeuroCade is not responding';
  else statusLabel = 'NeuroCade is starting…';

  const runs = typeof activeRuns === 'number' && activeRuns > 0 ? activeRuns : 0;
  const runsLabel = runs > 0 ? `${runs} workflow${runs === 1 ? '' : 's'} in progress` : null;

  const tooltipParts = [statusLabel];
  if (runsLabel) tooltipParts.push(runsLabel);
  if (appUrl && !stopping) tooltipParts.push(appUrl);

  return {
    statusLabel,
    runsLabel,
    tooltip: tooltipParts.join('\n'),
    canOpen: Boolean(appUrl) && !stopping,
    canStop: !stopping,
    // Stopping interrupts container workflows; only then ask first.
    stopNeedsConfirmation: runs > 0,
    stopConfirmationDetail: runs > 0
      ? `${runsLabel}. Stopping NeuroCade interrupts ${runs === 1 ? 'it' : 'them'}.`
      : null,
  };
}

/** Stable key so the menu is only rebuilt when something visible changed. */
export function trayViewKey(view) {
  return JSON.stringify(view);
}
