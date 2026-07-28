import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { NavigateFunction } from 'react-router-dom';

import { isRunActive, isRunTerminal } from '../constants';
import { useCasePolling } from './useCasePolling';
import { useCaseUploadModal } from './useCaseUploadModal';
import { useFastSurferRunController } from './useFastSurferRunController';
import { type AnalysisToolSummary, type CaseSummary, type ChatMessage, type OutputVolume, type UploadState, type Volume } from '../types';
import { loadClosedCaseVolumes, removeCaseState } from '../utils/caseStorage';
import * as api from '../utils/api';
import { dedupeOutputVolumes, outputVolumesToViewerLayers, visibleOutputVolumes } from '../utils/caseLayers';
import { restorePersistedCaseLayers, savePersistedCaseLayers } from '../utils/caseLayerPersistence';
import { isCaseTransitionPending } from '../utils/caseLoading';
import { caseViewerPath } from '../utils/caseRoutes';
import { createGuiSessionId } from '../utils/guiSession';


interface UseCaseWorkspaceControllerArgs {
  initialCaseId: string | null;
  initialWorkspaceId: string | null;
  navigate: NavigateFunction;
  volumes: Volume[];
  setVolumes: Dispatch<SetStateAction<Volume[]>>;
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
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string>('');
  const [chatNotifications, setChatNotifications] = useState<ChatMessage[]>([]);
  const [availableCases, setAvailableCases] = useState<CaseSummary[]>([]);
  const [analysisTools, setAnalysisTools] = useState<AnalysisToolSummary[]>([]);
  const [loadingCaseId, setLoadingCaseId] = useState<string | null>(initialCaseId);

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
  const isCaseLoading = isCaseTransitionPending(initialCaseId, activeCaseId, loadingCaseId);
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
      const visibleVolumes = visibleOutputVolumes(dedupedVolumes, closedFilenames);
      setVolumes(outputVolumesToViewerLayers(visibleVolumes));
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

  const runController = useFastSurferRunController({
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
  });
  const { runStatus, setRunStatus } = runController;

  const loadCase = useCallback(async (caseId: string) => {
    const actionId = startWorkspaceAction();
    setLoadingCaseId(caseId);
    setRunInputOptions([]);
    setVolumes([]);

    try {
      await fetchCaseOutputs(caseId, actionId);
      if (isStaleWorkspaceAction(actionId)) return;
      await fetchLogs(caseId, actionId);
      if (isStaleWorkspaceAction(actionId)) return;

      setVolumes((serverVolumes) => restorePersistedCaseLayers(caseId, serverVolumes));

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
    } finally {
      if (!isStaleWorkspaceAction(actionId)) {
        setLoadingCaseId(null);
      }
    }
  }, [fetchCaseOutputs, fetchLogs, isStaleWorkspaceAction, setRunStatus, setVolumes, startWorkspaceAction]);

  const handleRenameCase = useCallback(async (oldId: string, newId: string) => {
    const renamed = await api.renameCase(oldId, newId);
    if (currentCaseId === oldId) {
      setActiveCaseId(renamed.new_id);
      setCurrentCaseTitle(renamed.new_title);
      if (initialWorkspaceId) {
        void navigate(caseViewerPath(initialWorkspaceId, renamed.new_id, renamed.new_title));
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
  }, [currentCaseId, fetchAvailableCases, setRunStatus, setVolumes]);

  const openCase = useCallback((caseId: string) => {
    const targetCase = availableCases.find((caseItem) => caseItem.case_id === caseId);
    const targetWorkspaceId = targetCase?.workspace_id ?? initialWorkspaceId;
    if (!targetWorkspaceId) return;
    void navigate(caseViewerPath(targetWorkspaceId, caseId, targetCase?.subject_name));
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
        void navigate(caseViewerPath(data.workspace_id, data.case_id, data.title));
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
    void fetchAvailableCases();
    const interval = window.setInterval(() => {
      void fetchAvailableCases();
    }, 300000);
    return () => window.clearInterval(interval);
  }, [fetchAvailableCases]);

  useEffect(() => {
    void api.fetchAnalysisTools()
      .then(setAnalysisTools)
      .catch((error) => {
        console.error('Error fetching analysis tools:', error);
        setAnalysisTools([]);
      });
  }, []);

  useEffect(() => {
    if (!currentCaseId) return;
    const timerId = setTimeout(() => savePersistedCaseLayers(currentCaseId, volumes), 500);
    return () => clearTimeout(timerId);
  }, [currentCaseId, volumes]);

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
    showConfirm: runController.showConfirm,
    activeCaseId: currentCaseId,
    runStatus,
    logs,
    chatNotifications,
    availableCases,
    analysisTools,
    isCaseLoading,
    queueMessage: runController.queueMessage,
    hasUploadedCase,
    suggestedCaseName,
    setShowConfirm: runController.setShowConfirm,
    handleRunFastSurfer: runController.handleRunFastSurfer,
    handleCancel: runController.handleCancel,
    confirmRun: runController.confirmRun,
    handleAgentRunFastSurfer: runController.handleAgentRunFastSurfer,
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
