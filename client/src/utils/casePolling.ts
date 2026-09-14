import type { StatusResponse } from '../types';
import { terminalRunTransitionKey } from './runNotifications.js';

export type IsCurrentRequest = () => boolean;
export interface CasePollingOptions {
  activeCaseId: string | null;
  runId: string | null;
  runStatus: string;
  isRunActive: (status: string) => boolean;
  fetchStatus: (caseId: string) => Promise<StatusResponse>;
  fetchLogs: (caseId: string, isCurrent: IsCurrentRequest) => Promise<void>;
  fetchOutputs: (caseId: string, isCurrent: IsCurrentRequest) => Promise<void>;
  onRunChange: (status: string, runId?: string, errorMessage?: string | null, errorCode?: string | null) => void;
  onTerminalStatus?: (status: string, runId: string, workflowId?: string) => void;
  onError?: (error: unknown) => void;
}

/** Each stream is serial; cleanup invalidates every in-flight response. */
export function startCasePolling(options: CasePollingOptions, emitted: Set<string>): () => void {
  let disposed = false;
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const isCurrent = () => !disposed;
  const { activeCaseId: caseId } = options;
  const repeat = (delay: number, work: () => Promise<void>) => {
    const schedule = () => {
      if (disposed) return;
      const timer = setTimeout(() => { timers.delete(timer); void tick(); }, delay);
      timers.add(timer);
    };
    const tick = async () => {
      try { await work(); } catch (error) { if (!disposed) options.onError?.(error); }
      schedule();
    };
    schedule();
  };
  if (caseId) {
    repeat(options.isRunActive(options.runStatus) ? 2000 : 3000, async () => {
      const data = await options.fetchStatus(caseId);
      if (disposed || !data.status || data.status === 'unknown') return;
      const key = terminalRunTransitionKey({ runId: options.runId ?? undefined, status: options.runStatus }, data);
      if (key && data.runId && !emitted.has(key)) {
        await options.fetchLogs(caseId, isCurrent);
        if (disposed) return;
        await options.fetchOutputs(caseId, isCurrent);
        if (disposed) return;
        emitted.add(key);
        options.onTerminalStatus?.(data.status, data.runId, data.workflowId);
      }
      if (!disposed) options.onRunChange(data.status, data.runId, data.errorMessage, data.errorCode);
    });
    if (options.isRunActive(options.runStatus)) {
      repeat(3000, () => options.fetchLogs(caseId, isCurrent));
      repeat(10000, () => options.fetchOutputs(caseId, isCurrent));
    }
  }
  return () => { disposed = true; timers.forEach(clearTimeout); timers.clear(); };
}
