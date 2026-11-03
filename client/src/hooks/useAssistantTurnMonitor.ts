import { useCallback, useEffect, useState } from 'react';
import { useRequestScope } from './useRequestScope';

import type { AssistantActivity, AssistantScope, AssistantTurnCancelResponse } from '../types';
import { cancelAssistantTurn, fetchActiveAssistantTurn } from '../utils/api';

interface AssistantTurnMonitorOptions {
  workspaceId: string | null;
  scope: AssistantScope;
  caseId: string | null;
  isStreamConnected: boolean;
  onTurnComplete: () => Promise<void> | void;
}

interface AssistantTurnMonitor {
  activeTurnId: string | null;
  activity: AssistantActivity | null;
  isCanceling: boolean;
  trackTurn: (turnId: string) => void;
  updateActivity: (activity: AssistantActivity) => void;
  discoverTurn: () => Promise<string | null>;
  markTurnFinished: () => void;
  cancelTurn: () => Promise<AssistantTurnCancelResponse['status']>;
}

export function useAssistantTurnMonitor({
  workspaceId,
  scope,
  caseId,
  isStreamConnected,
  onTurnComplete,
}: AssistantTurnMonitorOptions): AssistantTurnMonitor {
  const requestScope = useRequestScope(JSON.stringify([workspaceId, scope, caseId]));
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [activity, setActivity] = useState<AssistantActivity | null>(null);
  const [isCanceling, setIsCanceling] = useState(false);

  const trackTurn = useCallback((turnId: string) => {
    setActiveTurnId(turnId);
    setActivity({ kind: 'model', label: 'Assistant', blocking: true });
  }, []);

  const updateActivity = useCallback((nextActivity: AssistantActivity) => {
    setActivity(nextActivity);
  }, []);

  const markTurnFinished = useCallback(() => {
    setActiveTurnId(null);
    setActivity(null);
    setIsCanceling(false);
  }, []);

  const discoverTurn = useCallback(async (): Promise<string | null> => {
    if (!workspaceId) return null;
    const isCurrent = requestScope.capture();
    const active = await fetchActiveAssistantTurn(workspaceId, scope, caseId);
    if (!isCurrent()) return null;
    setActivity(active.activity ?? null);
    return active.active ? active.turn_id : null;
  }, [caseId, requestScope, scope, workspaceId]);

  useEffect(() => {
    setActiveTurnId(null);
    setActivity(null);
    setIsCanceling(false);
    if (!workspaceId) return;

    let cancelled = false;
    void discoverTurn()
      .then((turnId) => {
        if (!cancelled) setActiveTurnId(turnId);
      })
      .catch((error) => {
        if (!cancelled) console.error('Failed to discover active assistant turn:', error);
      });
    return () => {
      cancelled = true;
    };
  }, [discoverTurn, workspaceId]);

  useEffect(() => {
    if (!workspaceId || !activeTurnId || isStreamConnected) return;

    let cancelled = false;
    const isCurrent = requestScope.capture();
    let timeoutId: number | undefined;
    const poll = async () => {
      try {
        const active = await fetchActiveAssistantTurn(workspaceId, scope, caseId);
        if (cancelled || !isCurrent()) return;
        if (active.active && active.turn_id) {
          setActiveTurnId(active.turn_id);
          setActivity(active.activity ?? null);
          timeoutId = window.setTimeout(() => void poll(), 2000);
          return;
        }
        markTurnFinished();
        await onTurnComplete();
      } catch (error) {
        if (cancelled || !isCurrent()) return;
        console.error('Failed to monitor active assistant turn:', error);
        timeoutId = window.setTimeout(() => void poll(), 5000);
      }
    };
    timeoutId = window.setTimeout(() => void poll(), 1000);
    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [activeTurnId, caseId, isStreamConnected, markTurnFinished, onTurnComplete, requestScope, scope, workspaceId]);

  const cancelTurn = useCallback(async (): Promise<AssistantTurnCancelResponse['status']> => {
    if (!workspaceId || isCanceling) return 'not_active';
    const isCurrent = requestScope.capture();
    setIsCanceling(true);
    try {
      let turnId = activeTurnId;
      turnId ??= await discoverTurn();
      if (!isCurrent()) return 'not_active';
      if (!turnId) {
        markTurnFinished();
        return 'not_active';
      }
      const result = await cancelAssistantTurn(turnId, workspaceId, scope, caseId);
      if (!isCurrent()) return result.status;
      if (result.status === 'not_active') markTurnFinished();
      return result.status;
    } catch (error) {
      if (isCurrent()) setIsCanceling(false);
      throw error;
    }
  }, [activeTurnId, caseId, discoverTurn, isCanceling, markTurnFinished, requestScope, scope, workspaceId]);

  return {
    activeTurnId,
    activity,
    isCanceling,
    trackTurn,
    updateActivity,
    discoverTurn,
    markTurnFinished,
    cancelTurn,
  };
}
