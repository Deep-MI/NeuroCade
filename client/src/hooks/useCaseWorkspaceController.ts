import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { NavigateFunction } from 'react-router';

import { isRunActive, isRunTerminal } from '../constants';
import { useCasePolling } from './useCasePolling';
import { useCaseUploadModal } from './useCaseUploadModal';
import { useAnalysisRunController } from './useAnalysisRunController';
import { type AnalysisToolSummary, type CaseFileEntry, type CaseSummary, type ChatMessage, type OutputVolume, type UploadState, type Volume } from '../types';
import { loadClosedCaseVolumes, removeCaseState } from '../utils/caseStorage';
import * as api from '../utils/api';
import { dedupeOutputVolumes, mergeOutputVolumesIntoViewerLayers, outputVolumesToViewerLayers, visibleOutputVolumes } from '../utils/caseLayers';
import { restorePersistedCaseLayers, savePersistedCaseLayers } from '../utils/caseLayerPersistence';
import { isCaseTransitionPending } from '../utils/caseLoading';
import { caseViewerPath } from '../utils/caseRoutes';
import { createGuiSessionId } from '../utils/guiSession';
import { workflowStatusNotificationId } from '../utils/runNotifications';
import type { IsCurrentRequest } from '../utils/casePolling';


interface UseCaseWorkspaceControllerArgs {
  initialCaseId: string | null;
  initialWorkspaceId: string | null;
  navigate: NavigateFunction;
  volumes: Volume[];
  setVolumes: Dispatch<SetStateAction<Volume[]>>;
  initialNotification?: string | null;
}

