import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { Niivue } from '@niivue/niivue';

import { isSurfaceLayer, type LocationInfo, type MriSnapshots, type MriViewerRef, type LayerType, type Volume } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import { layerType, type NiivueViewerInterop } from './niivueLayers';
import { LayerPanel } from './LayerPanel';
import { NiivuePane, type WindowSetting } from './NiivuePane';
import { ViewerHelpDialog } from './ViewerHelpDialog';
import { ViewerToolbar } from './ViewerToolbar';
import { useNativeDrawingSession, type SavedDrawingPayload } from './useNativeDrawingSession';
import { useViewerPaneInstances } from './useViewerPaneInstances';
import { useViewerWindowing } from './useViewerWindowing';
import { applyLayerDisplay, capturePaneSnapshot, previewLayerOpacity, referenceVolumeId as getReferenceVolumeId } from './viewerPaneAdapter';
import type { PaneRenderAction } from './viewerPaneAdapter';
import { VIEW_MODES, type NeuroCadeViewMode, type ViewerDragMode, type ViewerSliceType } from './viewerControls';

interface NeuroCadeCaseViewerProps {
  volumes: Volume[];
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

// Each grid quadrant is a dedicated single-purpose Niivue instance: the three
// orthogonal planes plus the 3D render. CSS lays them out (no Niivue internal
// multiplanar), so the cells are uniform and never overlap.
const PANE_SLICE_TYPES: ViewerSliceType[] = [0, 1, 2, 4];

// Curated, MRI-sensible colormaps for intensity volumes, filtered against
// Niivue's actually-loaded colormaps at runtime.
const INTENSITY_COLORMAPS = ['gray', 'bone', 'hot', 'cool', 'viridis', 'plasma', 'inferno', 'jet'];

interface NeuroCadeViewerDebugState {
  activeViewMode: NeuroCadeViewMode;
  activeDragMode: ViewerDragMode;
  mountedPaneCount: number;
  activePaneCount: number;
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
  volumes,
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
    instancesRef,
    anyInstance,
    bumpInstancesVersion,
    scheduleInstanceRefresh,
    scheduleInstanceLayerRefresh,
    scheduleInstanceDraw,
  } = useViewerPaneInstances();
  const colormapsReportedRef = useRef(false);
  const onLocationChangeRef = useRef(onLocationChange);
  onLocationChangeRef.current = onLocationChange;

  const [loadingPanes, setLoadingPanes] = useState<Record<number, boolean>>({});
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

