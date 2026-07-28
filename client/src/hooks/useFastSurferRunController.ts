import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import type { NavigateFunction } from 'react-router';

import { isRunFailed } from '../constants';
import type { CaseSummary, ChatMessage, FastSurferParams, OutputVolume, Volume } from '../types';
import * as api from '../utils/api';
import { caseViewerPath } from '../utils/caseRoutes';

function chooseRunInputArtifactId(options: OutputVolume[], layers: Volume[], requestedId?: string | null): string | null {
  const validOptions = options.filter((option) => option.id && option.kind === 'volume' && (option.type ?? 'intensity') === 'intensity');
  if (requestedId && validOptions.some((option) => option.id === requestedId)) {
    return requestedId;
  }

  const visibleIntensityLayers = layers.filter((layer) => (layer.type ?? 'intensity') === 'intensity' && layer.visible);
  const loadedIntensityLayers = layers.filter((layer) => (layer.type ?? 'intensity') === 'intensity');
  const candidates = [...visibleIntensityLayers, ...loadedIntensityLayers];
  for (const layer of candidates) {
    if (layer.artifactId && validOptions.some((option) => option.id === layer.artifactId)) {
      return layer.artifactId;
    }
    const matchingOption = validOptions.find((option) => option.filename === layer.filename);
    if (matchingOption?.id) {
      return matchingOption.id;
    }
  }

  return validOptions[0]?.id ?? null;
}

interface UseFastSurferRunControllerArgs {
  initialWorkspaceId: string | null;
  currentCaseId: string | null;
  currentCaseTitle: string | null;
  availableCases: CaseSummary[];
  runInputOptions: OutputVolume[];
  volumes: Volume[];
  navigate: NavigateFunction;
  fetchAvailableCases: () => Promise<void>;
  setActiveCaseId: (caseId: string) => void;
  setLogs: (logs: string) => void;
  setChatNotifications: Dispatch<SetStateAction<ChatMessage[]>>;
}

