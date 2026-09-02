import { isRunActive, isRunTerminal } from '../constants.js';
import type { StatusResponse } from '../types';

export interface ObservedRun {
  runId?: string;
  status: string;
}

export function terminalRunTransitionKey(
  previous: ObservedRun,
  current: StatusResponse,
): string | null {
  if (
    !previous.runId
    || !current.runId
    || previous.runId !== current.runId
    || !isRunActive(previous.status)
    || !isRunTerminal(current.status)
  ) {
    return null;
  }
  return `${current.runId}:${current.status}`;
}

export function workflowStatusNotificationId(runId: string, status: string): string {
  return `workflow:${runId}:${status}`;
}