export function useCaseWorkspaceController({
  initialCaseId,
  initialWorkspaceId,
  navigate,
  volumes,
  setVolumes,
  initialNotification = null,
}: UseCaseWorkspaceControllerArgs) {
  const [viewerInputOptions, setViewerInputOptions] = useState<OutputVolume[]>([]);
  const [caseFiles, setCaseFiles] = useState<CaseFileEntry[]>([]);
  const [currentCaseTitle, setCurrentCaseTitle] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string>('');
  const [chatNotifications, setChatNotifications] = useState<ChatMessage[]>(
    initialNotification ? [{ role: 'info', content: initialNotification }] : [],
  );
  const initialNotificationRef = useRef(initialNotification);
  const [availableCases, setAvailableCases] = useState<CaseSummary[]>([]);
  const [analysisTools, setAnalysisTools] = useState<AnalysisToolSummary[]>([]);
  const [loadingCaseId, setLoadingCaseId] = useState<string | null>(initialCaseId);

  const [guiSessionId] = useState(createGuiSessionId);
  const suppressedRouteCaseRef = useRef<string | null>(null);
  const caseTitlesRef = useRef<Record<string, string>>({});
  const workspaceActionRef = useRef(0);
  const caseListRequestRef = useRef(0);

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

  const fetchCaseOutputs = useCallback(async (
    caseId: string,
    actionId?: number,
    isCurrent?: IsCurrentRequest,
    updateViewer = true,
  ) => {
    const generation = actionId ?? workspaceActionRef.current;
    try {
      const data = await api.fetchOutputsList(caseId);
      if (isStaleWorkspaceAction(generation) || (isCurrent && !isCurrent())) return;
      const dedupedVolumes = dedupeOutputVolumes(data.volumes);
      const inputOptions = dedupedVolumes.filter((volume) => volume.kind === 'volume' && volume.type === 'intensity');
      setViewerInputOptions(inputOptions);
      if (!updateViewer || dedupedVolumes.length === 0) {
        return;
      }
      const closedFilenames = new Set(loadClosedCaseVolumes(caseId));
      const visibleVolumes = visibleOutputVolumes(dedupedVolumes, closedFilenames);
      if (actionId !== undefined) {
        setVolumes(outputVolumesToViewerLayers(visibleVolumes));
      } else {
        setVolumes((current) => mergeOutputVolumesIntoViewerLayers(current, visibleVolumes));
      }
    } catch (error) {
      console.error('Error fetching outputs:', error);
    }
  }, [isStaleWorkspaceAction, setVolumes]);

  const fetchCaseFiles = useCallback(async (caseId: string, actionId?: number, isCurrent?: IsCurrentRequest) => {
    const generation = actionId ?? workspaceActionRef.current;
    try {
      const entries = await api.fetchCaseFiles(caseId);
      if (isStaleWorkspaceAction(generation) || (isCurrent && !isCurrent())) return;
      setCaseFiles([...entries].sort((left, right) => (
        left.type === right.type
          ? left.path.localeCompare(right.path)
          : left.type === 'directory' ? -1 : 1
      )));
    } catch (error) {
      if (!isStaleWorkspaceAction(generation) && (!isCurrent || isCurrent())) setCaseFiles([]);
      console.error('Error fetching case files:', error);
    }
  }, [isStaleWorkspaceAction]);

  const fetchAvailableCases = useCallback(async () => {
    const requestId = ++caseListRequestRef.current;
    try {
      const data = await api.fetchCases(initialWorkspaceId);
      if (caseListRequestRef.current !== requestId) return;
      setAvailableCases(data.cases);
      const activeCase = currentCaseId ? data.cases.find((caseItem) => caseItem.id === currentCaseId) : null;
      if (activeCase?.title) {
        setCurrentCaseTitle(activeCase.title);
      }
    } catch (error) {
      console.error('Error fetching cases:', error);
    }
  }, [currentCaseId, initialWorkspaceId]);

  const fetchLogs = useCallback(async (caseId: string, actionId?: number, isCurrent?: IsCurrentRequest) => {
    const generation = actionId ?? workspaceActionRef.current;
    try {
      const text = await api.fetchLogs(caseId);
      if (isStaleWorkspaceAction(generation) || (isCurrent && !isCurrent())) return;
      setLogs(text);
    } catch (error) {
      console.error('Error fetching logs:', error);
    }
  }, [isStaleWorkspaceAction]);

  const fetchAnalysisTools = useCallback(async () => {
    try {
      setAnalysisTools(await api.fetchAnalysisTools());
    } catch (error) {
      console.error('Error fetching analysis tools:', error);
    }
  }, []);

  const runController = useAnalysisRunController({
    initialWorkspaceId,
    currentCaseId,
    currentCaseTitle,
    availableCases,
    navigate,
    fetchAvailableCases,
    setActiveCaseId,
    setLogs,
    setChatNotifications,
  });
  const { runId, runStatus, setRunId, setRunStatus, setRunError, setRunErrorCode } = runController;

  const setCurrentRun = useCallback((status: string, nextRunId?: string, errorMessage?: string | null, errorCode?: string | null) => {
    setRunStatus(status);
    setRunId(nextRunId ?? null);
    setRunError(errorMessage ?? null);
    setRunErrorCode(errorCode ?? null);
  }, [setRunId, setRunStatus, setRunError, setRunErrorCode]);

  const loadCase = useCallback(async (caseId: string) => {
    const actionId = startWorkspaceAction();
    setLoadingCaseId(caseId);
    setViewerInputOptions([]);
    setCaseFiles([]);
    setVolumes([]);
    setLogs('');
    setCurrentRun('idle');

    try {
      await fetchCaseOutputs(caseId, actionId);
      if (isStaleWorkspaceAction(actionId)) return;
      await fetchCaseFiles(caseId, actionId);
      if (isStaleWorkspaceAction(actionId)) return;
      await fetchLogs(caseId, actionId);
      if (isStaleWorkspaceAction(actionId)) return;

      setVolumes((serverVolumes) => restorePersistedCaseLayers(caseId, serverVolumes));

      try {
        const data = await api.fetchStatus(caseId);
        if (isStaleWorkspaceAction(actionId)) return;
        setCurrentRun(data.status ?? 'unknown', data.runId, data.errorMessage, data.errorCode);
      } catch {
        if (isStaleWorkspaceAction(actionId)) return;
        setCurrentRun('unknown');
      }

      setActiveCaseId(caseId);
      setCurrentCaseTitle(caseTitlesRef.current[caseId] ?? null);
      const importNotice = initialNotificationRef.current;
      initialNotificationRef.current = null;
      setChatNotifications([
        ...(importNotice ? [{ role: 'info' as const, content: importNotice }] : []),
        { role: 'info', content: `Loaded case ${caseTitlesRef.current[caseId] ?? caseId}.` },
      ]);
      if (suppressedRouteCaseRef.current === caseId) {
        suppressedRouteCaseRef.current = null;
      }
    } finally {
      if (!isStaleWorkspaceAction(actionId)) {
        setLoadingCaseId(null);
      }
    }
  }, [fetchCaseFiles, fetchCaseOutputs, fetchLogs, isStaleWorkspaceAction, setCurrentRun, setVolumes, startWorkspaceAction]);

  const handleRenameCase = useCallback(async (oldId: string, newId: string) => {
    const renamed = await api.updateCase(oldId, { title: newId });
    if (currentCaseId === oldId) {
      setActiveCaseId(renamed.id);
      setCurrentCaseTitle(renamed.title);
      if (initialWorkspaceId) {
        void navigate(caseViewerPath(initialWorkspaceId, renamed.id));
      }
    }
    await fetchAvailableCases();
  }, [currentCaseId, fetchAvailableCases, initialWorkspaceId, navigate]);

  const handleDeleteCase = useCallback(async (caseId: string) => {
    await api.deleteCase(caseId);
    removeCaseState(caseId);
    if (currentCaseId === caseId) {
      setActiveCaseId(null);
      setViewerInputOptions([]);
      setCaseFiles([]);
      setVolumes([]);
      setRunStatus('idle');
      setLogs('');
      setCurrentCaseTitle(null);
    }
    await fetchAvailableCases();
  }, [currentCaseId, fetchAvailableCases, setRunStatus, setVolumes]);

  const openCase = useCallback((caseId: string) => {
    const targetCase = availableCases.find((caseItem) => caseItem.id === caseId);
    const targetWorkspaceId = targetCase?.workspace_id ?? initialWorkspaceId;
    if (!targetWorkspaceId) return;
    void navigate(caseViewerPath(targetWorkspaceId, caseId));
  }, [availableCases, initialWorkspaceId, navigate]);

  const uploadModal = useCaseUploadModal({
    isBusy: isUploading,
    onCreateNewCaseUpload: async (files, caseName, metadata, importMode) => {
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
        const data = await api.createCaseWithUpload(files, initialWorkspaceId, caseName, metadata, importMode);
        if (isStaleWorkspaceAction(actionId)) {
          return;
        }
        setActiveCaseId(data.case_id);
        setCurrentCaseTitle(data.title);
        const createdCount = data.created_cases?.length ?? data.import_summary?.created_case_count ?? 1;
        if (createdCount > 1) {
          setChatNotifications((previous) => [
            ...previous,
            { role: 'info', content: `Created ${createdCount} cases. Opened ${data.title}; the other imported subjects are available in the case list.` },
          ]);
        }
        suppressedRouteCaseRef.current = data.case_id;
        void navigate(caseViewerPath(data.workspace_id, data.case_id));
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
    onCreateNewCaseLink: async (source, caseName, metadata, importMode) => {
      if (!initialWorkspaceId) throw new Error('No active workspace selected');
      const actionId = startWorkspaceAction();
      setIsUploading(true);
      try {
        const data = await api.createCaseWithLinkedDirectory(source, initialWorkspaceId, caseName, metadata, importMode);
        if (isStaleWorkspaceAction(actionId)) return;
        setActiveCaseId(data.case_id);
        setCurrentCaseTitle(data.title);
        const createdCount = data.created_cases?.length ?? data.import_summary?.created_case_count ?? 1;
        if (createdCount > 1) {
          setChatNotifications((previous) => [
            ...previous,
            { role: 'info', content: `Linked ${createdCount} cases. Opened ${data.title}; the other subjects are available in the case list.` },
          ]);
        }
        suppressedRouteCaseRef.current = data.case_id;
        void navigate(caseViewerPath(data.workspace_id, data.case_id));
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
        await fetchCaseOutputs(data.case_id, undefined, undefined, false);
        await fetchCaseFiles(data.case_id);
        await fetchLogs(data.case_id);
        try {
          const status = await api.fetchStatus(data.case_id);
          setCurrentRun(status.status ?? 'uploaded', status.runId, status.errorMessage, status.errorCode);
        } catch {
          setCurrentRun('uploaded');
        }
        await fetchAvailableCases();
        const importedFileCount = data.import_summary?.file_count ?? files.length;
        const uploadLabel = importedFileCount > 1 ? `${importedFileCount} files` : files[0].name;
        const detectedFormat = data.import_summary?.format;
        const formatNote = detectedFormat && detectedFormat !== 'generic'
          ? ` Detected ${detectedFormat === 'bids' ? 'BIDS' : detectedFormat === 'fastsurfer' ? 'FastSurfer' : detectedFormat === 'freesurfer' ? 'FreeSurfer' : detectedFormat}.`
          : '';
        setChatNotifications((previous) => [
          ...previous,
          { role: 'info', content: `Added "${uploadLabel}" to case "${data.title}".${formatNote}` },
        ]);
      } finally {
        setIsUploading(false);
      }
    } : undefined,
  });

  useEffect(() => {
    caseTitlesRef.current = Object.fromEntries(
      availableCases.map((caseItem) => [caseItem.id, caseItem.title]),
    );
  }, [availableCases]);

  useEffect(() => {
    void fetchAvailableCases();
    const interval = window.setInterval(() => {
      void fetchAvailableCases();
    }, 300000);
    return () => {
      window.clearInterval(interval);
      caseListRequestRef.current += 1;
    };
  }, [fetchAvailableCases]);

  useEffect(() => {
    void fetchAnalysisTools();
  }, [fetchAnalysisTools]);

  useEffect(() => {
    if (!currentCaseId || isCaseLoading) return;
    const timerId = setTimeout(() => savePersistedCaseLayers(currentCaseId, volumes), 500);
    return () => clearTimeout(timerId);
  }, [currentCaseId, isCaseLoading, volumes]);

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
    return () => {
      window.clearTimeout(timerId);
      startWorkspaceAction();
    };
  }, [initialCaseId, initialWorkspaceId, loadCase, startWorkspaceAction]);

  const pollLogs = useCallback((caseId: string, isCurrent: IsCurrentRequest) => fetchLogs(caseId, undefined, isCurrent), [fetchLogs]);
  const pollOutputs = useCallback((caseId: string, isCurrent: IsCurrentRequest) => fetchCaseOutputs(caseId, undefined, isCurrent), [fetchCaseOutputs]);
  const onPollingError = useCallback((error: unknown) => console.error('Polling error:', error), []);
  const onTerminalStatus = useCallback((status: string, terminalRunId: string, workflowId?: string) => {
    const workflowName = analysisTools.find((tool) => tool.id === workflowId)?.label ?? workflowId ?? 'Workflow';
    if (currentCaseId) void fetchCaseFiles(currentCaseId);
    setChatNotifications((previous) => [...previous, {
      notificationId: workflowStatusNotificationId(terminalRunId, status),
      role: 'info', content: `${workflowName} ${status}.`,
    }]);
  }, [analysisTools, currentCaseId, fetchCaseFiles]);

  useCasePolling({
    activeCaseId: isCaseLoading ? null : currentCaseId,
    runId,
    runStatus,
    isRunActive,
    fetchStatus: api.fetchStatus,
    fetchLogs: pollLogs,
    fetchOutputs: pollOutputs,
    onRunChange: setCurrentRun,
    onTerminalStatus,
    onError: onPollingError,
  });

  useEffect(() => {
    if (!currentCaseId || isCaseLoading || !isRunTerminal(runStatus)) {
      return;
    }
    const timerId = window.setTimeout(() => {
      void fetchLogs(currentCaseId);
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [currentCaseId, fetchLogs, isCaseLoading, runStatus]);

  const hasUploadedCase = currentCaseId !== null;
  const suggestedCaseName = currentCaseTitle
    ?? (currentCaseId ? (availableCases.find((caseItem) => caseItem.id === currentCaseId)?.title ?? currentCaseId) : '');

  return {
    guiSessionId,
    pendingUploadFiles: uploadModal.pendingUploadFiles,
    pendingLinkedSource: uploadModal.pendingLinkedSource,
    pendingUploadDefaultName: uploadModal.pendingUploadDefaultName,
    viewerInputOptions,
    caseFiles,
    currentCaseTitle,
    uploadState,
    showUploadModal: uploadModal.showUploadModal,
    showConfirm: runController.showConfirm,
    selectedAnalysisToolId: runController.selectedToolId,
    activeCaseId: currentCaseId,
    runStatus,
    isSubmittingRun: runController.isSubmittingRun,
    runError: runController.runError,
    runErrorCode: runController.runErrorCode,
    logs,
    chatNotifications,
    availableCases,
    analysisTools,
    isCaseLoading,
    queueMessage: runController.queueMessage,
    hasUploadedCase,
    suggestedCaseName,
    setShowConfirm: runController.setShowConfirm,
    handleRunAnalysis: runController.handleRunAnalysis,
    handleCancel: runController.handleCancel,
    confirmRun: runController.confirmRun,
    fetchAvailableCases,
    fetchAnalysisTools,
    handleRenameCase,
    handleDeleteCase,
    openCase,
    handleUpload: uploadModal.requestUploadFile,
    closeUploadModal: uploadModal.closeUploadModal,
    selectUploadFiles: uploadModal.selectUploadFiles,
    clearPendingSelection: uploadModal.clearPendingSelection,
    selectLinkedDirectory: uploadModal.selectLinkedDirectory,
    confirmCreateNewCaseUpload: uploadModal.confirmCreateNewCaseUpload,
    confirmAddToCaseUpload: uploadModal.confirmAddToCaseUpload,
  };
}
