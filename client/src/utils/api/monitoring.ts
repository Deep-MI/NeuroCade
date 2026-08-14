import type { MonitoringSummary } from '../../types';

import { appJson, appOk, jsonRequest } from './core';


export async function fetchMonitoringSummary(): Promise<MonitoringSummary> {
  return appJson<MonitoringSummary>('/monitoring/summary', 'Failed to fetch monitoring summary');
}

export async function reportClientError(payload: {
  level?: string;
  event_type?: string;
  message: string;
  path?: string | null;
  details?: Record<string, unknown>;
}): Promise<void> {
  await appOk('/monitoring/client-errors', 'Failed to report client error', jsonRequest(payload, {
    method: 'POST',
  }));
}
