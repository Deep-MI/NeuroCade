import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { SHOW_RENDER } from '@niivue/niivue';
import type Niivue from '@niivue/niivue';

import { isSurfaceLayer, type LocationInfo, type MriSnapshots, type MriViewerRef, type LayerType, type Volume } from '../types';
import { asNiivueInterop } from '../utils/niivueInterop';
import { layerType } from './niivueLayers';
import { LayerPanel } from './LayerPanel';
import { NiivuePane, type WindowSetting } from './NiivuePane';
import { ViewerHelpDialog } from './ViewerHelpDialog';
import { ViewerToolbar } from './ViewerToolbar';
import { useNativeDrawingSession, type SavedDrawingPayload } from './useNativeDrawingSession';
import { useViewerPaneInstance } from './useViewerPaneInstance';
import { useViewerWindowing } from './useViewerWindowing';
import { applyLayerDisplay, capturePaneSnapshot, previewLayerOpacity, referenceVolumeId as getReferenceVolumeId } from './viewerPaneAdapter';
import type { PaneRenderAction } from './viewerPaneAdapter';
import { VIEW_MODES, type NeuroCadeViewMode, type ViewerDragMode } from './viewerControls';

interface NeuroCadeCaseViewerProps {
  caseId: string;
  volumes: Volume[];
  caseLoading?: boolean;
  layerPanelOpen: boolean;
  layerPanelWidth: number;
  onStartLayerPanelResize: (event: React.MouseEvent<HTMLDivElement>) => void;
  onUpdateVolume: (id: string, updates: Partial<Volume>) => void;
  onReorderVolume: (sourceId: string, targetId: string, position: 'before' | 'after') => void;
  onRemoveVolume?: (id: string) => void;
  onOpenLayerPicker?: (type: LayerType) => void;
  onSaveDrawing?: (drawing: SavedDrawingPayload) => Promise<void>;
  canAddLayers?: boolean;
  onLocationChange?: (location: LocationInfo) => void;
  externalCoordinate?: [number, number, number] | null;
}

// Curated, MRI-sensible colormaps for intensity volumes, filtered against
// Niivue's actually-loaded colormaps at runtime.
const INTENSITY_COLORMAPS = ['gray', 'bone', 'hot', 'cool', 'viridis', 'plasma', 'inferno', 'jet'];

interface NeuroCadeViewerDebugState {
  activeViewMode: NeuroCadeViewMode;
  activeDragMode: ViewerDragMode;
  viewerReady: boolean;
  loadedLayerIds: string[];
  visibleLayerIds: string[];
  layerOpacities: Record<string, number>;
  layerOrder: string[];
  windowings: Record<string, { calMin: number; calMax: number }>;
}

declare global {
  interface Window {
    __neurocadeViewerDebug?: {
      getState: () => NeuroCadeViewerDebugState;
      getMeasures: () => PerformanceMeasure[];
      clearMeasures: () => void;
    };
  }
}

function markViewerMeasure(name: string, action: () => void): void {
  const start = `neurocade:${name}:start`;
  const end = `neurocade:${name}:end`;
  performance.mark(start);
  action();
  performance.mark(end);
  performance.measure(`neurocade:${name}`, start, end);
}

