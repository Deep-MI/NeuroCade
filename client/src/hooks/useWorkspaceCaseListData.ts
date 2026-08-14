import { useEffect, useState } from 'react';

import { isRunActive } from '../constants';
import type { CaseSummary, WorkspaceSummary } from '../types';
import { fetchCases } from '../utils/api';

export function useWorkspaceCaseListData(workspaceId: string | undefined, workspaces: WorkspaceSummary[] | undefined) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [workspaceCaseCounts, setWorkspaceCaseCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workspaceChatClearRequestToken, setWorkspaceChatClearRequestToken] = useState(0);
  const [isWorkspaceChatClearing, setIsWorkspaceChatClearing] = useState(false);

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
    if (!workspaceId) {
      setCases([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    let initial = true;
    let timeoutId: number | undefined;
    const loadWorkspaceData = async () => {
      let hasActiveRuns = false;
      if (initial) setLoading(true);
      try {
        const casesResponse = await fetchCases(workspaceId);
        if (cancelled) return;
        setCases(casesResponse.cases);
        setWorkspaceCaseCounts((current) => (
          current[workspaceId] === casesResponse.cases.length ? current : { ...current, [workspaceId]: casesResponse.cases.length }
        ));
        hasActiveRuns = casesResponse.cases.some((caseItem) => isRunActive(caseItem.latest_run_status ?? 'uploaded'));
        setError(null);
        if (initial) {
          setLoading(false);
          initial = false;
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
        if (!cancelled) {
          timeoutId = window.setTimeout(
            () => void loadWorkspaceData(),
            hasActiveRuns ? 5000 : 30000,
          );
        }
      }
    };
    void loadWorkspaceData();
    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
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
    workspaceChatClearRequestToken,
    requestWorkspaceChatClear: () => setWorkspaceChatClearRequestToken((token) => token + 1),
    isWorkspaceChatClearing,
    setIsWorkspaceChatClearing,
  };
}
