import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { NavigateFunction } from 'react-router-dom';

import { isRunActive, isRunFailed, isRunTerminal } from '../constants';
import { useCasePolling } from './useCasePolling';
import { useCaseUploadModal } from './useCaseUploadModal';
import { isSurfaceLayer, type CaseSummary, type ChatMessage, type FastSurferParams, type OutputVolume, type UploadState, type Volume } from '../types';
import { loadCaseState, loadClosedCaseVolumes, removeCaseState, saveCaseState } from '../utils/caseStorage';
import * as api from '../utils/api';
import { dedupeOutputVolumes, outputVolumeToLayer, selectInitialIntensityOutputVolume, visibleOutputVolumes } from '../utils/caseLayers';
import { createGuiSessionId } from '../utils/guiSession';
import { isLayerFile } from '../utils/layerAliases';
import { resolveSurfaceLayerColorMode } from '../utils/surfaceColors';


interface UseCaseWorkspaceControllerArgs {
  initialCaseId: string | null;
  initialWorkspaceId: string | null;
  navigate: NavigateFunction;
  volumes: Volume[];
  setVolumes: Dispatch<SetStateAction<Volume[]>>;
}

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

function routeCaseSlug(workspaceId: string, caseId: string, fallbackTitle?: string | null) {
  const prefix = `${workspaceId}__`;
  if (caseId.startsWith(prefix)) return caseId.slice(prefix.length);
  if (fallbackTitle) return fallbackTitle;
  throw new Error('Case id must use the canonical workspace-prefixed format.');
}

