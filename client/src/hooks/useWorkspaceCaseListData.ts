import { useEffect, useRef, useState } from 'react';

import { isRunActive, isRunTerminal } from '../constants';
import type { CaseSummary, ChatMessage, WorkspaceBatchRunSummary, WorkspaceSummary } from '../types';
import { fetchCases, fetchWorkspaceBatchRuns } from '../utils/api';

function buildWorkspaceRunCompletionMessage(run: WorkspaceBatchRunSummary): ChatMessage {
  const name = run.report_name || run.run_id;
  if (run.status === 'completed' || run.status === 'finished') return { role: 'info', content: `Workspace run "${name}" finished.` };
  if (run.status === 'failed' || run.status === 'error') return { role: 'info', content: `Workspace run "${name}" failed.` };
  if (run.status === 'canceled') return { role: 'info', content: `Workspace run "${name}" was canceled.` };
  return { role: 'info', content: `Workspace run "${name}" ended with status "${run.status}".` };
}

export function useWorkspaceCaseListData(workspaceId: string | undefined, workspaces: WorkspaceSummary[] | undefined) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [workspaceCaseCounts, setWorkspaceCaseCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workspaceBatchRuns, setWorkspaceBatchRuns] = useState<WorkspaceBatchRunSummary[]>([]);
  const [workspaceChatNotifications, setWorkspaceChatNotifications] = useState<ChatMessage[]>([]);
  const [workspaceChatClearRequestToken, setWorkspaceChatClearRequestToken] = useState(0);
  const [isWorkspaceChatClearing, setIsWorkspaceChatClearing] = useState(false);
  const previousBatchRunStatusesRef = useRef<Record<string, string>>({});

  useEffect(() => {
    setWorkspaceCaseCounts((current) => {
      let changed = false;
      const next = { ...current };
      for (const workspace of workspaces ?? []) {
        if (typeof workspace.case_count !== 'number') continue;
        if (next[workspace.id] !== workspace.case_count) {
          next[workspace.id] = workspace.case_count;
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [workspaces]);

  useEffect(() => {
    setWorkspaceChatNotifications([]);
    previousBatchRunStatusesRef.current = {};
  }, [workspaceId]);

  useEffect(() => {
    if (!workspaceId) {
      setCases([]);
      setWorkspaceBatchRuns([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    let initial = true;
    const loadWorkspaceData = async () => {
      if (initial) setLoading(true);
      try {
        const casesResponse = await fetchCases(workspaceId);
        if (cancelled) return;
        setCases(casesResponse.cases);
        setWorkspaceCaseCounts((current) => (
          current[workspaceId] === casesResponse.cases.length ? current : { ...current, [workspaceId]: casesResponse.cases.length }
        ));
        setError(null);
        if (initial) {
          setLoading(false);
          initial = false;
        }
        const batchRuns = await fetchWorkspaceBatchRuns(workspaceId);
        if (cancelled) return;
        const priorStatuses = previousBatchRunStatusesRef.current;
        const completedMessages = batchRuns
          .filter((run) => {
            const previousStatus = priorStatuses[run.run_id];
            return previousStatus !== undefined && isRunActive(previousStatus) && isRunTerminal(run.status);
          })
          .map(buildWorkspaceRunCompletionMessage);
        previousBatchRunStatusesRef.current = Object.fromEntries(batchRuns.map((run) => [run.run_id, run.status]));
        setWorkspaceBatchRuns(batchRuns);
        if (completedMessages.length > 0) {
          setWorkspaceChatNotifications((current) => [...current, ...completedMessages]);
        }
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        console.error('Failed to fetch workspace data:', err);
      } finally {
        if (!cancelled && initial) {
          setLoading(false);
          initial = false;
        }
      }
    };
    void loadWorkspaceData();
    const intervalId = window.setInterval(() => void loadWorkspaceData(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [workspaceId]);

  return {
    cases,
    setCases,
    workspaceCaseCounts,
    setWorkspaceCaseCounts,
    loading,
    error,
    setError,
    workspaceBatchRuns,
    setWorkspaceBatchRuns,
    workspaceChatNotifications,
    workspaceChatClearRequestToken,
    requestWorkspaceChatClear: () => setWorkspaceChatClearRequestToken((token) => token + 1),
    isWorkspaceChatClearing,
    setIsWorkspaceChatClearing,
  };
}
