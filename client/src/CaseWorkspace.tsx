import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';

import { useAppSession } from './auth/sessionContext';
import { CaseWorkspaceRightPanel } from './components/CaseWorkspaceRightPanel';
import { CaseWorkspaceToolbar, type WorkspaceRightPanel } from './components/CaseWorkspaceToolbar';
import { DownloadCaseModal } from './components/DownloadCaseModal';
import { LayerPickerModal } from './components/LayerPickerModal';
import type { ChatMessage, GuiCommand, LocationInfo, MriViewerRef } from './types';
import { ConfirmationModal } from './components/ConfirmationModal';
import { UploadCaseModal } from './components/UploadCaseModal';
import { ErrorBoundary } from './components/ErrorBoundary';
import { STATUS_CONFIG, isRunActive, isRunDone, isRunFailed } from './constants';
import { useCaseWorkspaceController } from './hooks/useCaseWorkspaceController';
import { useCaseDownloads } from './hooks/useCaseDownloads';
import { useGuiStateSync } from './hooks/useGuiStateSync';
import { useHorizontalPaneResize } from './hooks/useHorizontalPaneResize';
import { useWorkspaceVolumeState } from './hooks/useWorkspaceVolumeState';
import { type LayerType, type OutputVolume, type Volume } from './types';
import { makeDrawingFilename, type DrawingLut, type DrawingSession } from './neurocadeViewer/nativeDrawing';
import { fetchOutputsList, saveGeneratedVolume } from './utils/api';
import { workspaceCasesPath } from './utils/caseRoutes';
import { defaultPaneWidth } from './utils/guiSession';
import { outputVolumeLayerType } from './utils/layerBuilders';

const NeuroCadeCaseViewer = lazy(() => import('./neurocadeViewer/NeuroCadeCaseViewer').then(module => ({ default: module.NeuroCadeCaseViewer })));
const CaseManagerModal = lazy(() => import('./components/CaseManagerModal').then(module => ({ default: module.CaseManagerModal })));

interface CaseWorkspaceProps {
  initialCaseId?: string | null;
  initialWorkspaceId?: string | null;
}

const WEBGPU_FALLBACK_WARNING = [
  'Viewer warning: WebGPU initialization failed; using WebGL2, so layer visibility and windowing may be significantly slower.',
  'For sandboxed Chromium on Linux with NVIDIA graphics, enable "Default ANGLE Vulkan" at chrome://flags/#default-angle-vulkan or launch Chromium with --use-angle=vulkan.',
].join(' ');
const WEBGPU_WARNING_STORAGE_PREFIX = 'neurocade.webgpu-warning.v1.';

interface SavedNativeDrawing {
  filename: string;
  data: Uint8Array;
  lut: DrawingLut;
  source?: DrawingSession['source'];
}

function selectCurrentIntensityInput(options: OutputVolume[], volumes: Volume[]): OutputVolume | null {
  const inputOptions = options.filter((option) => option.kind === 'volume' && option.type === 'intensity');
  if (inputOptions.length === 0) return null;

  const visibleIntensityLayers = volumes.filter((volume) => volume.type === 'intensity' && volume.visible);
  const loadedIntensityLayers = volumes.filter((volume) => volume.type === 'intensity');
  for (const layer of [...visibleIntensityLayers, ...loadedIntensityLayers]) {
    const byArtifact = layer.artifactId ? inputOptions.find((option) => option.id === layer.artifactId) : undefined;
    if (byArtifact) return byArtifact;
    const byFilename = inputOptions.find((option) => option.filename === layer.filename);
    if (byFilename) return byFilename;
  }

  return inputOptions.find((option) => option.visible === true) ?? inputOptions[0] ?? null;
}