  const loading = Object.values(loadingPanes).some(Boolean);
  const selectedView = VIEW_MODES.find((mode) => mode.id === viewMode) ?? VIEW_MODES[VIEW_MODES.length - 1];
  const isGrid = viewMode === 'multi';
  const primarySliceType: ViewerSliceType = isGrid ? 0 : selectedView.sliceType;
  const activePaneSliceTypes = useMemo<ViewerSliceType[]>(
    () => isGrid ? PANE_SLICE_TYPES : [selectedView.sliceType],
    [isGrid, selectedView.sliceType],
  );
  const activePaneSet = useMemo(() => new Set(activePaneSliceTypes), [activePaneSliceTypes]);
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
    const nv = anyInstance();
    const nextReferenceVolumeId = nv ? getReferenceVolumeId(nv) : null;
    setReferenceVolumeId((current) => (current === nextReferenceVolumeId ? current : nextReferenceVolumeId));
  }, [anyInstance]);

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
    instancesRef,
    anyInstance,
    scheduleInstanceLayerRefresh,
  });

  const schedulePaneRenderAction = useCallback((sliceType: ViewerSliceType, nv: Niivue, action: PaneRenderAction) => {
    if (!action) return;
    if (action.kind === 'draw') scheduleInstanceDraw(sliceType, nv);
    if (action.kind === 'refresh') scheduleInstanceRefresh(sliceType, nv);
    if (action.kind === 'layer-refresh') scheduleInstanceLayerRefresh(sliceType, nv, action.loaded);
  }, [scheduleInstanceDraw, scheduleInstanceLayerRefresh, scheduleInstanceRefresh]);

  const applyImmediateVolumeUpdate = useCallback((id: string, updates: Partial<Volume>) => {
    const source = volumes.find((volume) => volume.id === id);
    if (!source) return;
    const next = { ...source, ...updates } as Volume;
    for (const [sliceType, nv] of instancesRef.current.entries()) {
      const action = applyLayerDisplay(nv, id, next, updates);
      schedulePaneRenderAction(sliceType, nv, action);
    }
  }, [instancesRef, schedulePaneRenderAction, volumes]);

  const handlePaneLoading = useCallback((sliceType: ViewerSliceType, isLoading: boolean) => {
    setLoadingPanes((prev) => (prev[sliceType] === isLoading ? prev : { ...prev, [sliceType]: isLoading }));
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
    for (const [sliceType, nv] of instancesRef.current.entries()) {
      const action = previewLayerOpacity(nv, id, next);
      schedulePaneRenderAction(sliceType, nv, action);
    }
  }, [instancesRef, schedulePaneRenderAction, volumes]);

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

  const layerShownIn3D = useCallback((volume: Volume): boolean => {
    return volume.renderIn3D ?? isSurfaceLayer(volume);
  }, []);

  const volumesFor3D = useMemo(() => volumes.filter(layerShownIn3D), [layerShownIn3D, volumes]);

  const volumesForPane = useCallback((sliceType: ViewerSliceType): Volume[] => {
    if (sliceType !== 4) return volumes;
    return volumesFor3D;
  }, [volumes, volumesFor3D]);

  const {
    drawingSession,
    canUndo,
    drawingMenuOpen,
    setDrawingMenuOpen,
    registerDrawingPane,
    updateDrawingOptions,
    beginBlankDrawing,
    beginDrawingFromSegmentation,
    handleDrawUndo,
    handleSaveDrawing,
    closeNativeDrawing,
  } = useNativeDrawingSession({
    canAddLayers,
    instancesRef,
    anyInstance,
    referenceVolumeId,
    onSaveDrawing,
  });

  // --- Pane registration + cross-instance drawing bridge --------------------
  const handlePaneReady = useCallback((nv: Niivue | null, sliceType: ViewerSliceType) => {
    if (nv) {
      instancesRef.current.set(sliceType, nv);
      if (sliceType <= 2) registerDrawingPane(nv);
    } else {
      instancesRef.current.delete(sliceType);
    }
    bumpInstancesVersion();
    requestAnimationFrame(syncReferenceVolumeId);
  }, [bumpInstancesVersion, instancesRef, registerDrawingPane, syncReferenceVolumeId]);

  useImperativeHandle(ref, () => ({
    getSnapshots: (): MriSnapshots | null => {
      const grab = (sliceType: ViewerSliceType): string | null => {
        return capturePaneSnapshot(instancesRef.current.get(sliceType));
      };
      const axial = grab(0);
      const coronal = grab(1);
      const sagittal = grab(2);
      const fallback = axial ?? coronal ?? sagittal;
      if (!fallback) return null;
      return { axial: axial ?? fallback, coronal: coronal ?? fallback, sagittal: sagittal ?? fallback };
    },
  }), [instancesRef]);

  useEffect(() => {
    window.__neurocadeViewerDebug = {
      getState: () => {
        const loadedIds = new Set<string>();
        const actualWindowings: Record<string, { calMin: number; calMax: number }> = {};
        for (const nv of instancesRef.current.values()) {
          const interop = asNiivueInterop(nv);
          for (const loaded of interop.volumes) {
            if (!loaded.id) continue;
            loadedIds.add(loaded.id);
            actualWindowings[loaded.id] = {
              calMin: loaded.cal_min ?? windowings[loaded.id]?.calMin ?? 0,
              calMax: loaded.cal_max ?? windowings[loaded.id]?.calMax ?? 0,
            };
          }
          for (const mesh of interop.meshes ?? []) {
            if (mesh.id) loadedIds.add(mesh.id);
          }
        }
        return {
          activeViewMode: viewMode,
          activeDragMode: dragMode,
          mountedPaneCount: instancesRef.current.size,
          activePaneCount: activePaneSliceTypes.length,
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
  }, [activePaneSliceTypes.length, dragMode, instancesRef, viewMode, volumes, windowings]);

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
    const instances = [...instancesRef.current.values()];
    if (instances.length === 0) return;
    clearManualWindowing();
    const nextWindowings: Record<string, WindowSetting> = {};
    for (const nv of instances) {
      const nvInterop = asNiivueInterop(nv) as NiivueViewerInterop;
      nvInterop.setScale?.(1);
      if (nvInterop.scene?.pan2Dxyzmm) {
        nvInterop.scene.pan2Dxyzmm[0] = 0;
        nvInterop.scene.pan2Dxyzmm[1] = 0;
        nvInterop.scene.pan2Dxyzmm[2] = 0;
        nvInterop.scene.pan2Dxyzmm[3] = 1;
      }
      nvInterop.setRenderAzimuthElevation?.(110, 10);
      if (nvInterop.scene?.clipPlaneDepthAziElevs) {
        const activeClipPlaneIndex = nvInterop.uiData?.activeClipPlaneIndex ?? 0;
        nvInterop.scene.clipPlaneDepthAziElevs[activeClipPlaneIndex] = [2, 0, 0];
        nvInterop.setClipPlane?.([2, 0, 0]);
      }
      for (const loaded of nvInterop.volumes) {
        const source = volumes.find((volume) => volume.id === loaded.id);
        if (!source || isSurfaceLayer(source) || source.type === 'segmentation') continue;
        const resetBounds = loaded as NiivueVolumeInterop & { robust_min?: number; robust_max?: number };
        const calMin = resetBounds.robust_min ?? loaded.global_min ?? loaded.cal_min ?? 0;
        const calMax = resetBounds.robust_max ?? loaded.global_max ?? loaded.cal_max ?? 1;
        loaded.cal_min = calMin;
        loaded.cal_max = calMax;
        if (loaded.id && !nextWindowings[loaded.id]) {
          nextWindowings[loaded.id] = {
            calMin,
            calMax,
            globalMin: loaded.global_min ?? calMin,
            globalMax: loaded.global_max ?? calMax,
          };
        }
      }
      nvInterop.drawScene?.();
    }
    for (const id of Object.keys(nextWindowings)) {
      onUpdateVolume(id, { brightness: 0, contrast: 1 });
    }
    if (Object.keys(nextWindowings).length > 0) {
      setWindowings((prev) => ({ ...prev, ...nextWindowings }));
    }
  }, [clearManualWindowing, instancesRef, onUpdateVolume, setWindowings, volumes]);

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
          canUndo={canUndo}
          drawingMenuOpen={drawingMenuOpen}
          layerShownIn3D={layerShownIn3D}
          onSetDrawingMenuOpen={setDrawingMenuOpen}
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
        <div className={`nc-viewer-grid ${isGrid ? 'is-grid' : 'is-single'}`}>
          {PANE_SLICE_TYPES.map((sliceType) => (
            <NiivuePane
              key={sliceType}
              sliceType={sliceType}
              volumes={volumesForPane(sliceType)}
              windowings={windowings}
              manualWindowingIds={manualWindowingRef}
              dragMode={dragMode}
              externalCoordinate={externalCoordinate}
              reportLocation={sliceType === primarySliceType}
              hidden={!activePaneSet.has(sliceType)}
              showOrientationLabels={showOrientationLabels}
              onReady={handlePaneReady}
              onLocationChange={handlePaneLocation}
              onIntensityWindowChange={syncIntensityWindow}
              onLoadingChange={handlePaneLoading}
              onError={setLoadError}
              onColormaps={handlePaneColormaps}
            />
          ))}
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
