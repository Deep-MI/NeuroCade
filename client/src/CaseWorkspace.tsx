import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Check, Download, FileUp, Folder, Layers, LoaderCircle, MessageSquare, Moon, Play, RefreshCw, Square, Sun, TerminalSquare, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { useAppSession } from './auth/sessionContext';
import { DownloadCaseModal } from './components/DownloadCaseModal';
import type { LocationInfo, MriViewerRef } from './types';
import { ConfirmationModal } from './components/ConfirmationModal';
import { UploadCaseModal } from './components/UploadCaseModal';
import { ErrorBoundary } from './components/ErrorBoundary';
import { STATUS_CONFIG, isRunActive, isRunDone, isRunFailed } from './constants';
import { useCaseWorkspaceController } from './hooks/useCaseWorkspaceController';
import { useGuiStateSync } from './hooks/useGuiStateSync';
import { useHorizontalPaneResize } from './hooks/useHorizontalPaneResize';
import { useWorkspaceVolumeState } from './hooks/useWorkspaceVolumeState';
import { isSegmentationLayer, type ArtifactListItem, type LayerType, type OutputVolume, type Volume } from './types';
import { makeDrawingFilename, type DrawingLut, type DrawingSession } from './neurocadeViewer/nativeDrawing';
import { downloadArtifactFile, downloadCaseArchive as downloadCaseArchiveFile, fetchCaseArtifacts, fetchOutputsList, saveGeneratedVolume } from './utils/api';
import { workspaceCasesPath } from './utils/caseRoutes';
import { defaultPaneWidth } from './utils/guiSession';
import { isMaskLikeFilename, outputVolumeLayerType } from './utils/layerBuilders';
import { layerDisplayName } from './utils/layerAliases';

const NeuroCadeCaseViewer = lazy(() => import('./neurocadeViewer/NeuroCadeCaseViewer').then(module => ({ default: module.NeuroCadeCaseViewer })));
const CaseManagerModal = lazy(() => import('./components/CaseManagerModal').then(module => ({ default: module.CaseManagerModal })));
const Chat = lazy(() => import('./components/Chat').then(module => ({ default: module.Chat })));

interface CaseWorkspaceProps {
  initialCaseId?: string | null;
  initialWorkspaceId?: string | null;
}

const DOWNLOAD_ARTIFACT_TIMEOUT_MS = 8000;

interface SavedNativeDrawing {
  filename: string;
  data: Uint8Array;
  lut: DrawingLut;
  source?: DrawingSession['source'];
}

const LAYER_PICKER_LABELS: Record<LayerType, string> = {
  intensity: 'Intensity Volume',
  segmentation: 'Segmentation Volume',
  drawing: 'Drawing Source',
  surface: 'Surface Mesh',
};

function selectCurrentIntensityInput(options: OutputVolume[], volumes: Volume[]): OutputVolume | null {
  const inputOptions = options.filter((option) => option.id && option.kind === 'volume' && (option.type ?? 'intensity') === 'intensity');
  if (inputOptions.length === 0) return null;

  const visibleIntensityLayers = volumes.filter((volume) => (volume.type ?? 'intensity') === 'intensity' && volume.visible);
  const loadedIntensityLayers = volumes.filter((volume) => (volume.type ?? 'intensity') === 'intensity');
  for (const layer of [...visibleIntensityLayers, ...loadedIntensityLayers]) {
    const byArtifact = layer.artifactId ? inputOptions.find((option) => option.id === layer.artifactId) : undefined;
    if (byArtifact) return byArtifact;
    const byFilename = inputOptions.find((option) => option.filename === layer.filename);
    if (byFilename) return byFilename;
  }

  return inputOptions.find((option) => option.visible === true) ?? inputOptions[0] ?? null;
}

function AnalysisStatusIndicator({ status }: { status: string }) {
  if (isRunDone(status)) {
    return <span className="analysis-status-indicator is-done" title="Analysis finished" aria-label="Analysis finished"><Check size={13} /></span>;
  }
  if (isRunFailed(status)) {
    return <span className="analysis-status-indicator is-failed" title="Analysis failed" aria-label="Analysis failed"><X size={13} /></span>;
  }
  if (status === 'queued') {
    return <span className="analysis-status-indicator is-queued" title="Analysis queued" aria-label="Analysis queued"><span aria-hidden="true">...</span></span>;
  }
  if (isRunActive(status)) {
    return <span className="analysis-status-indicator is-running" title="Analysis running" aria-label="Analysis running"><LoaderCircle size={13} className="animate-spin" /></span>;
  }
  return null;
}