function CaseWorkspace({ initialCaseId = null, initialWorkspaceId = null }: CaseWorkspaceProps) {
  const navigate = useNavigate();
  const { session } = useAppSession();
  const [volumes, setVolumes] = useState<Volume[]>([]);
  const [showCaseManager, setShowCaseManager] = useState(false);
  const [currentLocation, setCurrentLocation] = useState<LocationInfo | null>(null);
  const [requestedCursor, setRequestedCursor] = useState<[number, number, number] | null>(null);
  const [rightPanel, setRightPanel] = useState<WorkspaceRightPanel>('chat');
  const [chatClearRequestToken, setChatClearRequestToken] = useState(0);
  const [isChatClearing, setIsChatClearing] = useState(false);
  const [viewerDiagnostics, setViewerDiagnostics] = useState<ChatMessage[]>([]);
  const [layerPanelOpen, setLayerPanelOpen] = useState(true);
  const [isLight, setIsLight] = useState(false);
  const [analysisToolId, setAnalysisToolId] = useState('');
  const [layerPickerType, setLayerPickerType] = useState<LayerType | null>(null);
  const [layerPickerOptions, setLayerPickerOptions] = useState<OutputVolume[]>([]);
  const [layerPickerLoading, setLayerPickerLoading] = useState(false);
  const [layerPickerError, setLayerPickerError] = useState<string | null>(null);
  const [layerPanelWidth, startLayerPanelResize] = useHorizontalPaneResize(defaultPaneWidth(220, 280), { minWidth: 180, maxWidth: 480, edge: 'right' });
  const [rightPanelWidth, startRightPanelResize] = useHorizontalPaneResize(defaultPaneWidth(300, 380), { minWidth: 260, maxWidth: 620, edge: 'left' });

  const mriViewerRef = useRef<MriViewerRef>(null);
  const currentWorkspace = session?.workspaces.find((workspace) => workspace.id === initialWorkspaceId) ?? null;

  const controller = useCaseWorkspaceController({
    initialCaseId,
    initialWorkspaceId,
    navigate,
    volumes,
    setVolumes,
  });
  const activeCaseId = controller.activeCaseId ?? initialCaseId ?? controller.uploadState.caseId;
  const downloads = useCaseDownloads({
    caseId: activeCaseId,
    caseTitle: controller.currentCaseTitle ?? activeCaseId,
    volumes,
  });
  const currentAnalysisInput = useMemo(
    () => selectCurrentIntensityInput(controller.runInputOptions, volumes),
    [controller.runInputOptions, volumes],
  );
  const chatMessages = useMemo(
    () => [...controller.chatNotifications, ...viewerDiagnostics],
    [controller.chatNotifications, viewerDiagnostics],
  );
  const terminalOutput = useMemo(() => {
    const diagnostics = viewerDiagnostics
      .map((message) => `[viewer] ${typeof message.content === 'string' ? message.content : 'Viewer diagnostic'}`)
      .join('\n');
    return [controller.logs.trim(), diagnostics].filter(Boolean).join('\n\n');
  }, [controller.logs, viewerDiagnostics]);

  const handleViewerBackendChange = useCallback((backend: 'webgpu' | 'webgl2' | null) => {
    if (backend !== 'webgl2') return;
    console.warn(`[NeuroCade viewer] ${WEBGPU_FALLBACK_WARNING}`);
    const storageKey = `${WEBGPU_WARNING_STORAGE_PREFIX}${session?.user.id ?? 'anonymous'}`;
    try {
      if (window.localStorage.getItem(storageKey)) return;
      window.localStorage.setItem(storageKey, 'shown');
    } catch (error) {
      console.warn('[NeuroCade viewer] Could not persist the performance warning state:', error);
    }
    setViewerDiagnostics((current) => (
      current.some((message) => message.content === WEBGPU_FALLBACK_WARNING)
        ? current
        : [...current, { role: 'info', severity: 'warning', content: WEBGPU_FALLBACK_WARNING }]
    ));
  }, [session?.user.id]);

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
    currentIntensityArtifactId: currentAnalysisInput?.id ?? null,
    currentIntensityVolume: currentAnalysisInput?.filename ?? null,
    isRunActive,
    onSyncResponse: data => {
      data.commands.forEach((command: GuiCommand) => {
        const payload = command.payload;
        if (command.type === 'move_cursor') {
          const position = payload.position as [number, number, number];
          setRequestedCursor(position);
          setTimeout(() => setRequestedCursor(null), 500);
        } else if (command.type === 'load_layer') {
          volumeState.handleLoadLayerCommand({
            downloadPath: String(payload.download_path),
            filename: String(payload.filename),
            name: typeof payload.name === 'string' ? payload.name : undefined,
            type: typeof payload.type === 'string' ? payload.type : undefined,
            lut: typeof payload.lut === 'string' ? payload.lut : undefined,
            customLutDownloadUrl: typeof payload.custom_lut_download_path === 'string' ? payload.custom_lut_download_path : undefined,
            curvatureDownloadUrl: typeof payload.curvature_download_path === 'string' ? payload.curvature_download_path : undefined,
            annotationDownloadUrl: typeof payload.annotation_download_path === 'string' ? payload.annotation_download_path : undefined,
            visible: typeof payload.visible === 'boolean' ? payload.visible : undefined,
          });
        } else if (command.type === 'remove_layers') {
          volumeState.handleRemoveLayersCommand(payload.layer_ids as string[]);
        } else if (command.type === 'reorder_layer') {
          volumeState.reorderVolume(
            String(payload.layer_id),
            String(payload.target_layer_id),
            payload.position === 'before' ? 'before' : 'after',
          );
        } else if (command.type === 'set_layer_visibility') {
          volumeState.handleSetLayerVisibilityCommand(
            payload.changes as { layer_id: string; visible: boolean }[],
          );
        } else if (command.type === 'set_layer_display') {
          volumeState.handleSetLayerDisplayCommand(
            payload.layer_ids as string[],
            payload.updates as {
              opacity?: number;
              brightness?: number;
              contrast?: number;
              surface_color_mode?: 'solid' | 'curvature' | 'annotation';
            },
          );
        }
      });
    },
    onError: error => {
      console.error('Failed to sync GUI state with NeuroCade runtime:', error);
    },
  });

  useEffect(() => {
    window.neurocadeElectron?.setTitlebarTheme(isLight ? 'light' : 'dark');
  }, [isLight]);

  const handleAnalyze = () => {
    const tool = controller.analysisTools.find((item) => item.id === analysisToolId)
      ?? controller.analysisTools[0];
    if (!tool) return;
    setRightPanel('results');
    controller.handleRunAnalysis(tool.id);
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
    volumeState.handleLoadLayerCommand({
      downloadPath: artifact.downloadPath,
      filename: artifact.name,
      type: 'segmentation',
      lut: drawing.lut,
      visible: true,
    });
  }, [controller.activeCaseId, controller.uploadState.caseId, initialCaseId, volumeState]);

  const loadSelectedLayer = useCallback((option: OutputVolume) => {
    const layerType = outputVolumeLayerType(option);
    volumeState.handleLoadLayerCommand({
      downloadPath: option.downloadUrl,
      filename: option.filename,
      type: layerType,
      lut: option.lut,
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
  const selectedAnalysisTool = controller.analysisTools.find((tool) => tool.id === analysisToolId)
    ?? controller.analysisTools[0]
    ?? null;
  const modalAnalysisTool = controller.analysisTools.find((tool) => tool.id === controller.selectedAnalysisToolId)
    ?? selectedAnalysisTool;
  const displayedRunStatus = controller.isSubmittingRun ? 'starting' : controller.runStatus;
  const terminalStatusMessage = isRunDone(displayedRunStatus)
    ? '✓ Analysis job completed successfully.'
    : isRunFailed(displayedRunStatus)
      ? displayedRunStatus === 'canceled'
        ? 'Analysis job canceled.'
        : 'Analysis job failed.'
      : null;

  return (
    <div className={`nc-shell ${isLight ? 'nc-light' : ''}`}>
      <CaseWorkspaceToolbar
        workspaceName={currentWorkspace?.name ?? 'Workspace'}
        caseTitle={controller.currentCaseTitle ?? activeCaseId ?? 'Select Case'}
        hasCase={controller.hasUploadedCase}
        layerPanelOpen={layerPanelOpen}
        rightPanel={rightPanel}
        isLight={isLight}
        runStatus={controller.runStatus}
        isSubmittingRun={controller.isSubmittingRun}
        analysisTools={controller.analysisTools}
        selectedAnalysisToolId={selectedAnalysisTool?.id ?? ''}
        onBack={() => void navigate(workspaceBackPath)}
        onOpenCaseManager={() => setShowCaseManager(true)}
        onUpload={controller.handleUpload}
        onDownload={downloads.open}
        onToggleLayers={() => setLayerPanelOpen((value) => !value)}
        onSelectAnalysisTool={setAnalysisToolId}
        onAnalyze={handleAnalyze}
        onCancel={() => {
          setRightPanel('results');
          void controller.handleCancel();
        }}
        onToggleRightPanel={(panel) => setRightPanel((current) => current === panel ? null : panel)}
        onToggleTheme={() => setIsLight((value) => !value)}
      />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <ErrorBoundary label="NeuroCadeCaseViewer">
          <Suspense fallback={<div className="flex h-full min-w-0 flex-1 items-center justify-center bg-[var(--nc-bg-deep)] text-sm text-[var(--nc-tx-muted)]">Loading viewer...</div>}>
            <NeuroCadeCaseViewer
              ref={mriViewerRef}
              caseId={controller.activeCaseId ?? initialCaseId ?? controller.uploadState.caseId ?? 'no-case'}
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
              onBackendChange={handleViewerBackendChange}
            />
          </Suspense>
        </ErrorBoundary>

        {rightPanel && (
          <CaseWorkspaceRightPanel
            panel={rightPanel}
            width={rightPanelWidth}
            onStartResize={startRightPanelResize}
            runStatus={displayedRunStatus}
            terminalOutput={terminalOutput}
            terminalStatusMessage={terminalStatusMessage}
            chatMessages={chatMessages}
            currentLocation={currentLocation}
            getMriSnapshots={() => mriViewerRef.current?.getSnapshots() ?? null}
            workspaceId={initialWorkspaceId}
            caseId={activeCaseId}
            guiSessionId={controller.guiSessionId}
            chatClearRequestToken={chatClearRequestToken}
            isChatClearing={isChatClearing}
            onRequestChatClear={() => setChatClearRequestToken((token) => token + 1)}
            onChatClearStateChange={setIsChatClearing}
            onAssistantTurnComplete={() => void controller.fetchAnalysisTools()}
          />
        )}
      </div>

      <ConfirmationModal
        isOpen={controller.showConfirm}
        onClose={() => controller.setShowConfirm(false)}
        onConfirm={confirmRun}
        tool={modalAnalysisTool}
        message={controller.queueMessage}
        inputOptions={controller.runInputOptions}
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
        isOpen={downloads.isOpen}
        caseTitle={controller.currentCaseTitle ?? activeCaseId}
        artifacts={downloads.artifacts}
        loadingArtifacts={downloads.loading}
        error={downloads.error}
        actionLoading={downloads.action}
        onClose={downloads.close}
        onDownloadVolume={downloads.downloadVolume}
        onDownloadCase={downloads.downloadArchive}
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