export function useFastSurferRunController({
  initialWorkspaceId,
  currentCaseId,
  currentCaseTitle,
  availableCases,
  runInputOptions,
  volumes,
  navigate,
  fetchAvailableCases,
  setActiveCaseId,
  setLogs,
  setChatNotifications,
}: UseFastSurferRunControllerArgs) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [runStatus, setRunStatus] = useState<string>('idle');
  const [queueMessage, setQueueMessage] = useState<string>("FastSurfer will run on the dedicated analysis server. This may take 15-60 minutes depending on the hardware.");

  const handleAgentRunFastSurfer = useCallback(async (cmd: { case_id: string; input_artifact_id?: string; seg_only?: boolean; case_name?: string }) => {
    if (!initialWorkspaceId) {
      setChatNotifications((previous) => [...previous, { role: 'info', content: 'No active workspace selected for FastSurfer run.' }]);
      return;
    }
    if (!currentCaseId) {
      setChatNotifications((previous) => [...previous, { role: 'info', content: 'No active case selected for FastSurfer run.' }]);
      return;
    }
    const currentCaseName = currentCaseTitle ?? availableCases.find((caseItem) => caseItem.case_id === currentCaseId)?.subject_name ?? null;
    const effectiveName = cmd.case_name ?? currentCaseName ?? currentCaseId;
    const inputArtifactId = chooseRunInputArtifactId(runInputOptions, volumes, cmd.input_artifact_id);
    if (!inputArtifactId) {
      setShowConfirm(true);
      setChatNotifications((previous) => [...previous, { role: 'info', content: 'Choose an input volume before starting FastSurfer.' }]);
      return;
    }
    const formData = api.buildRunFormData(
      {
        input_artifact_id: inputArtifactId,
        seg_only: cmd.seg_only ?? false,
        no_bias: false,
        no_cereb: cmd.seg_only ?? false,
        no_asegdkt: false,
        no_hypothal: false,
        three_t: false,
        case_name: effectiveName,
      },
      {
        activeCaseId: currentCaseId,
        currentCaseName,
        workspaceId: initialWorkspaceId,
      },
    );

    try {
      const data = await api.startRun(formData);
      setRunStatus(data.status);
      setActiveCaseId(data.case_id);
      if (data.workspace_id) {
        void navigate(caseViewerPath(data.workspace_id, data.case_id, effectiveName));
      }
      setLogs('Initializing FastSurfer pipeline...\nRun started by AI assistant');
      setChatNotifications((previous) => [...previous, {
        role: 'info',
        content: `FastSurfer analysis started for case "${effectiveName}". You can monitor progress in the output panel.`,
      }]);
      void fetchAvailableCases();
    } catch (error) {
      console.error('Agent-triggered run failed:', error);
      setChatNotifications((previous) => [...previous, { role: 'info', content: 'Failed to start FastSurfer run triggered by assistant.' }]);
    }
  }, [availableCases, currentCaseId, currentCaseTitle, fetchAvailableCases, initialWorkspaceId, navigate, runInputOptions, setActiveCaseId, setChatNotifications, setLogs, volumes]);

  const handleRunFastSurfer = useCallback(() => {
    if (!currentCaseId && !isRunFailed(runStatus)) {
      alert('Please create or load a case first.');
      return;
    }
    setShowConfirm(true);
    setQueueMessage("FastSurfer will run on the dedicated analysis server. This may take 15-60 minutes depending on the hardware.");
  }, [currentCaseId, runStatus]);

  const handleCancel = useCallback(async () => {
    if (!currentCaseId) return;
    if (!confirm('Are you sure you want to cancel this run?')) return;

    try {
      await api.cancelCaseRun(currentCaseId);
      setRunStatus('canceled');
      setChatNotifications((previous) => [...previous, { role: 'info', content: 'Run canceled by user.' }]);
    } catch (error) {
      console.error('Failed to cancel run', error);
    }
  }, [currentCaseId, setChatNotifications]);

  const confirmRun = useCallback(async (params: FastSurferParams) => {
    if (!currentCaseId || !initialWorkspaceId) {
      throw new Error('No active case selected');
    }
    if (!params.input_artifact_id) {
      throw new Error('Choose an input volume for FastSurfer');
    }
    const currentCaseName = currentCaseTitle ?? availableCases.find((caseItem) => caseItem.case_id === currentCaseId)?.subject_name ?? null;
    const formData = api.buildRunFormData(params, {
      activeCaseId: currentCaseId,
      currentCaseName,
      workspaceId: initialWorkspaceId,
    });

    try {
      const data = await api.startRun(formData);
      setShowConfirm(false);
      setRunStatus(data.status);
      setActiveCaseId(data.case_id);
      if (data.workspace_id) {
        void navigate(caseViewerPath(data.workspace_id, data.case_id, params.case_name ?? currentCaseName));
      }
      setLogs('Initializing FastSurfer pipeline...\nRun started');
      setChatNotifications([{
        role: 'info',
        content: `FastSurfer analysis started for case "${params.case_name ?? currentCaseName ?? currentCaseId}". You can monitor the progress in the output panel.`,
      }]);
    } catch (error: unknown) {
      console.error('Run error:', error);
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(message, { cause: error });
    }
  }, [availableCases, currentCaseId, currentCaseTitle, initialWorkspaceId, navigate, setActiveCaseId, setChatNotifications, setLogs]);

  useEffect(() => {
    if (!showConfirm || !initialWorkspaceId) {
      return;
    }
    void api.fetchQueueStatus(initialWorkspaceId)
      .then((data) => {
        const totalInfo = data.total > 0
          ? `There are currently ${data.total} other jobs in the queue (Active: ${data.active}, Queued: ${data.queued}).`
          : 'The server is currently idle.';
        setQueueMessage(`FastSurfer will run on the dedicated analysis server. This may take 15-60 minutes depending on the hardware. ${totalInfo}`);
      })
      .catch((error) => {
        console.error('Failed to fetch queue status', error);
        setQueueMessage("FastSurfer will run on the dedicated analysis server. This may take 15-60 minutes depending on the hardware.");
      });
  }, [initialWorkspaceId, showConfirm]);

  return {
    showConfirm,
    setShowConfirm,
    runStatus,
    setRunStatus,
    queueMessage,
    handleAgentRunFastSurfer,
    handleRunFastSurfer,
    handleCancel,
    confirmRun,
  };
}
