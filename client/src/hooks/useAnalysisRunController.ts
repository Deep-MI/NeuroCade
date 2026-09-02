import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import type { NavigateFunction } from 'react-router';

import { isRunFailed } from '../constants';
import type { AnalysisRunParams, CaseSummary, ChatMessage } from '../types';
import * as api from '../utils/api';
import { caseViewerPath } from '../utils/caseRoutes';
import { workflowStatusNotificationId } from '../utils/runNotifications';

interface UseAnalysisRunControllerArgs {
  initialWorkspaceId: string | null;
  currentCaseId: string | null;
  currentCaseTitle: string | null;
  availableCases: CaseSummary[];
  navigate: NavigateFunction;
  fetchAvailableCases: () => Promise<void>;
  setActiveCaseId: (caseId: string) => void;
  setLogs: (logs: string) => void;
  setChatNotifications: Dispatch<SetStateAction<ChatMessage[]>>;
}

export function useAnalysisRunController({
  initialWorkspaceId,
  currentCaseId,
  currentCaseTitle,
  availableCases,
  navigate,
  fetchAvailableCases,
  setActiveCaseId,
  setLogs,
  setChatNotifications,
}: UseAnalysisRunControllerArgs) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<string>('idle');
  const [runId, setRunId] = useState<string | null>(null);
  const [isSubmittingRun, setIsSubmittingRun] = useState(false);
  const [queueMessage, setQueueMessage] = useState<string>('This workflow will run as a background job.');

  const handleRunAnalysis = useCallback((toolId: string) => {
    if (!currentCaseId && !isRunFailed(runStatus)) {
      alert('Please create or load a case first.');
      return;
    }
    setSelectedToolId(toolId);
    setShowConfirm(true);
    setQueueMessage('This workflow will run as a background job.');
  }, [currentCaseId, runStatus]);

  const handleCancel = useCallback(async () => {
    if (!currentCaseId) return;
    if (!confirm('Are you sure you want to cancel this run?')) return;
    try {
      await api.cancelCaseRun(currentCaseId);
      setRunStatus('canceled');
      setChatNotifications((previous) => [...previous, {
        notificationId: runId ? workflowStatusNotificationId(runId, 'canceled') : undefined,
        role: 'info',
        content: 'Run canceled by user.',
      }]);
    } catch (error) {
      console.error('Failed to cancel run', error);
    }
  }, [currentCaseId, runId, setChatNotifications]);

  const confirmRun = useCallback(async (params: AnalysisRunParams) => {
    if (!currentCaseId || !initialWorkspaceId) {
      throw new Error('No active case selected');
    }
    if (params.input_artifact_ids.length === 0 || params.input_artifact_ids.some((value) => !value)) {
      throw new Error('Choose every required workflow input');
    }
    const currentCaseName = currentCaseTitle
      ?? availableCases.find((caseItem) => caseItem.id === currentCaseId)?.title
      ?? null;
    setShowConfirm(false);
    setIsSubmittingRun(true);
    setLogs(`Starting ${params.tool_id} workflow…\nChecking runtime and queuing analysis.`);
    try {
      const data = await api.startRun({
        tool_id: params.tool_id,
        case_id: currentCaseId,
        input_artifact_ids: params.input_artifact_ids,
        output_name_overrides: params.output_name_overrides,
      });
      setRunStatus(data.status);
      setRunId(data.run_id);
      setActiveCaseId(data.case_id);
      if (data.workspace_id) {
        void navigate(caseViewerPath(data.workspace_id, data.case_id));
      }
      setLogs(`Initializing ${params.tool_id} workflow…\nRun started`);
      setChatNotifications([{
        role: 'info',
        content: `${params.tool_id} started for case "${currentCaseName ?? currentCaseId}".`,
      }]);
      void fetchAvailableCases();
    } catch (error: unknown) {
      console.error('Run error:', error);
      const message = error instanceof Error ? error.message : String(error);
      setRunStatus('failed');
      setLogs(`Could not start ${params.tool_id} workflow.\n${message}`);
      setChatNotifications((previous) => [...previous, {
        role: 'info',
        content: `${params.tool_id} could not be started: ${message}`,
      }]);
    } finally {
      setIsSubmittingRun(false);
    }
  }, [
    availableCases,
    currentCaseId,
    currentCaseTitle,
    fetchAvailableCases,
    initialWorkspaceId,
    navigate,
    setActiveCaseId,
    setChatNotifications,
    setLogs,
  ]);

  useEffect(() => {
    if (!showConfirm || !initialWorkspaceId) return;
    void api.fetchQueueStatus(initialWorkspaceId)
      .then((data) => {
        const queue = data.total > 0
          ? `${data.total} other job(s) are queued or running.`
          : 'The analysis worker is currently idle.';
        setQueueMessage(`This workflow will run as a background job. ${queue}`);
      })
      .catch((error) => {
        console.error('Failed to fetch queue status', error);
        setQueueMessage('This workflow will run as a background job.');
      });
  }, [initialWorkspaceId, showConfirm]);

  return {
    showConfirm,
    setShowConfirm,
    selectedToolId,
    runId,
    runStatus,
    isSubmittingRun,
    setRunId,
    setRunStatus,
    queueMessage,
    handleRunAnalysis,
    handleCancel,
    confirmRun,
  };
}