interface LayerPickerModalProps {
  type: LayerType;
  options: OutputVolume[];
  loadedFilenames: Set<string>;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRefresh: () => void;
  onLoad: (option: OutputVolume) => void;
}

function LayerPickerModal({ type, options, loadedFilenames, loading, error, onClose, onRefresh, onLoad }: LayerPickerModalProps) {
  const title = `Load ${LAYER_PICKER_LABELS[type]}`;

  return (
    <div className="fixed inset-0 z-[116] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative flex max-h-[82vh] w-full max-w-xl flex-col overflow-hidden rounded border border-[var(--nc-border)] bg-[var(--nc-bg-panel)] shadow-2xl">
        <div className="flex items-center gap-2 border-b border-[var(--nc-border)] px-4 py-3">
          <Layers size={14} className="text-[var(--nc-interactive)]" />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-[var(--nc-tx)]">{title}</h3>
            <div className="nc-mono text-[11px] text-[var(--nc-tx-dim)]">Select an existing file from this case directory.</div>
          </div>
          <button type="button" className="nc-btn nc-icon-btn" onClick={onRefresh} disabled={loading} title="Refresh case directory" aria-label="Refresh case directory">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <button type="button" className="nc-btn nc-icon-btn" onClick={onClose} title="Close" aria-label="Close layer picker">
            <X size={14} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {error && (
            <div className="mb-3 rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] px-3 py-2 text-sm text-[var(--nc-danger)]">
              {error}
            </div>
          )}

          {loading && options.length === 0 ? (
            <div className="nc-mono py-8 text-center text-sm text-[var(--nc-tx-muted)]">Loading case directory...</div>
          ) : options.length > 0 ? (
            <div className="divide-y divide-[var(--nc-border)] border-y border-[var(--nc-border)]">
              {options.map((option) => {
                const loaded = loadedFilenames.has(option.filename);
                const pathLabel = option.filename;
                const displayName = layerDisplayName(option);
                return (
                  <button
                    key={`${option.type ?? 'intensity'}:${option.filename}`}
                    type="button"
                    className="flex w-full items-center gap-3 px-2 py-2 text-left transition hover:bg-[var(--nc-row-hover)] disabled:cursor-default disabled:hover:bg-transparent"
                    disabled={loaded}
                    onClick={() => onLoad(option)}
                    title={loaded ? `${pathLabel} is already loaded` : `Load ${pathLabel}`}
                  >
                    <span className={`h-2 w-2 shrink-0 rounded-full ${type === 'surface' ? 'bg-[var(--nc-warning)]' : type === 'segmentation' ? 'bg-[var(--nc-success)]' : type === 'drawing' ? 'bg-[var(--nc-accent)]' : 'bg-[var(--nc-interactive)]'}`} />
                    <span className="min-w-0 flex-1">
                      <span className={`block truncate text-sm ${loaded ? 'text-[var(--nc-tx-faint)]' : 'text-[var(--nc-tx)]'}`}>{displayName}</span>
                      <span className="nc-mono block truncate text-[11px] text-[var(--nc-tx-dim)]">{pathLabel}</span>
                    </span>
                    <span className="nc-mono shrink-0 text-[11px] text-[var(--nc-tx-faint)]">{loaded ? 'Loaded' : 'Load'}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="rounded border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] px-3 py-8 text-center">
              <div className="text-sm text-[var(--nc-tx-muted)]">No matching files found in this case directory.</div>
              <div className="nc-mono mt-1 text-[11px] text-[var(--nc-tx-dim)]">New images can be added via Upload.</div>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-[var(--nc-border)] px-4 py-3">
          <div className="nc-mono text-[11px] text-[var(--nc-tx-dim)]">New images can be added via Upload.</div>
          <button type="button" className="nc-btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function CaseWorkspace({ initialCaseId = null, initialWorkspaceId = null }: CaseWorkspaceProps) {
  const navigate = useNavigate();
  const { session } = useAppSession();
  const [volumes, setVolumes] = useState<Volume[]>([]);
  const [showCaseManager, setShowCaseManager] = useState(false);
  const [currentLocation, setCurrentLocation] = useState<LocationInfo | null>(null);
  const [requestedCursor, setRequestedCursor] = useState<[number, number, number] | null>(null);
  const [showDownloadModal, setShowDownloadModal] = useState(false);
  const [downloadArtifacts, setDownloadArtifacts] = useState<ArtifactListItem[]>([]);
  const [downloadLoading, setDownloadLoading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadAction, setDownloadAction] = useState<'volume' | 'case' | null>(null);
  const [rightPanel, setRightPanel] = useState<'chat' | 'results' | null>('chat');
  const [chatClearRequestToken, setChatClearRequestToken] = useState(0);
  const [isChatClearing, setIsChatClearing] = useState(false);
  const [layerPanelOpen, setLayerPanelOpen] = useState(true);
  const [isLight, setIsLight] = useState(false);
  const [layerPickerType, setLayerPickerType] = useState<LayerType | null>(null);
  const [layerPickerOptions, setLayerPickerOptions] = useState<OutputVolume[]>([]);
  const [layerPickerLoading, setLayerPickerLoading] = useState(false);
  const [layerPickerError, setLayerPickerError] = useState<string | null>(null);
  const [layerPanelWidth, startLayerPanelResize] = useHorizontalPaneResize(defaultPaneWidth(220, 280), { minWidth: 180, maxWidth: 480, edge: 'right' });
  const [rightPanelWidth, startRightPanelResize] = useHorizontalPaneResize(defaultPaneWidth(300, 380), { minWidth: 260, maxWidth: 620, edge: 'left' });

  const mriViewerRef = useRef<MriViewerRef>(null);
  const downloadRequestIdRef = useRef(0);
  const currentWorkspace = session?.workspaces.find((workspace) => workspace.id === initialWorkspaceId) ?? null;

  const controller = useCaseWorkspaceController({
    initialCaseId,
    initialWorkspaceId,
    navigate,
    volumes,
    setVolumes,
  });
  const currentFastSurferInput = useMemo(
    () => selectCurrentIntensityInput(controller.runInputOptions, volumes),
    [controller.runInputOptions, volumes],
  );

  const volumeState = useWorkspaceVolumeState({
    activeCaseId: controller.activeCaseId,
    initialCaseId,
    uploadCaseId: controller.uploadState.caseId,
    setVolumes,
  });

  useGuiStateSync({
    workspaceId: initialWorkspaceId,
    caseId: controller.activeCaseId ?? initialCaseId ?? controller.uploadState.caseId,
    guiSessionId: controller.guiSessionId,
    runStatus: controller.runStatus,
    volumes,
    currentCaseId: controller.activeCaseId,
    currentIntensityArtifactId: currentFastSurferInput?.id ?? null,
    currentIntensityVolume: currentFastSurferInput?.filename ?? null,
    isRunActive,
    onSyncResponse: data => {
      if (data.requested_cursor_position) {
        setRequestedCursor(data.requested_cursor_position);
        setTimeout(() => setRequestedCursor(null), 500);
      }
      if (data.requested_load_volume) {
        volumeState.handleLoadVolumeCommand({
          downloadPath: data.requested_load_volume.download_path,
          filename: data.requested_load_volume.filename,
          name: data.requested_load_volume.name,
          type: data.requested_load_volume.type,
          lut: data.requested_load_volume.lut,
          customLutDownloadUrl: data.requested_load_volume.custom_lut_download_path,
          curvatureDownloadUrl: data.requested_load_volume.curvature_download_path,
          annotationDownloadUrl: data.requested_load_volume.annotation_download_path,
          visible: data.requested_load_volume.visible,
        });
      }
      if (data.requested_close_volume) volumeState.handleCloseVolumeCommand(data.requested_close_volume);
      if (data.requested_close_volumes) data.requested_close_volumes.forEach(volumeState.handleCloseVolumeCommand);
      if (data.requested_select_volumes) volumeState.handleSelectVolumesCommand(data.requested_select_volumes);
      if (data.requested_run_fastsurfer) {
        setRightPanel('results');
        void controller.handleAgentRunFastSurfer(data.requested_run_fastsurfer);
      }
      if (data.requested_adjust_display) volumeState.handleAdjustDisplayCommand(data.requested_adjust_display);
    },
    onError: error => {
      console.error('Failed to sync GUI state with NeuroCade runtime:', error);
    },
  });

  useEffect(() => {
    window.neurocadeElectron?.setTitlebarTheme(isLight ? 'light' : 'dark');
  }, [isLight]);

  useEffect(() => {
    downloadRequestIdRef.current += 1;
    setShowDownloadModal(false);
    setDownloadArtifacts([]);
    setDownloadError(null);
    setDownloadAction(null);
    setDownloadLoading(false);
  }, [controller.activeCaseId]);

  const buildFallbackDownloadArtifacts = useCallback((currentVolumes: Volume[]): ArtifactListItem[] => (
    currentVolumes
      .filter((volume) => Boolean(volume.url))
      .map((volume) => ({
        id: volume.id,
        name: volume.filename,
        kind: 'volume',
        downloadPath: volume.url,
        metadata: {
          volume_role: volume.type === 'segmentation' ? 'segmentation' : 'intensity',
          lut: isSegmentationLayer(volume) ? volume.lut : undefined,
          customLutDownloadUrl: isSegmentationLayer(volume) ? volume.customLutUrl : undefined,
          visible: volume.visible,
        },
      }))
  ), []);

  const closeDownloadModal = useCallback(() => {
    downloadRequestIdRef.current += 1;
    setShowDownloadModal(false);
    setDownloadLoading(false);
    setDownloadAction(null);
    setDownloadError(null);
  }, []);

  const openDownloadModal = useCallback(() => {
    const caseId = controller.activeCaseId ?? initialCaseId ?? null;
    if (!caseId) return;
    const fallbackArtifacts = buildFallbackDownloadArtifacts(volumes);
    const requestId = downloadRequestIdRef.current + 1;
    downloadRequestIdRef.current = requestId;
    setShowDownloadModal(true);
    setDownloadArtifacts(fallbackArtifacts);
    setDownloadLoading(true);
    setDownloadError(null);
    const timeoutPromise = new Promise<ArtifactListItem[]>((_, reject) => {
      window.setTimeout(() => reject(new Error('Timed out while loading the full volume list. You can still download the whole folder or any volume already open.')), DOWNLOAD_ARTIFACT_TIMEOUT_MS);
    });
    void Promise.race([fetchCaseArtifacts(caseId), timeoutPromise])
      .then((artifacts) => {
        if (downloadRequestIdRef.current !== requestId) return;
        setDownloadArtifacts(artifacts.length > 0 ? artifacts : fallbackArtifacts);
      })
      .catch((error: unknown) => {
        if (downloadRequestIdRef.current !== requestId) return;
        const message = error instanceof Error ? error.message : String(error);
        setDownloadError(fallbackArtifacts.length > 0 ? `${message} Showing currently loaded volumes.` : message);
      })
      .finally(() => {
        if (downloadRequestIdRef.current !== requestId) return;
        setDownloadLoading(false);
      });
  }, [buildFallbackDownloadArtifacts, controller.activeCaseId, initialCaseId, volumes]);

  const handleDownloadVolume = useCallback(async (artifactId: string) => {
    const artifact = downloadArtifacts.find((entry) => entry.id === artifactId);
    if (!artifact) {
      setDownloadError('Select a volume to download.');
      return;
    }
    setDownloadAction('volume');
    setDownloadError(null);
    try {
      await downloadArtifactFile(artifact);
      closeDownloadModal();
    } catch (error: unknown) {
      setDownloadError(error instanceof Error ? error.message : String(error));
    } finally {
      setDownloadAction(null);
    }
  }, [closeDownloadModal, downloadArtifacts]);

  const handleDownloadCaseArchive = useCallback(async () => {
    const caseId = controller.activeCaseId ?? initialCaseId ?? null;
    if (!caseId) {
      setDownloadError('No active case selected for download.');
      return;
    }
    setDownloadAction('case');
    setDownloadError(null);
    try {
      await downloadCaseArchiveFile(caseId, controller.currentCaseTitle ?? caseId);
      closeDownloadModal();
    } catch (error: unknown) {
      setDownloadError(error instanceof Error ? error.message : String(error));
    } finally {
      setDownloadAction(null);
    }
  }, [closeDownloadModal, controller.activeCaseId, controller.currentCaseTitle, initialCaseId]);

  const handleAnalyze = () => {
    setRightPanel('results');
    controller.handleRunFastSurfer();
  };

  const loadLayerPickerOptions = useCallback(async (type: LayerType) => {
    const caseId = controller.activeCaseId ?? initialCaseId ?? controller.uploadState.caseId;
    if (!caseId) {
      setLayerPickerOptions([]);
      setLayerPickerError('Load or create a case before selecting files from its directory.');
      return;
    }
    setLayerPickerLoading(true);
    setLayerPickerError(null);
    try {
      const data = await fetchOutputsList(caseId);
      const options = data.volumes
        .filter((volume) => outputVolumeLayerType(volume) === type)
        .filter((volume, index, list) => list.findIndex((candidate) => candidate.filename === volume.filename) === index)
        .sort((a, b) => a.filename.localeCompare(b.filename));
      setLayerPickerOptions(options);
    } catch (error: unknown) {
      setLayerPickerError(error instanceof Error ? error.message : String(error));
    } finally {
      setLayerPickerLoading(false);
    }
  }, [controller.activeCaseId, controller.uploadState.caseId, initialCaseId]);

  const openLayerPicker = useCallback((type: LayerType) => {
    setLayerPickerType(type);
    setLayerPickerOptions([]);
    setLayerPickerError(null);
    void loadLayerPickerOptions(type);
  }, [loadLayerPickerOptions]);

  const closeLayerPicker = useCallback(() => {
    setLayerPickerType(null);
    setLayerPickerError(null);
    setLayerPickerLoading(false);
  }, []);

  const saveNativeDrawing = useCallback(async (drawing: SavedNativeDrawing) => {
    const caseId = controller.activeCaseId ?? initialCaseId ?? controller.uploadState.caseId;
    if (!caseId) throw new Error('No active case selected.');
    const filename = makeDrawingFilename(drawing.filename);
    const drawingBuffer = new ArrayBuffer(drawing.data.byteLength);
    new Uint8Array(drawingBuffer).set(drawing.data);
    const artifact = await saveGeneratedVolume(caseId, {
      filename,
      blob: new Blob([drawingBuffer], { type: 'application/octet-stream' }),
      metadata: {
        source_artifact_id: drawing.source?.artifactId,
        source_layer_id: drawing.source?.layerId,
        layer_role: 'drawing',
        volume_role: 'segmentation',
        lut: drawing.lut,
      },
    });
    volumeState.handleLoadVolumeCommand({
      downloadPath: artifact.downloadPath,
      filename: artifact.name,
      type: 'segmentation',
      lut: drawing.lut,
      visible: true,
    });
  }, [controller.activeCaseId, controller.uploadState.caseId, initialCaseId, volumeState]);

  const loadSelectedLayer = useCallback((option: OutputVolume) => {
    const layerType = outputVolumeLayerType(option);
    volumeState.handleLoadVolumeCommand({
      downloadPath: option.downloadUrl,
      filename: option.filename,
      type: layerType,
      lut: option.lut ?? (layerType === 'segmentation' && isMaskLikeFilename(option.filename) ? 'binary' : undefined),
      customLutDownloadUrl: option.customLutDownloadUrl,
      curvatureDownloadUrl: option.curvatureDownloadUrl,
      annotationDownloadUrl: option.annotationDownloadUrl,
      visible: true,
    });
    closeLayerPicker();
  }, [closeLayerPicker, volumeState]);

  const confirmRun = async (params: Parameters<typeof controller.confirmRun>[0]) => {
    setRightPanel('results');
    await controller.confirmRun(params);
  };

  const workspaceBackPath = initialWorkspaceId ? workspaceCasesPath(initialWorkspaceId) : '/';
  const primaryAnalysisTool = controller.analysisTools[0] ?? null;
  const analysisToolLabel = primaryAnalysisTool?.label ?? 'FastSurfer';

  return (
    <div className={`nc-shell ${isLight ? 'nc-light' : ''}`}>
      <div className="nc-topbar">
        <div className="nc-logo">
          <img src="/logo-192.png" alt="" className="nc-logo-mark" aria-hidden="true" />
          <span>NeuroCade</span>
        </div>
        <div className="h-5 w-px bg-[var(--nc-border)]" />
        <button type="button" onClick={() => void navigate(workspaceBackPath)} className="nc-btn" data-testid="case-workspace-back">
          <ArrowLeft size={13} className="text-[var(--nc-interactive)]" />
          <span className="hidden max-w-[130px] truncate lg:inline">{currentWorkspace?.name ?? 'Workspace'}</span>
        </button>
        <button type="button" onClick={() => setShowCaseManager(true)} className="nc-btn nc-btn-active">
          <Folder size={13} className="text-[var(--nc-interactive)]" />
          <span className="max-w-[105px] truncate font-normal sm:max-w-[150px]">{controller.currentCaseTitle ?? controller.activeCaseId ?? initialCaseId ?? 'Select Case'}</span>
        </button>
        <button type="button" onClick={controller.handleUpload} className="nc-btn">
          <FileUp size={13} />
          <span className="hidden lg:inline">Upload</span>
          <span className="sr-only">Choose MRI File</span>
        </button>
        <button type="button" onClick={openDownloadModal} disabled={!controller.hasUploadedCase} className="nc-btn">
          <Download size={13} />
          <span className="hidden lg:inline">Download</span>
        </button>
        <div className="flex-1" />
        <button type="button" onClick={() => setLayerPanelOpen((value) => !value)} className={`nc-btn ${layerPanelOpen ? 'nc-btn-active' : ''}`}>
          <Layers size={13} />
          <span className="hidden lg:inline">Layers</span>
        </button>
        <button
          type="button"
          onClick={() => {
            if (isRunActive(controller.runStatus)) {
              setRightPanel('results');
              void controller.handleCancel();
            } else {
              handleAnalyze();
            }
          }}
          disabled={(!isRunActive(controller.runStatus) && !controller.hasUploadedCase && !isRunFailed(controller.runStatus)) || isRunDone(controller.runStatus)}
          className="nc-btn nc-btn-warning"
        >
          {isRunActive(controller.runStatus) ? <Square size={13} /> : <Play size={13} />}
          <span className="hidden lg:inline">{isRunActive(controller.runStatus) ? 'Cancel' : isRunDone(controller.runStatus) ? 'Analyzed' : isRunFailed(controller.runStatus) ? 'Rerun' : 'Analyze'}</span>
          <span className="sr-only">
            {isRunActive(controller.runStatus) ? 'Cancel Analysis' : `Run ${analysisToolLabel} Analysis`}
          </span>
        </button>
        <button type="button" onClick={() => setRightPanel((panel) => panel === 'chat' ? null : 'chat')} className={`nc-btn ${rightPanel === 'chat' ? 'nc-btn-active' : ''}`}>
          <MessageSquare size={13} />
          <span className="hidden lg:inline">Chat</span>
        </button>
        <button type="button" onClick={() => setRightPanel((panel) => panel === 'results' ? null : 'results')} className={`nc-btn ${rightPanel === 'results' ? 'nc-btn-active' : ''}`}>
          <TerminalSquare size={13} />
          <span className="hidden lg:inline">Terminal</span>
        </button>
        <button type="button" onClick={() => setIsLight((value) => !value)} className="nc-btn nc-icon-btn" title={isLight ? 'Switch to dark mode' : 'Switch to light mode'}>
          {isLight ? <Moon size={14} /> : <Sun size={14} />}
        </button>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <ErrorBoundary label="NeuroCadeCaseViewer">
          <Suspense fallback={<div className="flex h-full min-w-0 flex-1 items-center justify-center bg-[var(--nc-bg-deep)] text-sm text-[var(--nc-tx-muted)]">Loading viewer...</div>}>
            <NeuroCadeCaseViewer
              ref={mriViewerRef}
              volumes={volumes}
              caseLoading={controller.isCaseLoading}
              layerPanelOpen={layerPanelOpen}
              layerPanelWidth={layerPanelWidth}
              onStartLayerPanelResize={startLayerPanelResize}
              onUpdateVolume={volumeState.updateVolume}
              onReorderVolume={volumeState.reorderVolume}
              onRemoveVolume={volumeState.removeVolume}
              onOpenLayerPicker={openLayerPicker}
              onSaveDrawing={saveNativeDrawing}
              canAddLayers={Boolean(controller.activeCaseId ?? initialCaseId ?? controller.uploadState.caseId)}
              onLocationChange={setCurrentLocation}
              externalCoordinate={requestedCursor}
            />
          </Suspense>
        </ErrorBoundary>

        {rightPanel && (
          <aside className="nc-panel relative flex shrink-0 flex-col overflow-hidden border-l" style={{ width: rightPanelWidth }}>
            <div className="nc-pane-header">
              {rightPanel === 'chat' ? <MessageSquare size={12} /> : <TerminalSquare size={12} />}
              <span>{rightPanel === 'chat' ? 'Case Assistant' : 'Terminal Output'}</span>
              {rightPanel === 'chat' ? (
                <button
                  type="button"
                  className="chat-clear-button ml-auto"
                  onClick={() => setChatClearRequestToken((token) => token + 1)}
                  disabled={isChatClearing}
                  title="Clear chat context"
                  aria-label="Clear chat context"
                >
                  <span aria-hidden="true">+</span>
                </button>
              ) : (
                <AnalysisStatusIndicator status={controller.runStatus} />
              )}
            </div>
            {rightPanel === 'chat' ? (
              <ErrorBoundary label="Chat">
                <Suspense fallback={<div className="p-4 text-sm text-[var(--nc-tx-muted)]">Loading chat...</div>}>
                  <Chat
                    externalMessages={controller.chatNotifications}
                    style={{ flex: 1, minHeight: 0, marginTop: 0, borderRadius: 0 }}
                    hideHeader
                    currentLocation={currentLocation}
                    getMriSnapshots={() => mriViewerRef.current?.getSnapshots() ?? null}
                    workspaceId={initialWorkspaceId}
                    caseId={controller.activeCaseId ?? initialCaseId ?? controller.uploadState.caseId}
                    guiSessionId={controller.guiSessionId}
                    clearRequestToken={chatClearRequestToken}
                    onClearStateChange={setIsChatClearing}
                  />
                </Suspense>
              </ErrorBoundary>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col bg-[var(--nc-bg-deep)]">
                <pre data-testid="terminal-content" className="nc-mono min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap p-3 text-[11px] leading-[1.45] text-[var(--nc-tx-muted)]">
                  {controller.logs.trim() || 'No analysis run yet. Click Analyze to start.'}
                </pre>
              </div>
            )}
            <div
              role="separator"
              aria-orientation="vertical"
              className="nc-resize-handle nc-resize-handle-left"
              onMouseDown={startRightPanelResize}
            />
          </aside>
        )}
      </div>

      <ConfirmationModal
        isOpen={controller.showConfirm}
        onClose={() => controller.setShowConfirm(false)}
        onConfirm={confirmRun}
        title={`Start ${analysisToolLabel}`}
        message={controller.queueMessage}
        defaultCaseName={controller.suggestedCaseName}
        inputOptions={controller.runInputOptions}
        defaultInputArtifactId={currentFastSurferInput?.id ?? null}
      />

      {controller.showUploadModal && (
        <UploadCaseModal
          key={`${controller.pendingUploadFiles[0]?.name ?? ''}:${controller.pendingUploadFiles.length}:${controller.pendingUploadDefaultName}`}
          isOpen={controller.showUploadModal}
          filename={controller.pendingUploadFiles[0]?.name ?? null}
          fileCount={controller.pendingUploadFiles.length}
          defaultName={controller.pendingUploadDefaultName}
          addToCaseLabel={controller.currentCaseTitle ?? controller.activeCaseId ?? initialCaseId ?? null}
          onClose={controller.closeUploadModal}
          onSelectFiles={controller.selectUploadFiles}
          onCreateNewCase={controller.confirmCreateNewCaseUpload}
          onAddToCase={controller.confirmAddToCaseUpload}
        />
      )}

      <DownloadCaseModal
        isOpen={showDownloadModal}
        caseTitle={controller.currentCaseTitle ?? controller.activeCaseId ?? initialCaseId ?? null}
        artifacts={downloadArtifacts}
        loadingArtifacts={downloadLoading}
        error={downloadError}
        actionLoading={downloadAction}
        onClose={closeDownloadModal}
        onDownloadVolume={handleDownloadVolume}
        onDownloadCase={handleDownloadCaseArchive}
      />

      {layerPickerType && (
        <LayerPickerModal
          type={layerPickerType}
          options={layerPickerOptions}
          loadedFilenames={new Set(volumes.map((volume) => volume.filename))}
          loading={layerPickerLoading}
          error={layerPickerError}
          onClose={closeLayerPicker}
          onRefresh={() => void loadLayerPickerOptions(layerPickerType)}
          onLoad={loadSelectedLayer}
        />
      )}

      <Suspense fallback={null}>
        <CaseManagerModal
          isOpen={showCaseManager}
          onClose={() => setShowCaseManager(false)}
          availableCases={controller.availableCases}
          activeCaseId={controller.activeCaseId}
          statusConfig={STATUS_CONFIG}
          onRename={controller.handleRenameCase}
          onDelete={controller.handleDeleteCase}
          onLoadCase={controller.openCase}
        />
      </Suspense>
    </div>
  );
}

export default CaseWorkspace;