export const NeuroCadeCaseViewer = forwardRef<MriViewerRef, NeuroCadeCaseViewerProps>(({
  caseId,
  volumes,
  caseLoading = false,
  layerPanelOpen,
  layerPanelWidth,
  onStartLayerPanelResize,
  onUpdateVolume,
  onReorderVolume,
  onRemoveVolume,
  onOpenLayerPicker,
  onSaveDrawing,
  canAddLayers = false,
  onLocationChange,
  externalCoordinate,
}, ref) => {
  const {
    instanceRef,
    scheduleRefresh,
    scheduleDraw,
  } = useViewerPaneInstance();
  const colormapsReportedRef = useRef(false);
  const onLocationChangeRef = useRef(onLocationChange);
  onLocationChangeRef.current = onLocationChange;

  const [paneLoading, setPaneLoading] = useState(false);
  const [referenceVolumeId, setReferenceVolumeId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<NeuroCadeViewMode>('multi');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dragMode, setDragMode] = useState<ViewerDragMode>('pan');
  const [showOrientationLabels, setShowOrientationLabels] = useState(true);
  const [helpOpen, setHelpOpen] = useState(false);
  const [currentLocation, setCurrentLocation] = useState<LocationInfo | null>(null);
  const [expandedLayerId, setExpandedLayerId] = useState<string | null>(null);
  const [draggingLayerId, setDraggingLayerId] = useState<string | null>(null);
  const [dragTarget, setDragTarget] = useState<{ id: string; position: 'before' | 'after' } | null>(null);
  const [availableColormaps, setAvailableColormaps] = useState<string[]>([]);

  const loading = caseLoading || paneLoading;
  const selectedView = VIEW_MODES.find((mode) => mode.id === viewMode) ?? VIEW_MODES[VIEW_MODES.length - 1];
  const groupedLayers = useMemo(() => ({
    intensity: volumes.filter((volume) => layerType(volume) === 'intensity'),
    segmentation: volumes.filter((volume) => layerType(volume) === 'segmentation'),
    surface: volumes.filter(isSurfaceLayer),
  }), [volumes]);

  const intensityColormaps = useMemo(() => {
    const available = new Set(availableColormaps.map((name) => name.toLowerCase()));
    return INTENSITY_COLORMAPS.filter((name) => available.has(name));
  }, [availableColormaps]);

  const syncReferenceVolumeId = useCallback(() => {
    const nv = instanceRef.current;
    const nextReferenceVolumeId = nv ? getReferenceVolumeId(nv) : null;
    setReferenceVolumeId((current) => (current === nextReferenceVolumeId ? current : nextReferenceVolumeId));
  }, [instanceRef]);

  const {
    manualWindowingRef,
    windowings,
    setWindowings,
    ensureWindowingForLayer,
    updateWindowing,
    syncIntensityWindow,
    clearManualWindowing,
  } = useViewerWindowing({
    volumes,
    instanceRef,
    scheduleRefresh,
  });

  const schedulePaneRenderAction = useCallback((nv: Niivue, action: PaneRenderAction) => {
    if (!action) return;
    if (action.kind === 'draw') scheduleDraw(nv);
    if (action.kind === 'refresh') scheduleRefresh(nv);
  }, [scheduleDraw, scheduleRefresh]);

  const applyImmediateVolumeUpdate = useCallback((id: string, updates: Partial<Volume>) => {
    const source = volumes.find((volume) => volume.id === id);
    if (!source) return;
    const next = { ...source, ...updates } as Volume;
    const nv = instanceRef.current;
    if (nv) {
      const action = applyLayerDisplay(nv, id, next, updates);
      schedulePaneRenderAction(nv, action);
    }
  }, [instanceRef, schedulePaneRenderAction, volumes]);

  const handlePaneLoading = useCallback((isLoading: boolean) => {
    setPaneLoading(isLoading);
    if (!isLoading) requestAnimationFrame(syncReferenceVolumeId);
  }, [syncReferenceVolumeId]);

  const handleUpdateVolume = useCallback((id: string, updates: Partial<Volume>) => {
    applyImmediateVolumeUpdate(id, updates);
    onUpdateVolume(id, updates);
    requestAnimationFrame(syncReferenceVolumeId);
  }, [applyImmediateVolumeUpdate, onUpdateVolume, syncReferenceVolumeId]);

  useEffect(() => {
    const frame = requestAnimationFrame(syncReferenceVolumeId);
    return () => cancelAnimationFrame(frame);
  }, [syncReferenceVolumeId, volumes]);

  const previewVolumeOpacity = useCallback((id: string, opacity: number) => {
    const source = volumes.find((volume) => volume.id === id);
    if (!source) return;
    const next = { ...source, opacity } as Volume;
    const nv = instanceRef.current;
    if (nv) {
      const action = previewLayerOpacity(nv, id, next);
      schedulePaneRenderAction(nv, action);
    }
  }, [instanceRef, schedulePaneRenderAction, volumes]);

  const commitVolumeOpacity = useCallback((id: string, opacity: number) => {
    handleUpdateVolume(id, { opacity });
  }, [handleUpdateVolume]);

  const handlePaneColormaps = useCallback((colormaps: string[]) => {
    if (colormapsReportedRef.current) return;
    colormapsReportedRef.current = true;
    setAvailableColormaps(colormaps);
  }, []);

  const handlePaneLocation = useCallback((location: LocationInfo) => {
    setCurrentLocation(location);
    onLocationChangeRef.current?.(location);
  }, []);

  const {
    drawingSession,
    drawingLabels,
    canUndo,
    registerDrawingPane,
    updateDrawingOptions,
    beginBlankDrawing,
    beginDrawingFromSegmentation,
    handleDrawUndo,
    handleSaveDrawing,
    closeNativeDrawing,
  } = useNativeDrawingSession({
    canAddLayers,
    caseId,
    instanceRef,
    referenceVolumeId,
    onSaveDrawing,
  });

  const handlePaneReady = useCallback((nv: Niivue | null) => {
    instanceRef.current = nv;
    if (nv) registerDrawingPane(nv);
    requestAnimationFrame(syncReferenceVolumeId);
  }, [instanceRef, registerDrawingPane, syncReferenceVolumeId]);

  useImperativeHandle(ref, () => ({
    getSnapshots: (): MriSnapshots | null => {
      const nv = instanceRef.current;
      if (!nv) return null;
      const previousSliceType = nv.sliceType;
      const previousShowRender = nv.showRender;
      const capture = (sliceType: 0 | 1 | 2): string | null => {
        nv.sliceType = sliceType;
        nv.showRender = SHOW_RENDER.NEVER;
        nv.drawScene();
        return capturePaneSnapshot(nv);
      };
      try {
        const axial = capture(0);
        const coronal = capture(1);
        const sagittal = capture(2);
        if (!axial || !coronal || !sagittal) return null;
        return { axial, coronal, sagittal };
      } finally {
        nv.sliceType = previousSliceType;
        nv.showRender = previousShowRender;
        nv.drawScene();
      }
    },
  }), [instanceRef]);

  useEffect(() => {
    window.__neurocadeViewerDebug = {
      getState: () => {
        const loadedIds = new Set<string>();
        const actualWindowings: Record<string, { calMin: number; calMax: number }> = {};
        const nv = instanceRef.current;
        if (nv) {
          const interop = asNiivueInterop(nv);
          for (const loaded of interop.volumes) {
            if (!loaded.id) continue;
            loadedIds.add(loaded.id);
            actualWindowings[loaded.id] = {
              calMin: loaded.calMin ?? windowings[loaded.id]?.calMin ?? 0,
              calMax: loaded.calMax ?? windowings[loaded.id]?.calMax ?? 0,
            };
          }
          for (const mesh of interop.meshes ?? []) {
            if (mesh.id) loadedIds.add(mesh.id);
          }
        }
        return {
          activeViewMode: viewMode,
          activeDragMode: dragMode,
          viewerReady: Boolean(nv),
          loadedLayerIds: [...loadedIds],
          visibleLayerIds: volumes.filter((volume) => volume.visible).map((volume) => volume.id),
          layerOpacities: Object.fromEntries(volumes.map((volume) => [volume.id, volume.opacity])),
          layerOrder: volumes.map((volume) => volume.id),
          windowings: { ...windowings, ...actualWindowings },
        };
      },
      getMeasures: () => performance.getEntriesByType('measure')
        .filter((entry): entry is PerformanceMeasure => entry.entryType === 'measure' && entry.name.startsWith('neurocade:')),
      clearMeasures: () => {
        performance.getEntriesByType('measure')
          .filter((entry) => entry.name.startsWith('neurocade:'))
          .forEach((entry) => performance.clearMeasures(entry.name));
      },
    };
    return () => {
      delete window.__neurocadeViewerDebug;
    };
  }, [dragMode, instanceRef, viewMode, volumes, windowings]);

  const toggleExpandLayer = useCallback((id: string, type: LayerType) => {
    setExpandedLayerId((prev) => prev === id ? null : id);
    if (type === 'intensity') {
      ensureWindowingForLayer(id);
    }
  }, [ensureWindowingForLayer]);

  const handleDragModeChange = useCallback((mode: ViewerDragMode) => {
    markViewerMeasure(`tool:${mode}`, () => setDragMode(mode));
  }, []);

  const handleViewModeChange = useCallback((mode: NeuroCadeViewMode) => {
    markViewerMeasure(`view:${mode}`, () => setViewMode(mode));
  }, []);

  const resetView = useCallback(() => {
    const nv = instanceRef.current;
    if (!nv) return;
    clearManualWindowing();
    const nextWindowings: Record<string, WindowSetting> = {};
    const nvInterop = asNiivueInterop(nv);
    nvInterop.scaleMultiplier = 1;
    nvInterop.pan2Dxyzmm = [0, 0, 0, 1];
    nvInterop.azimuth = 110;
    nvInterop.elevation = 10;
    nvInterop.setClipPlane([2, 0, 0]);
    for (const loaded of nvInterop.volumes) {
      const source = volumes.find((volume) => volume.id === loaded.id);
      if (!source || isSurfaceLayer(source) || source.type === 'segmentation') continue;
      const calMin = loaded.robustMin ?? loaded.globalMin ?? loaded.calMin ?? 0;
      const calMax = loaded.robustMax ?? loaded.globalMax ?? loaded.calMax ?? 1;
      loaded.calMin = calMin;
      loaded.calMax = calMax;
      const volumeIndex = nvInterop.volumes.indexOf(loaded);
      if (volumeIndex >= 0) void nvInterop.setVolume(volumeIndex, { calMin, calMax });
      if (loaded.id && !nextWindowings[loaded.id]) {
        nextWindowings[loaded.id] = {
          calMin,
          calMax,
          globalMin: loaded.globalMin ?? calMin,
          globalMax: loaded.globalMax ?? calMax,
        };
      }
    }
    nvInterop.drawScene();
    for (const id of Object.keys(nextWindowings)) {
      onUpdateVolume(id, { brightness: 0, contrast: 1 });
    }
    if (Object.keys(nextWindowings).length > 0) {
      setWindowings((prev) => ({ ...prev, ...nextWindowings }));
    }
  }, [clearManualWindowing, instanceRef, onUpdateVolume, setWindowings, volumes]);

  // Drag-and-drop layer reordering is scoped within each layer section.
  const sameSectionLayer = useCallback((sourceId: string | null, target: Volume) => {
    if (!sourceId || sourceId === target.id) return false;
    const source = volumes.find((volume) => volume.id === sourceId);
    return Boolean(source && layerType(source) === layerType(target));
  }, [volumes]);

  const handleLayerDragOver = useCallback((event: React.DragEvent<HTMLDivElement>, target: Volume) => {
    if (!sameSectionLayer(draggingLayerId, target)) return;
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const position = event.clientY < rect.top + rect.height / 2 ? 'before' : 'after';
    setDragTarget((prev) => (prev?.id === target.id && prev.position === position ? prev : { id: target.id, position }));
  }, [draggingLayerId, sameSectionLayer]);

  const handleLayerDrop = useCallback((event: React.DragEvent<HTMLDivElement>, target: Volume) => {
    event.preventDefault();
    const sourceId = draggingLayerId ?? event.dataTransfer.getData('text/plain');
    if (sameSectionLayer(sourceId, target)) {
      onReorderVolume(sourceId, target.id, dragTarget?.id === target.id ? dragTarget.position : 'before');
    }
    setDraggingLayerId(null);
    setDragTarget(null);
  }, [draggingLayerId, dragTarget, onReorderVolume, sameSectionLayer]);

  const handleLayerReorderKeyDown = useCallback((event: React.KeyboardEvent<HTMLButtonElement>, layer: Volume, sectionLayers: Volume[]) => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    event.stopPropagation();
    const index = sectionLayers.findIndex((candidate) => candidate.id === layer.id);
    if (event.key === 'ArrowUp' && index > 0) {
      onReorderVolume(layer.id, sectionLayers[index - 1].id, 'before');
    }
    if (event.key === 'ArrowDown' && index >= 0 && index < sectionLayers.length - 1) {
      onReorderVolume(layer.id, sectionLayers[index + 1].id, 'after');
    }
  }, [onReorderVolume]);

  const handleLayerDragStart = useCallback((id: string, event: React.DragEvent<HTMLButtonElement>) => {
    setDraggingLayerId(id);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', id);
  }, []);

  const handleLayerDragEnd = useCallback(() => {
    setDraggingLayerId(null);
    setDragTarget(null);
  }, []);

  const handleLayerDragLeave = useCallback((id: string) => {
    if (dragTarget?.id === id) setDragTarget(null);
  }, [dragTarget?.id]);

  return (
    <>
      {layerPanelOpen && (
        <LayerPanel
          layerPanelWidth={layerPanelWidth}
          onStartLayerPanelResize={onStartLayerPanelResize}
          groupedLayers={groupedLayers}
          canAddLayers={canAddLayers}
          onOpenLayerPicker={onOpenLayerPicker}
          onRemoveVolume={onRemoveVolume}
          expandedLayerId={expandedLayerId}
          draggingLayerId={draggingLayerId}
          dragTarget={dragTarget}
          referenceVolumeId={referenceVolumeId}
          windowings={windowings}
          intensityColormaps={intensityColormaps}
          drawingSession={drawingSession}
          drawingLabels={drawingLabels}
          canUndo={canUndo}
          onToggleExpandLayer={toggleExpandLayer}
          onUpdateVolume={handleUpdateVolume}
          onPreviewVolumeOpacity={previewVolumeOpacity}
          onCommitVolumeOpacity={commitVolumeOpacity}
          onUpdateWindowing={updateWindowing}
          onLayerDragOver={handleLayerDragOver}
          onLayerDrop={handleLayerDrop}
          onLayerDragStart={handleLayerDragStart}
          onLayerDragEnd={handleLayerDragEnd}
          onLayerDragLeave={handleLayerDragLeave}
          onLayerReorderKeyDown={handleLayerReorderKeyDown}
          onUpdateDrawingOptions={updateDrawingOptions}
          onBeginBlankDrawing={beginBlankDrawing}
          onBeginDrawingFromSegmentation={(source) => { void beginDrawingFromSegmentation(source); }}
          onDrawUndo={handleDrawUndo}
          onSaveDrawing={() => { void handleSaveDrawing(); }}
          onCloseDrawing={() => closeNativeDrawing(true)}
        />
      )}

      <main className="nc-viewer-main min-w-0 flex-1 overflow-hidden bg-[var(--nc-bg-deep)]">
        <div className="nc-viewer-grid is-single">
          <NiivuePane
            cacheScope={caseId}
            sliceType={selectedView.sliceType}
            showRender={selectedView.showRender}
            volumes={volumes}
            windowings={windowings}
            manualWindowingIds={manualWindowingRef}
            dragMode={dragMode}
            externalCoordinate={externalCoordinate}
            reportLocation
            showOrientationLabels={showOrientationLabels}
            onReady={handlePaneReady}
            onLocationChange={handlePaneLocation}
            onIntensityWindowChange={syncIntensityWindow}
            onLoadingChange={handlePaneLoading}
            onError={setLoadError}
            onColormaps={handlePaneColormaps}
          />
          {loading && (
            <div className="nc-viewer-canvas-spinner" role="status" aria-label="Loading imaging data">
              <span className="mri-loading-spinner" />
            </div>
          )}
          {!loading && volumes.length === 0 && (
            <div className="nc-viewer-canvas-status">Select or upload a case volume to begin.</div>
          )}
          {loadError && (
            <div className="nc-viewer-canvas-error">{loadError}</div>
          )}
        </div>
        <ViewerToolbar
          dragMode={dragMode}
          viewMode={viewMode}
          location={currentLocation}
          showOrientationLabels={showOrientationLabels}
          onDragModeChange={handleDragModeChange}
          onViewModeChange={handleViewModeChange}
          onToggleOrientationLabels={() => setShowOrientationLabels((visible) => !visible)}
          onOpenHelp={() => setHelpOpen(true)}
          onResetView={resetView}
        />
      </main>

      {helpOpen && <ViewerHelpDialog onClose={() => setHelpOpen(false)} />}
    </>
  );
});

NeuroCadeCaseViewer.displayName = 'NeuroCadeCaseViewer';