export function useCaseWorkspaceController({
  initialCaseId,
  initialWorkspaceId,
  navigate,
  volumes,
  setVolumes,
}: UseCaseWorkspaceControllerArgs) {
  const [runInputOptions, setRunInputOptions] = useState<OutputVolume[]>([]);
  const [currentCaseTitle, setCurrentCaseTitle] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<string>('idle');
  const [logs, setLogs] = useState<string>('');
  const [chatNotifications, setChatNotifications] = useState<ChatMessage[]>([]);
  const [availableCases, setAvailableCases] = useState<CaseSummary[]>([]);
  const [queueMessage, setQueueMessage] = useState<string>("FastSurfer will run on the dedicated analysis server. This may take 15-60 minutes depending on the hardware.");

  const [guiSessionId] = useState(createGuiSessionId);
  const suppressedRouteCaseRef = useRef<string | null>(null);
  const caseTitlesRef = useRef<Record<string, string>>({});
  const autoOpenedTerminalCaseRef = useRef<string | null>(null);
  const workspaceActionRef = useRef(0);

  const startWorkspaceAction = useCallback(() => {
    workspaceActionRef.current += 1;
    return workspaceActionRef.current;
  }, []);

  const isStaleWorkspaceAction = useCallback((actionId: number) => workspaceActionRef.current !== actionId, []);

  const currentCaseId = activeCaseId ?? initialCaseId ?? null;
  const uploadState: UploadState = {
    status: isUploading ? 'uploading' : (currentCaseId ? 'uploaded' : 'idle'),
    caseId: currentCaseId,
  };

  const fetchCaseOutputs = useCallback(async (caseId: string, actionId?: number) => {
    try {
      const data = await api.fetchOutputsList(caseId);
      const dedupedVolumes = dedupeOutputVolumes(data.volumes);
      const inputOptions = dedupedVolumes.filter((volume) => volume.id && volume.kind === 'volume' && (volume.type ?? 'intensity') === 'intensity');
      setRunInputOptions(inputOptions);
      if (dedupedVolumes.length === 0) {
        return;
      }
      if (actionId !== undefined && isStaleWorkspaceAction(actionId)) return;
      const closedFilenames = new Set(loadClosedCaseVolumes(caseId));
      const initialIntensityVolume = selectInitialIntensityOutputVolume(dedupedVolumes);
      const visibleVolumes = visibleOutputVolumes(dedupedVolumes, closedFilenames);
      const hasOrigVolume = visibleVolumes.some((volume) => isLayerFile(volume.filename, 'orig.mgz'));
      const newLayers: Volume[] = visibleVolumes.map((volume) => (
        outputVolumeToLayer(volume, { hasOrigVolume, initialIntensityVolume })
      ));

      setVolumes(newLayers);
    } catch (error) {
      console.error('Error fetching outputs:', error);
    }
  }, [isStaleWorkspaceAction, setVolumes]);

  const fetchAvailableCases = useCallback(async () => {
    try {
      const data = await api.fetchCases(initialWorkspaceId);
      setAvailableCases(data.cases);
      const activeCase = currentCaseId ? data.cases.find((caseItem) => caseItem.case_id === currentCaseId) : null;
      if (activeCase?.subject_name) {
        setCurrentCaseTitle(activeCase.subject_name);
      }
    } catch (error) {
      console.error('Error fetching cases:', error);
    }
  }, [currentCaseId, initialWorkspaceId]);

  const fetchLogs = useCallback(async (caseId: string, actionId?: number) => {
    try {
      const text = await api.fetchLogs(caseId);
      if (actionId !== undefined && isStaleWorkspaceAction(actionId)) return;
      setLogs(text);
    } catch (error) {
      console.error('Error fetching logs:', error);
    }
  }, [isStaleWorkspaceAction]);

  const loadCase = useCallback(async (caseId: string) => {
    const actionId = startWorkspaceAction();
    setRunInputOptions([]);
    setVolumes([]);

    await fetchCaseOutputs(caseId, actionId);
    if (isStaleWorkspaceAction(actionId)) return;
    await fetchLogs(caseId, actionId);
    if (isStaleWorkspaceAction(actionId)) return;

    const saved = loadCaseState(caseId);
    if (saved && saved.volumes.length > 0) {
      const savedOrder = new Map(saved.volumes.flatMap((volume, index) => [
        [volume.id, index] as const,
        [volume.filename, index] as const,
      ]));
      setVolumes((serverVolumes) => {
        const restoredVolumes = serverVolumes.map((serverVolume) => {
          const persistedVolume = saved.volumes.find((volume) => volume.id === serverVolume.id || volume.filename === serverVolume.filename);
          if (!persistedVolume) {
            return serverVolume;
          }
          const restored = {
            ...serverVolume,
            visible: persistedVolume.visible,
            opacity: persistedVolume.opacity,
            renderIn3D: persistedVolume.renderIn3D,
            renderInSlices: persistedVolume.renderInSlices,
          };
          if (isSurfaceLayer(serverVolume) && persistedVolume.type === 'surface') {
            return {
              ...restored,
              surfaceColorMode: resolveSurfaceLayerColorMode({ ...serverVolume, surfaceColorMode: persistedVolume.surfaceColorMode ?? serverVolume.surfaceColorMode }),
              surfaceReferenceAffine: persistedVolume.surfaceReferenceAffine ?? serverVolume.surfaceReferenceAffine,
              curvatureNegativeThreshold: persistedVolume.curvatureNegativeThreshold ?? serverVolume.curvatureNegativeThreshold,
              curvaturePositiveThreshold: persistedVolume.curvaturePositiveThreshold ?? serverVolume.curvaturePositiveThreshold,
            };
          }
          if (!isSurfaceLayer(serverVolume) && persistedVolume.type !== 'surface') {
            return {
              ...restored,
              brightness: persistedVolume.brightness,
              contrast: persistedVolume.contrast,
            };
          }
          return restored;
        });
        return restoredVolumes
          .map((volume, index) => ({ volume, index }))
          .sort((a, b) => {
            const aOrder = savedOrder.get(a.volume.id) ?? savedOrder.get(a.volume.filename) ?? Number.MAX_SAFE_INTEGER;
            const bOrder = savedOrder.get(b.volume.id) ?? savedOrder.get(b.volume.filename) ?? Number.MAX_SAFE_INTEGER;
            return aOrder - bOrder || a.index - b.index;
          })
          .map(({ volume }) => volume);
      });
    }

    try {
      const data = await api.fetchStatus(caseId);
      if (isStaleWorkspaceAction(actionId)) return;
      setRunStatus(data.status ?? 'unknown');
    } catch {
      if (isStaleWorkspaceAction(actionId)) return;
      setRunStatus('unknown');
    }

    setActiveCaseId(caseId);
    setCurrentCaseTitle(caseTitlesRef.current[caseId] ?? null);
    setChatNotifications([{ role: 'info', content: `Loaded case ${caseTitlesRef.current[caseId] ?? caseId}.` }]);
    if (suppressedRouteCaseRef.current === caseId) {
      suppressedRouteCaseRef.current = null;
    }
  }, [fetchCaseOutputs, fetchLogs, isStaleWorkspaceAction, setVolumes, startWorkspaceAction]);

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
        void navigate(`/workspaces/${encodeURIComponent(data.workspace_id)}/cases/${encodeURIComponent(routeCaseSlug(data.workspace_id, data.case_id, effectiveName))}`);
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
  }, [availableCases, currentCaseId, currentCaseTitle, fetchAvailableCases, initialWorkspaceId, navigate, runInputOptions, volumes]);

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
  }, [currentCaseId]);

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
        void navigate(`/workspaces/${encodeURIComponent(data.workspace_id)}/cases/${encodeURIComponent(routeCaseSlug(data.workspace_id, data.case_id, params.case_name ?? currentCaseName))}`);
      }
      setLogs('Initializing FastSurfer pipeline...\nRun started');
      setChatNotifications([{
        role: 'info',
        content: `FastSurfer analysis started for case "${params.case_name ?? currentCaseName ?? currentCaseId}". You can monitor the progress in the output panel.`,
      }]);
    } catch (error: unknown) {
      console.error('Run error:', error);
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(message);
    }
  }, [availableCases, currentCaseId, currentCaseTitle, initialWorkspaceId, navigate]);

  const handleRenameCase = useCallback(async (oldId: string, newId: string) => {
    const renamed = await api.renameCase(oldId, newId);
    if (currentCaseId === oldId) {
      setActiveCaseId(renamed.new_id);
      setCurrentCaseTitle(renamed.new_title);
      if (initialWorkspaceId) {
        void navigate(`/workspaces/${encodeURIComponent(initialWorkspaceId)}/cases/${encodeURIComponent(routeCaseSlug(initialWorkspaceId, renamed.new_id, renamed.new_title))}`);
      }
    }
    await fetchAvailableCases();
  }, [currentCaseId, fetchAvailableCases, initialWorkspaceId, navigate]);

  const handleDeleteCase = useCallback(async (caseId: string) => {
    await api.deleteCase(caseId);
    removeCaseState(caseId);
    if (currentCaseId === caseId) {
      setActiveCaseId(null);
      setRunInputOptions([]);
      setVolumes([]);
      setRunStatus('idle');
      setLogs('');
      setCurrentCaseTitle(null);
    }
    await fetchAvailableCases();
  }, [currentCaseId, fetchAvailableCases, setVolumes]);

  const openCase = useCallback((caseId: string) => {
    const targetCase = availableCases.find((caseItem) => caseItem.case_id === caseId);
    const targetWorkspaceId = targetCase?.workspace_id ?? initialWorkspaceId;
    if (!targetWorkspaceId) return;
    void navigate(`/workspaces/${encodeURIComponent(targetWorkspaceId)}/cases/${encodeURIComponent(routeCaseSlug(targetWorkspaceId, caseId, targetCase?.subject_name))}`);
  }, [availableCases, initialWorkspaceId, navigate]);

  const uploadModal = useCaseUploadModal({
    isBusy: isUploading,
    onCreateNewCaseUpload: async (files, caseName, metadata) => {
      if (!initialWorkspaceId) {
        throw new Error('No active workspace selected');
      }
      const actionId = startWorkspaceAction();
      suppressedRouteCaseRef.current = '__uploading__';
      setIsUploading(true);
      setCurrentCaseTitle(caseName);
      setRunStatus('idle');
      setLogs('');
      try {
        const data = await api.createCaseWithUpload(files, initialWorkspaceId, caseName, metadata);
        if (isStaleWorkspaceAction(actionId)) {
          return;
        }
        setActiveCaseId(data.case_id);
        setCurrentCaseTitle(data.title);
        suppressedRouteCaseRef.current = data.case_id;
        void navigate(`/workspaces/${encodeURIComponent(data.workspace_id)}/cases/${encodeURIComponent(routeCaseSlug(data.workspace_id, data.case_id, data.title))}`);
      } catch (error) {
        if (!isStaleWorkspaceAction(actionId)) {
          suppressedRouteCaseRef.current = null;
        }
        console.error('Case upload failed:', error);
        throw error;
      } finally {
        setIsUploading(false);
      }
    },
    onAddToCaseUpload: currentCaseId ? async (files) => {
      setIsUploading(true);
      try {
        const data = await api.addUploadToCase(files, currentCaseId);
        setActiveCaseId(data.case_id);
        setCurrentCaseTitle(data.title);
        await fetchCaseOutputs(data.case_id);
        await fetchLogs(data.case_id);
        try {
          const status = await api.fetchStatus(data.case_id);
          setRunStatus(status.status ?? 'uploaded');
        } catch {
          setRunStatus('uploaded');
        }
        await fetchAvailableCases();
        const uploadLabel = files.length > 1 ? `${files.length} files` : files[0].name;
        setChatNotifications((previous) => [
          ...previous,
          { role: 'info', content: `Added "${uploadLabel}" to case "${data.title}".` },
        ]);
      } finally {
        setIsUploading(false);
      }
    } : undefined,
  });

  useEffect(() => {
    caseTitlesRef.current = Object.fromEntries(
      availableCases.map((caseItem) => [caseItem.case_id, caseItem.subject_name]),
    );
  }, [availableCases]);

  useEffect(() => {
    const interval = setInterval(fetchAvailableCases, 60000);
    return () => clearInterval(interval);
  }, [fetchAvailableCases]);

  useEffect(() => {
    if (!currentCaseId) return;
    const timerId = setTimeout(() => saveCaseState(currentCaseId, volumes), 500);
    return () => clearTimeout(timerId);
  }, [currentCaseId, volumes]);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchAvailableCases();
    });
  }, [fetchAvailableCases]);

  useEffect(() => {
    if (!initialCaseId) {
      return undefined;
    }
    if (suppressedRouteCaseRef.current && suppressedRouteCaseRef.current !== initialCaseId) {
      return undefined;
    }
    const timerId = window.setTimeout(() => {
      void loadCase(initialCaseId);
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [initialCaseId, loadCase]);

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

  useCasePolling({
    activeCaseId: currentCaseId,
    runStatus,
    isRunActive,
    isRunTerminal,
    fetchStatus: api.fetchStatus,
    fetchLogs,
    fetchOutputs: fetchCaseOutputs,
    onStatusChange: setRunStatus,
    onTerminalStatus: (status) => {
      setChatNotifications((previous) => [...previous, { role: 'info', content: `Run ${status}.` }]);
    },
    onError: (error) => {
      console.error('Polling error:', error);
    },
  });

  useEffect(() => {
    if (!currentCaseId || !isRunTerminal(runStatus)) {
      return;
    }
    const timerId = window.setTimeout(() => {
      void fetchLogs(currentCaseId);
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [currentCaseId, fetchLogs, runStatus]);

  useEffect(() => {
    if (!currentCaseId) {
      autoOpenedTerminalCaseRef.current = null;
      return;
    }
    if (!isRunTerminal(runStatus)) {
      autoOpenedTerminalCaseRef.current = null;
      return;
    }
    if (!logs.trim() || autoOpenedTerminalCaseRef.current === currentCaseId) {
      return;
    }
    const timerId = window.setTimeout(() => {
      autoOpenedTerminalCaseRef.current = currentCaseId;
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [currentCaseId, logs, runStatus]);

  const hasUploadedCase = currentCaseId !== null;
  const suggestedCaseName = currentCaseTitle
    ?? (currentCaseId ? (availableCases.find((caseItem) => caseItem.case_id === currentCaseId)?.subject_name ?? currentCaseId) : '');

  return {
    guiSessionId,
    pendingUploadFiles: uploadModal.pendingUploadFiles,
    pendingUploadDefaultName: uploadModal.pendingUploadDefaultName,
    runInputOptions,
    currentCaseTitle,
    uploadState,
    showUploadModal: uploadModal.showUploadModal,
    showConfirm,
    activeCaseId: currentCaseId,
    runStatus,
    logs,
    chatNotifications,
    availableCases,
    queueMessage,
    hasUploadedCase,
    suggestedCaseName,
    setShowConfirm,
    handleRunFastSurfer,
    handleCancel,
    confirmRun,
    handleAgentRunFastSurfer,
    fetchAvailableCases,
    handleRenameCase,
    handleDeleteCase,
    openCase,
    handleUpload: uploadModal.requestUploadFile,
    closeUploadModal: uploadModal.closeUploadModal,
    selectUploadFiles: uploadModal.selectUploadFiles,
    confirmCreateNewCaseUpload: uploadModal.confirmCreateNewCaseUpload,
    confirmAddToCaseUpload: uploadModal.confirmAddToCaseUpload,
  };
}
