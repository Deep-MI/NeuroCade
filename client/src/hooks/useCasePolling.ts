import { useEffect, useRef } from 'react';
import { startCasePolling, type CasePollingOptions } from '../utils/casePolling';

export function useCasePolling(options: CasePollingOptions): void {
  const emitted = useRef(new Set<string>());
  const { activeCaseId, runId, runStatus, isRunActive, fetchStatus, fetchLogs,
    fetchOutputs, onRunChange, onTerminalStatus, onError } = options;

  useEffect(() => { emitted.current.clear(); }, [activeCaseId]);
  useEffect(() => startCasePolling({ activeCaseId, runId, runStatus, isRunActive,
    fetchStatus, fetchLogs, fetchOutputs, onRunChange, onTerminalStatus, onError }, emitted.current),
  [activeCaseId, runId, runStatus, isRunActive, fetchStatus, fetchLogs, fetchOutputs,
    onRunChange, onTerminalStatus, onError]);
}
