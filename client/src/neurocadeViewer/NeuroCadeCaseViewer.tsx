import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Download, Eraser, Eye, EyeOff, Layers, Pencil, Plus, Undo2, X } from 'lucide-react';
import { Niivue } from '@niivue/niivue';

import { isSegmentationLayer, isSurfaceLayer, type LocationInfo, type MriSnapshots, type MriViewerRef, type LayerType, type SegmentationVolumeLayer, type SurfaceColorMode, type Volume } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import { resolveSurfaceLayerColorMode, SURFACE_COLOR_MODE_LABELS, surfaceColorModeAvailable } from '../utils/surfaceColors';
import {
  clampOpacity,
  effectiveLayerOpacity,
  layerDefaultOpacity,
  layerType,
  type NiivueViewerInterop,
} from './niivueLayers';
import { NiivuePane, type WindowSetting } from './NiivuePane';
import { ViewerHelpDialog } from './ViewerHelpDialog';
import { ViewerToolbar } from './ViewerToolbar';
import {
  DEFAULT_DRAWING_OPTIONS,
  drawingSourceFromSegmentation,
  filenameForSegmentationDrawing,
  inferSavedDrawingLut,
  loadDrawingSourceImage,
  makeDrawingFilename,
  popUndoBitmap,
  pushUndoBitmap,
  validateSameDrawingGrid,
  type DrawingLut,
  type DrawingOptions,
  type DrawingSession,
} from './nativeDrawing';
import { VIEW_MODES, type NeuroCadeViewMode, type ViewerDragMode, type ViewerSliceType } from './viewerControls';

interface SavedDrawingPayload {
  filename: string;
  data: Uint8Array;
  lut: DrawingLut;
  source?: DrawingSession['source'];
}

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

function titleCaseColormap(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function layerAccent(type: LayerType) {
  if (type === 'surface') return 'text-[var(--nc-warning)]';
  if (type === 'drawing') return 'text-[var(--nc-accent)]';
  if (type === 'segmentation') return 'text-[var(--nc-success)]';
  return 'text-[var(--nc-interactive)]';
}

function sectionTitle(type: LayerType) {
  if (type === 'surface') return 'Surfaces';
  if (type === 'drawing') return 'Drawing';
  if (type === 'segmentation') return 'Segmentations';
  return 'Intensity';
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
  const instancesRef = useRef<Map<ViewerSliceType, Niivue>>(new Map());
  const manualWindowingRef = useRef<Set<string>>(new Set());
  const colormapsReportedRef = useRef(false);
  const refreshFrameRefs = useRef<Map<ViewerSliceType, number>>(new Map());
  const onLocationChangeRef = useRef(onLocationChange);
  const drawingSessionRef = useRef<DrawingSession>({ ...DEFAULT_DRAWING_OPTIONS, active: false, dirty: false, error: null });
  const drawingBitmapRef = useRef<Uint8Array | null>(null);
  const drawingUndoStackRef = useRef<Uint8Array[]>([]);
  const drawingSyncingRef = useRef(false);
  onLocationChangeRef.current = onLocationChange;

  const [instancesVersion, setInstancesVersion] = useState(0);
  const [loadingPanes, setLoadingPanes] = useState<Record<number, boolean>>({});
  const [referenceVolumeId, setReferenceVolumeId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<NeuroCadeViewMode>('multi');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dragMode, setDragMode] = useState<ViewerDragMode>('contrast');
  const [helpOpen, setHelpOpen] = useState(false);
  const [currentLocation, setCurrentLocation] = useState<LocationInfo | null>(null);
  const [expandedLayerId, setExpandedLayerId] = useState<string | null>(null);
  const [draggingLayerId, setDraggingLayerId] = useState<string | null>(null);
  const [dragTarget, setDragTarget] = useState<{ id: string; position: 'before' | 'after' } | null>(null);
  const [windowings, setWindowings] = useState<Record<string, WindowSetting>>({});
  const [availableColormaps, setAvailableColormaps] = useState<string[]>([]);
  const [drawingSession, setDrawingSession] = useState<DrawingSession>(drawingSessionRef.current);
  // Only a boolean is exposed to the UI: undo *depth* changes on every stroke, but
  // the button only cares whether undo is available, so a boolean avoids a full
  // viewer re-render per stroke (React bails when the value is unchanged).
  const [canUndo, setCanUndo] = useState(false);
  const [drawingMenuOpen, setDrawingMenuOpen] = useState(false);
  drawingSessionRef.current = drawingSession;

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

  const anyInstance = useCallback((): Niivue | null => {
    return instancesRef.current.get(0) ?? [...instancesRef.current.values()][0] ?? null;
  }, []);

  const syncReferenceVolumeId = useCallback(() => {
    const nv = anyInstance();
    const referenceId = nv ? asNiivueInterop(nv).volumes[0]?.id ?? null : null;
    setReferenceVolumeId((current) => (current === referenceId ? current : referenceId));
  }, [anyInstance]);

  const scheduleInstanceRefresh = useCallback((sliceType: ViewerSliceType, nv: Niivue) => {
    if (refreshFrameRefs.current.has(sliceType)) return;
    const frame = window.requestAnimationFrame(() => {
      refreshFrameRefs.current.delete(sliceType);
      nv.updateGLVolume();
    });
    refreshFrameRefs.current.set(sliceType, frame);
  }, []);

  const applyImmediateVolumeUpdate = useCallback((id: string, updates: Partial<Volume>) => {
    const source = volumes.find((volume) => volume.id === id);
    if (!source) return;
    const next = { ...source, ...updates } as Volume;
    for (const [sliceType, nv] of instancesRef.current.entries()) {
      const interop = asNiivueInterop(nv);
      const loaded = interop.volumes.find((volume) => volume.id === id);
      if (loaded) {
        if (typeof updates.visible === 'boolean' || typeof updates.opacity === 'number') {
          loaded.opacity = effectiveLayerOpacity(next);
        }
      }
      const mesh = (interop.meshes ?? []).find((item) => item.id === id);
      if (mesh) {
        if (typeof updates.visible === 'boolean') mesh.visible = updates.visible;
        if (typeof updates.opacity === 'number' || typeof updates.visible === 'boolean') {
          mesh.opacity = effectiveLayerOpacity(next);
        }
      }
      if (loaded || mesh) scheduleInstanceRefresh(sliceType, nv);
    }
  }, [scheduleInstanceRefresh, volumes]);

  useEffect(() => {
    const instances = [...instancesRef.current.values()];
    if (instances.length < 2) return;
    for (const instance of instances) {
      const others = instances.filter((other) => other !== instance);
      (instance as unknown as { broadcastTo?: (others: Niivue[], opts?: object) => void })
        .broadcastTo?.(others, { '2d': true, '3d': true });
    }
  }, [instancesVersion]);

  const handlePaneLoading = useCallback((sliceType: ViewerSliceType, isLoading: boolean) => {
    setLoadingPanes((prev) => (prev[sliceType] === isLoading ? prev : { ...prev, [sliceType]: isLoading }));
    if (!isLoading) requestAnimationFrame(syncReferenceVolumeId);
  }, [syncReferenceVolumeId]);

  const handleUpdateVolume = useCallback((id: string, updates: Partial<Volume>) => {
    applyImmediateVolumeUpdate(id, updates);
    onUpdateVolume(id, updates);
    requestAnimationFrame(syncReferenceVolumeId);
  }, [applyImmediateVolumeUpdate, onUpdateVolume, syncReferenceVolumeId]);

  useEffect(() => () => {
    for (const frame of refreshFrameRefs.current.values()) {
      window.cancelAnimationFrame(frame);
    }
    refreshFrameRefs.current.clear();
  }, []);

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

  const drawingPanes = useCallback((): Niivue[] => (
    [...instancesRef.current.entries()]
      .filter(([sliceType]) => sliceType <= 2)
      .map(([, nv]) => nv)
  ), []);

  const setDrawingError = useCallback((message: string | null) => {
    const next = { ...drawingSessionRef.current, error: message };
    drawingSessionRef.current = next;
    setDrawingSession(next);
  }, []);

  // Match the draw overlay colours to the source label map while editing. Only
  // applied at session start / pane init (it calls updateGLVolume), never in the
  // per-stroke sync path.
  const applySessionDrawColormap = useCallback((nv: Niivue, session: DrawingSession) => {
    const colormap = session.source?.colormap;
    if (!colormap) return;
    try {
      asNiivueInterop(nv).setDrawColormap?.(colormap);
    } catch {
      // Unknown colormap name: fall back to Niivue's default draw palette.
    }
  }, []);

  const applyDrawingOptionsToInstance = useCallback((nv: Niivue, options: DrawingOptions) => {
    const interop = asNiivueInterop(nv);
    interop.setDrawOpacity?.(options.opacity);
    interop.drawFillOverwrites = options.penFill;
    interop.opts.clickToSegment = options.mode === 'wand';
    interop.opts.clickToSegmentIs2D = options.magicWand2dOnly;
    interop.opts.clickToSegmentAutoIntensity = true;
    interop.opts.clickToSegmentMaxDistanceMM = options.magicWandMaxDistanceMM;
    interop.opts.clickToSegmentPercent = options.magicWandThresholdPercent;
    const isDrawingEnabled = options.mode !== 'none';
    const penValue = options.mode === 'wand'
      ? options.penValue
      : options.erase
        ? 0
        : options.penValue;
    interop.setPenValue?.(penValue, options.mode === 'pen' && options.penFill);
    interop.setDrawingEnabled?.(isDrawingEnabled);
  }, []);

  const applyBitmapToPane = useCallback((nv: Niivue, bitmap: Uint8Array, options: DrawingOptions) => {
    const interop = asNiivueInterop(nv);
    interop.setDrawingEnabled?.(true);
    interop.drawBitmap = new Uint8Array(bitmap);
    interop.drawClearAllUndoBitmaps?.();
    interop.refreshDrawing?.(true, false);
    applyDrawingOptionsToInstance(nv, options);
  }, [applyDrawingOptionsToInstance]);

  // Tear down native Niivue drawing in every 2D pane. No React state is touched
  // here so it is safe to call from unmount cleanup.
  const closeDrawingPanes = useCallback(() => {
    drawingSyncingRef.current = true;
    try {
      for (const nv of drawingPanes()) {
        const interop = asNiivueInterop(nv);
        interop.opts.clickToSegment = false;
        interop.setDrawingEnabled?.(false);
        interop.setPenValue?.(0, false);
        interop.closeDrawing?.();
      }
    } finally {
      drawingSyncingRef.current = false;
    }
    drawingBitmapRef.current = null;
    drawingUndoStackRef.current = [];
  }, [drawingPanes]);

  const closeNativeDrawing = useCallback((resetSession: boolean) => {
    closeDrawingPanes();
    setCanUndo(false);
    if (resetSession) {
      const next = { ...DEFAULT_DRAWING_OPTIONS, active: false, dirty: false, error: null };
      drawingSessionRef.current = next;
      setDrawingSession(next);
    }
  }, [closeDrawingPanes]);

  const syncDrawingBitmapFromPane = useCallback((sourceNv: Niivue, action: string) => {
    if (drawingSyncingRef.current || action !== 'draw') return;
    const source = asNiivueInterop(sourceNv);
    const session = drawingSessionRef.current;
    if (!source.drawBitmap || !session.active) return;
    // One canonical clone of the freshly drawn bitmap; it is shared (read-only)
    // by both the latest-bitmap ref and the undo history. Panes get their own
    // copies inside applyBitmapToPane since Niivue mutates each pane's buffer.
    const bitmap = new Uint8Array(source.drawBitmap);
    drawingBitmapRef.current = bitmap;
    drawingUndoStackRef.current = pushUndoBitmap(drawingUndoStackRef.current, bitmap);
    setCanUndo(drawingUndoStackRef.current.length > 1);
    // Track dirty in the ref only — it drives no UI, so avoid a per-stroke
    // re-render of the whole viewer.
    session.dirty = true;
    session.error = null;
    drawingSyncingRef.current = true;
    try {
      for (const nv of drawingPanes()) {
        if (nv === sourceNv) continue;
        applyBitmapToPane(nv, bitmap, session);
      }
    } finally {
      drawingSyncingRef.current = false;
    }
  }, [applyBitmapToPane, drawingPanes]);

  const initializeDrawingPane = useCallback((nv: Niivue) => {
    const session = drawingSessionRef.current;
    const bitmap = drawingBitmapRef.current;
    if (!session.active || !bitmap) return;
    drawingSyncingRef.current = true;
    try {
      applyBitmapToPane(nv, bitmap, session);
      applySessionDrawColormap(nv, session);
    } finally {
      drawingSyncingRef.current = false;
    }
  }, [applyBitmapToPane, applySessionDrawColormap]);

  // --- Pane registration + cross-instance drawing bridge --------------------
  const handlePaneReady = useCallback((nv: Niivue | null, sliceType: ViewerSliceType) => {
    if (nv) {
      instancesRef.current.set(sliceType, nv);
      asNiivueInterop(nv).onDrawingChanged = (action: string) => syncDrawingBitmapFromPane(nv, action);
      if (sliceType <= 2) initializeDrawingPane(nv);
    } else {
      instancesRef.current.delete(sliceType);
    }
    setInstancesVersion((version) => version + 1);
    requestAnimationFrame(syncReferenceVolumeId);
  }, [initializeDrawingPane, syncDrawingBitmapFromPane, syncReferenceVolumeId]);

  const updateDrawingOptions = useCallback((updates: Partial<DrawingOptions>) => {
    const next = { ...drawingSessionRef.current, ...updates, error: null };
    drawingSessionRef.current = next;
    for (const nv of drawingPanes()) {
      applyDrawingOptionsToInstance(nv, next);
    }
    setDrawingSession(next);
  }, [applyDrawingOptionsToInstance, drawingPanes]);

  const referenceVolumeForDrawing = useCallback((): NiivueVolumeInterop | null => {
    const nv = drawingPanes()[0] ?? anyInstance();
    return nv ? asNiivueInterop(nv).volumes[0] ?? null : null;
  }, [anyInstance, drawingPanes]);

  const beginBlankDrawing = useCallback(() => {
    const panes = drawingPanes();
    const reference = referenceVolumeForDrawing();
    if (!canAddLayers) {
      setDrawingError('Load or create a case before starting a drawing.');
      return;
    }
    if (!reference || panes.length === 0) {
      setDrawingError('Load an intensity volume before starting a drawing.');
      return;
    }

    closeNativeDrawing(false);
    const session: DrawingSession = {
      ...DEFAULT_DRAWING_OPTIONS,
      active: true,
      dirty: false,
      error: null,
      filename: `drawing-${Date.now()}.nii`,
    };
    drawingSessionRef.current = session;
    drawingSyncingRef.current = true;
    try {
      for (const nv of panes) {
        const interop = asNiivueInterop(nv);
        interop.setDrawingEnabled?.(true);
        interop.drawClearAllUndoBitmaps?.();
        applyDrawingOptionsToInstance(nv, session);
      }
      const bitmap = asNiivueInterop(panes[0]).drawBitmap;
      drawingBitmapRef.current = bitmap ? new Uint8Array(bitmap) : null;
      drawingUndoStackRef.current = drawingBitmapRef.current ? [drawingBitmapRef.current] : [];
      setCanUndo(false);
    } finally {
      drawingSyncingRef.current = false;
    }
    setDrawingSession(session);
  }, [applyDrawingOptionsToInstance, canAddLayers, closeNativeDrawing, drawingPanes, referenceVolumeForDrawing, setDrawingError]);

  const beginDrawingFromSegmentation = useCallback(async (source: SegmentationVolumeLayer) => {
    const panes = drawingPanes();
    const reference = referenceVolumeForDrawing();
    if (!canAddLayers) {
      setDrawingError('Load or create a case before editing a label map.');
      return;
    }
    if (!reference || panes.length === 0) {
      setDrawingError('Load an intensity volume before editing a label map.');
      return;
    }

    setDrawingError(null);
    const controller = new AbortController();
    try {
      const sourceImage = await loadDrawingSourceImage(source, controller.signal);
      const gridError = validateSameDrawingGrid(reference, sourceImage);
      if (gridError) {
        setDrawingError(gridError);
        return;
      }

      closeNativeDrawing(false);
      const session: DrawingSession = {
        ...DEFAULT_DRAWING_OPTIONS,
        active: true,
        dirty: false,
        error: null,
        filename: filenameForSegmentationDrawing(source),
        source: drawingSourceFromSegmentation(source),
      };
      drawingSessionRef.current = session;
      drawingSyncingRef.current = true;
      try {
        const primary = panes[0];
        const loaded = asNiivueInterop(primary).loadDrawing?.(sourceImage);
        if (loaded === false || !asNiivueInterop(primary).drawBitmap) {
          closeNativeDrawing(true);
          setDrawingError('Could not initialize drawing from the selected label map.');
          return;
        }
        const bitmap = new Uint8Array(asNiivueInterop(primary).drawBitmap ?? []);
        drawingBitmapRef.current = bitmap;
        drawingUndoStackRef.current = [bitmap];
        setCanUndo(false);
        applyDrawingOptionsToInstance(primary, session);
        applySessionDrawColormap(primary, session);
        for (const nv of panes.slice(1)) {
          applyBitmapToPane(nv, bitmap, session);
          applySessionDrawColormap(nv, session);
        }
      } finally {
        drawingSyncingRef.current = false;
      }
      setDrawingSession(session);
    } catch (error) {
      setDrawingError(error instanceof Error ? error.message : String(error));
    }
  }, [applyBitmapToPane, applyDrawingOptionsToInstance, applySessionDrawColormap, canAddLayers, closeNativeDrawing, drawingPanes, referenceVolumeForDrawing, setDrawingError]);

  const handleDrawUndo = useCallback(() => {
    const { stack, current } = popUndoBitmap(drawingUndoStackRef.current);
    if (stack === drawingUndoStackRef.current || !current) return;
    drawingUndoStackRef.current = stack;
    drawingBitmapRef.current = current;
    setCanUndo(stack.length > 1);
    drawingSessionRef.current.dirty = stack.length > 1;
    drawingSyncingRef.current = true;
    try {
      for (const nv of drawingPanes()) {
        applyBitmapToPane(nv, current, drawingSessionRef.current);
      }
    } finally {
      drawingSyncingRef.current = false;
    }
  }, [applyBitmapToPane, drawingPanes]);

  const handleSaveDrawing = useCallback(async () => {
    if (!drawingSession.active) return;
    const nv = drawingPanes().find((candidate) => asNiivueInterop(candidate).drawBitmap);
    if (!nv) {
      setDrawingError('No drawing is available to save.');
      return;
    }
    try {
      const filename = makeDrawingFilename(drawingSession.filename);
      const saved = await asNiivueInterop(nv).saveImage?.({
        filename: '',
        isSaveDrawing: true,
        volumeByIndex: 0,
      });
      if (!(saved instanceof Uint8Array)) {
        setDrawingError('Could not export the current drawing.');
        return;
      }
      await onSaveDrawing?.({
        filename,
        data: saved,
        lut: inferSavedDrawingLut(asNiivueInterop(nv).drawBitmap, drawingSession.source),
        source: drawingSession.source,
      });
      closeNativeDrawing(true);
    } catch (error) {
      setDrawingError(error instanceof Error ? error.message : String(error));
    }
  }, [closeNativeDrawing, drawingPanes, drawingSession, onSaveDrawing, setDrawingError]);

  // The active drawing bitmap is bound to one voxel grid. If the reference
  // intensity volume changes (case switch, intensity layer swap), discard the
  // session rather than letting a mismatched bitmap paint onto a new grid.
  useEffect(() => {
    if (drawingSessionRef.current.active) {
      closeNativeDrawing(true);
    }
  }, [referenceVolumeId, closeNativeDrawing]);

  // Free native drawing GL state and undo bitmaps on unmount. Done without React
  // state updates since the component is going away.
  useEffect(() => () => {
    closeDrawingPanes();
  }, [closeDrawingPanes]);

  useImperativeHandle(ref, () => ({
    getSnapshots: (): MriSnapshots | null => {
      const grab = (sliceType: ViewerSliceType): string | null => {
        const nv = instancesRef.current.get(sliceType);
        const canvas = nv ? (nv as unknown as { canvas?: HTMLCanvasElement }).canvas : undefined;
        return canvas ? canvas.toDataURL('image/jpeg', 0.8) : null;
      };
      const axial = grab(0);
      const coronal = grab(1);
      const sagittal = grab(2);
      const fallback = axial ?? coronal ?? sagittal;
      if (!fallback) return null;
      return { axial: axial ?? fallback, coronal: coronal ?? fallback, sagittal: sagittal ?? fallback };
    },
  }), []);

  useEffect(() => {
    window.__neurocadeViewerDebug = {
      getState: () => {
        const loadedIds = new Set<string>();
        const actualWindowings: Record<string, { calMin: number; calMax: number }> = {};
        for (const nv of instancesRef.current.values()) {
          for (const loaded of asNiivueInterop(nv).volumes) {
            if (!loaded.id) continue;
            loadedIds.add(loaded.id);
            actualWindowings[loaded.id] = {
              calMin: loaded.cal_min ?? windowings[loaded.id]?.calMin ?? 0,
              calMax: loaded.cal_max ?? windowings[loaded.id]?.calMax ?? 0,
            };
          }
          for (const mesh of asNiivueInterop(nv).meshes ?? []) {
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
  }, [activePaneSliceTypes.length, dragMode, viewMode, volumes, windowings]);

  const toggleExpandLayer = useCallback((id: string, type: LayerType) => {
    setExpandedLayerId((prev) => prev === id ? null : id);
    if (type === 'intensity') {
      setWindowings((prev) => {
        if (prev[id]) return prev;
        const nv = anyInstance();
        if (!nv) return prev;
        const loaded = asNiivueInterop(nv).volumes.find((v) => v.id === id);
        if (!loaded) return prev;
        return {
          ...prev,
          [id]: {
            calMin: loaded.cal_min ?? loaded.global_min ?? 0,
            calMax: loaded.cal_max ?? loaded.global_max ?? 1,
            globalMin: loaded.global_min ?? 0,
            globalMax: loaded.global_max ?? 1,
          },
        };
      });
    }
  }, [anyInstance]);

  const updateWindowing = useCallback((id: string, field: 'calMin' | 'calMax', value: number) => {
    manualWindowingRef.current.add(id);
    for (const [sliceType, nv] of instancesRef.current.entries()) {
      const loaded = asNiivueInterop(nv).volumes.find((volume) => volume.id === id);
      if (!loaded) continue;
      if (field === 'calMin') loaded.cal_min = value;
      if (field === 'calMax') loaded.cal_max = value;
      scheduleInstanceRefresh(sliceType, nv);
    }
    setWindowings((prev) => {
      const current = prev[id] ?? { calMin: 0, calMax: 1, globalMin: 0, globalMax: 1 };
      return { ...prev, [id]: { ...current, [field]: value } };
    });
  }, [scheduleInstanceRefresh]);

  const handleDragModeChange = useCallback((mode: ViewerDragMode) => {
    markViewerMeasure(`tool:${mode}`, () => setDragMode(mode));
  }, []);

  const handleViewModeChange = useCallback((mode: NeuroCadeViewMode) => {
    markViewerMeasure(`view:${mode}`, () => setViewMode(mode));
  }, []);

  const resetView = useCallback(() => {
    const instances = [...instancesRef.current.values()];
    if (instances.length === 0) return;
    manualWindowingRef.current.clear();
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
  }, [onUpdateVolume, volumes]);

  // Drag-and-drop layer reordering (matches the legacy NeuroCade layer panel).
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

  const renderLayerSection = (type: LayerType, items: Volume[]) => (
    <section key={type} className="nc-viewer-layer-section">
      <div className="nc-viewer-layer-section-header">
        <span>{sectionTitle(type)}</span>
        {canAddLayers && onOpenLayerPicker && (
          <button type="button" className="nc-btn nc-icon-btn !border-0" onClick={() => onOpenLayerPicker(type)} title={`Load ${sectionTitle(type)}`} aria-label={`Load ${sectionTitle(type)}`}>
            <Plus size={13} />
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <div className="nc-viewer-empty-layer">No {sectionTitle(type).toLowerCase()} loaded</div>
      ) : (
        <div className="nc-viewer-layer-list">
          {items.map((volume) => {
            const typeName = layerType(volume);
            const isExpanded = expandedLayerId === volume.id;
            const isReferenceVolume = !isSurfaceLayer(volume) && volume.id === referenceVolumeId;
            const showWindowing = typeName === 'intensity';
            const showSurfaceDisplay = isSurfaceLayer(volume);
            const surfaceColorMode = showSurfaceDisplay ? resolveSurfaceLayerColorMode(volume) : 'solid';
            const win = windowings[volume.id];
            const defaultOpacity = layerDefaultOpacity(volume);
            const dropClass = dragTarget?.id === volume.id ? `nc-layer-drop-${dragTarget.position}` : '';
            const currentColormap = (volume.colormap || 'gray').toLowerCase();
            const colormapOptions = intensityColormaps.includes(currentColormap)
              ? intensityColormaps
              : [currentColormap, ...intensityColormaps];
            const windowStep = win ? ((win.globalMax - win.globalMin) / 200 || 0.01) : 0.01;
            return (
              <div
                key={volume.id}
                className={`nc-layer-item ${draggingLayerId === volume.id ? 'opacity-[0.55]' : ''} ${dropClass}`}
                data-testid="viewer-layer-item"
                data-layer-id={volume.id}
                data-layer-type={typeName}
                onDragOver={(event) => handleLayerDragOver(event, volume)}
                onDragLeave={() => { if (dragTarget?.id === volume.id) setDragTarget(null); }}
                onDrop={(event) => handleLayerDrop(event, volume)}
              >
                <div className="nc-viewer-layer-row">
                  <button
                    type="button"
                    className={`nc-viewer-layer-visibility ${volume.visible ? layerAccent(typeName) : 'text-[var(--nc-tx-faint)]'}`}
                    onClick={() => handleUpdateVolume(volume.id, { visible: !volume.visible })}
                    aria-label={`${volume.visible ? 'Hide' : 'Show'} ${volume.name}`}
                    title={volume.visible ? 'Hide layer' : 'Show layer'}
                    data-testid="viewer-layer-visibility"
                  >
                    {volume.visible ? <Eye size={14} /> : <EyeOff size={14} />}
                  </button>
                  <div className="min-w-0 flex-1">
                    <button
                      type="button"
                      draggable
                      className="nc-layer-drag-handle flex w-full items-center gap-1 text-left"
                      onClick={() => toggleExpandLayer(volume.id, typeName)}
                      onKeyDown={(event) => handleLayerReorderKeyDown(event, volume, items)}
                      onDragStart={(event) => {
                        setDraggingLayerId(volume.id);
                        event.dataTransfer.effectAllowed = 'move';
                        event.dataTransfer.setData('text/plain', volume.id);
                      }}
                      onDragEnd={() => { setDraggingLayerId(null); setDragTarget(null); }}
                      aria-expanded={isExpanded}
                      title="Click to toggle settings · drag to reorder · ↑/↓ to move"
                    >
                      <span className="truncate text-[var(--nc-tx)]">{volume.name}</span>
                      {isExpanded ? <ChevronDown size={11} className="shrink-0 text-[var(--nc-tx-dim)]" /> : <ChevronRight size={11} className="shrink-0 text-[var(--nc-tx-dim)]" />}
                    </button>
                    <div className="nc-mono truncate text-[11px] text-[var(--nc-tx-dim)]">{volume.filename}</div>
                  </div>
                </div>
                {isExpanded && (
                  <div className="border-b border-[var(--nc-border)] px-2 pb-2 pt-1 space-y-1.5">
                    {isReferenceVolume && (
                      <div className="nc-mono rounded border border-[var(--nc-interactive-border)] bg-[var(--nc-interactive-subtle)] px-2 py-1 text-[11px] text-[var(--nc-interactive)]">
                        Reference volume
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Opacity</span>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={clampOpacity(volume.opacity, defaultOpacity)}
                        onChange={(event) => handleUpdateVolume(volume.id, { opacity: Number(event.currentTarget.value), visible: Number(event.currentTarget.value) > 0 })}
                        className="nc-viewer-layer-slider"
                        aria-label={`${volume.name} opacity`}
                      />
                      <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(clampOpacity(volume.opacity, defaultOpacity) * 100)}</span>
                    </div>
                    <label className="flex cursor-pointer items-center gap-2 text-[11px] text-[var(--nc-tx-dim)]">
                      <input
                        type="checkbox"
                        checked={layerShownIn3D(volume)}
                        onChange={(event) => handleUpdateVolume(volume.id, { renderIn3D: event.currentTarget.checked })}
                      />
                      <span>Show in 3D</span>
                    </label>
                    {showSurfaceDisplay && (
                      <label className="flex cursor-pointer items-center gap-2 text-[11px] text-[var(--nc-tx-dim)]">
                        <input
                          type="checkbox"
                          checked={volume.renderInSlices ?? true}
                          onChange={(event) => handleUpdateVolume(volume.id, { renderInSlices: event.currentTarget.checked })}
                        />
                        <span>Show in slices</span>
                      </label>
                    )}
                    {showWindowing && (
                      <>
                        {win ? (
                          <>
                            <div className="flex items-center gap-2">
                              <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Min</span>
                              <input
                                type="range"
                                min={win.globalMin}
                                max={win.globalMax}
                                step={windowStep}
                                value={win.calMin}
                                onInput={(e) => updateWindowing(volume.id, 'calMin', Number(e.currentTarget.value))}
                                onChange={(e) => updateWindowing(volume.id, 'calMin', Number(e.currentTarget.value))}
                                onKeyDown={(event) => {
                                  const direction = event.key === 'ArrowRight' || event.key === 'ArrowUp' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -1 : 0;
                                  if (direction === 0) return;
                                  event.preventDefault();
                                  updateWindowing(volume.id, 'calMin', Math.max(win.globalMin, Math.min(win.calMax - windowStep, win.calMin + direction * windowStep)));
                                }}
                                className="nc-viewer-layer-slider"
                                aria-label={`${volume.name} window minimum`}
                                data-testid="viewer-window-min"
                              />
                              <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(win.calMin)}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Max</span>
                              <input
                                type="range"
                                min={win.globalMin}
                                max={win.globalMax}
                                step={windowStep}
                                value={win.calMax}
                                onInput={(e) => updateWindowing(volume.id, 'calMax', Number(e.currentTarget.value))}
                                onChange={(e) => updateWindowing(volume.id, 'calMax', Number(e.currentTarget.value))}
                                onKeyDown={(event) => {
                                  const direction = event.key === 'ArrowRight' || event.key === 'ArrowUp' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -1 : 0;
                                  if (direction === 0) return;
                                  event.preventDefault();
                                  updateWindowing(volume.id, 'calMax', Math.min(win.globalMax, Math.max(win.calMin + windowStep, win.calMax + direction * windowStep)));
                                }}
                                className="nc-viewer-layer-slider"
                                aria-label={`${volume.name} window maximum`}
                                data-testid="viewer-window-max"
                              />
                              <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(win.calMax)}</span>
                            </div>
                          </>
                        ) : (
                          <div className="nc-mono text-[11px] italic text-[var(--nc-tx-dim)]">Loading volume bounds…</div>
                        )}
                        {colormapOptions.length > 0 && (
                          <div className="flex items-center gap-2">
                            <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Colormap</span>
                            <select
                              className="nc-mono nc-viewer-layer-select"
                              value={currentColormap}
                              onChange={(e) => handleUpdateVolume(volume.id, { colormap: e.target.value })}
                              aria-label={`${volume.name} colormap`}
                            >
                              {colormapOptions.map((cm) => (
                                <option key={cm} value={cm}>{titleCaseColormap(cm)}</option>
                              ))}
                            </select>
                          </div>
                        )}
                      </>
                    )}
                    {showSurfaceDisplay && (
                      <div className="flex items-center gap-2">
                        <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Display</span>
                        <select
                          className="nc-mono nc-viewer-layer-select"
                          value={surfaceColorMode}
                          disabled={!volume.visible}
                          onChange={(event) => handleUpdateVolume(volume.id, { surfaceColorMode: event.target.value as SurfaceColorMode })}
                          aria-label={`${volume.name} surface display`}
                        >
                          {(Object.keys(SURFACE_COLOR_MODE_LABELS) as SurfaceColorMode[])
                            .filter((mode) => surfaceColorModeAvailable(volume, mode))
                            .map((mode) => (
                              <option key={mode} value={mode}>{SURFACE_COLOR_MODE_LABELS[mode]}</option>
                            ))}
                        </select>
                      </div>
                    )}
                    {onRemoveVolume && (
                      <button
                        type="button"
                        className="nc-viewer-layer-close"
                        onClick={() => onRemoveVolume(volume.id)}
                        title={`Close ${volume.name}`}
                        aria-label={`Close ${volume.name}`}
                      >
                        <X size={12} className="shrink-0" />
                        <span>Close layer</span>
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );

  const segmentationSources = groupedLayers.segmentation.filter(isSegmentationLayer);
  const renderDrawingTools = () => (
    <section className="nc-viewer-layer-section">
      <div className="nc-viewer-layer-section-header">
        <span>Drawing</span>
        <div className="relative">
          <button
            type="button"
            className="nc-btn nc-icon-btn !border-0"
            onClick={() => setDrawingMenuOpen((open) => !open)}
            title="New drawing"
            aria-label="New drawing"
            aria-expanded={drawingMenuOpen}
            disabled={!canAddLayers}
          >
            <Plus size={13} />
          </button>
          {drawingMenuOpen && (
            <button
              type="button"
              aria-hidden="true"
              tabIndex={-1}
              className="fixed inset-0 z-10 cursor-default"
              onClick={() => setDrawingMenuOpen(false)}
            />
          )}
          {drawingMenuOpen && (
            <div className="nc-viewer-drawing-menu" role="menu">
              <button
                type="button"
                role="menuitem"
                className="nc-viewer-drawing-menu-item"
                onClick={() => { setDrawingMenuOpen(false); beginBlankDrawing(); }}
                disabled={groupedLayers.intensity.length === 0}
                title={groupedLayers.intensity.length === 0 ? 'Load an intensity volume first' : 'Empty drawing matching the active volume'}
              >
                Blank drawing
              </button>
              <div className="nc-viewer-drawing-menu-label">Start from label map</div>
              {segmentationSources.length === 0 ? (
                <div className="nc-viewer-drawing-menu-empty">No segmentations loaded</div>
              ) : (
                segmentationSources.map((segmentation) => (
                  <button
                    key={segmentation.id}
                    type="button"
                    role="menuitem"
                    className="nc-viewer-drawing-menu-item truncate"
                    onClick={() => { setDrawingMenuOpen(false); void beginDrawingFromSegmentation(segmentation); }}
                    title={`Edit a copy of ${segmentation.name}`}
                  >
                    {segmentation.name}
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      <div className="nc-viewer-drawing-tools p-2 space-y-1.5">
        <div className="flex items-center gap-2">
          <Pencil size={12} className="text-[var(--nc-accent)]" />
          <span className="nc-mono min-w-0 flex-1 truncate text-[11px] text-[var(--nc-tx-dim)]" title={drawingSession.source?.name ?? drawingSession.filename}>
            {drawingSession.active ? `Editing: ${drawingSession.source?.name ?? drawingSession.filename}` : 'No active drawing'}
          </span>
        </div>

        {drawingSession.error && (
          <div className="rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] px-2 py-1 text-[11px] text-[var(--nc-danger)]">
            {drawingSession.error}
          </div>
        )}

        <div className="flex items-center gap-1">
          {(['none', 'pen', 'wand'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              className={`nc-btn flex-1 text-center text-[11px] ${drawingSession.mode === mode ? 'nc-btn-active' : ''}`}
              onClick={() => updateDrawingOptions({ mode })}
              disabled={!drawingSession.active}
            >
              {mode === 'none' ? 'None' : mode === 'pen' ? 'Pen' : 'Magic Wand'}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2">
          <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">File</span>
          <input
            type="text"
            value={drawingSession.filename}
            disabled={!drawingSession.active}
            onChange={(event) => updateDrawingOptions({ filename: event.currentTarget.value })}
            className="nc-viewer-layer-select nc-mono min-w-0 flex-1"
            aria-label="Drawing filename"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Opacity</span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={drawingSession.opacity}
            disabled={!drawingSession.active}
            onChange={(event) => updateDrawingOptions({ opacity: Number(event.currentTarget.value) })}
            className="nc-viewer-layer-slider"
            aria-label="Drawing opacity"
          />
          <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(drawingSession.opacity * 100)}</span>
        </div>

        {(drawingSession.mode === 'pen' || drawingSession.mode === 'wand') && (
          <div className="flex items-center gap-2">
            <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Pen</span>
            <input
              type="number"
              min={1}
              step={1}
              value={drawingSession.penValue}
              disabled={!drawingSession.active || drawingSession.erase}
              onChange={(event) => updateDrawingOptions({ penValue: Math.max(1, Math.round(Number(event.currentTarget.value) || 1)) })}
              className="nc-viewer-layer-select nc-mono min-w-0 flex-1"
              aria-label="Pen label value"
            />
            {drawingSession.mode === 'pen' && (
              <button
                type="button"
                className={`nc-btn flex items-center justify-center gap-1 text-[11px] ${drawingSession.erase ? 'nc-btn-active' : ''}`}
                onClick={() => updateDrawingOptions({ erase: !drawingSession.erase })}
                disabled={!drawingSession.active}
                title="Paint background (0) to erase"
              >
                <Eraser size={11} /> Erase
              </button>
            )}
          </div>
        )}

        {drawingSession.mode === 'pen' && (
          <label className="flex cursor-pointer items-center gap-2 text-[11px] text-[var(--nc-tx-dim)]">
            <input
              type="checkbox"
              checked={drawingSession.penFill}
              disabled={!drawingSession.active}
              onChange={(event) => updateDrawingOptions({ penFill: event.currentTarget.checked })}
            />
            <span>Pen fill</span>
          </label>
        )}

        {drawingSession.mode === 'wand' && (
          <>
            <label className="flex cursor-pointer items-center gap-2 text-[11px] text-[var(--nc-tx-dim)]">
              <input
                type="checkbox"
                checked={drawingSession.magicWand2dOnly}
                disabled={!drawingSession.active}
                onChange={(event) => updateDrawingOptions({ magicWand2dOnly: event.currentTarget.checked })}
              />
              <span>2D only</span>
            </label>
            <div className="flex items-center gap-2">
              <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Dist</span>
              <input
                type="range"
                min="2"
                max="500"
                step="1"
                value={drawingSession.magicWandMaxDistanceMM}
                disabled={!drawingSession.active}
                onChange={(event) => updateDrawingOptions({ magicWandMaxDistanceMM: Number(event.currentTarget.value) })}
                className="nc-viewer-layer-slider"
                aria-label="Magic wand maximum distance"
              />
              <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(drawingSession.magicWandMaxDistanceMM)}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Thresh</span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={drawingSession.magicWandThresholdPercent}
                disabled={!drawingSession.active}
                onChange={(event) => updateDrawingOptions({ magicWandThresholdPercent: Number(event.currentTarget.value) })}
                className="nc-viewer-layer-slider"
                aria-label="Magic wand threshold"
              />
              <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{drawingSession.magicWandThresholdPercent.toFixed(2)}</span>
            </div>
          </>
        )}

        <div className="flex gap-1 pt-0.5">
          <button type="button" className="nc-btn flex flex-1 items-center justify-center gap-1 text-[11px]" onClick={handleDrawUndo} disabled={!drawingSession.active || !canUndo} title="Undo last drawing change">
            <Undo2 size={11} /> Undo
          </button>
          <button type="button" className="nc-btn flex flex-1 items-center justify-center gap-1 text-[11px]" onClick={() => void handleSaveDrawing()} disabled={!drawingSession.active} title="Save as segmentation artifact">
            <Download size={11} /> Save
          </button>
          <button type="button" className="nc-btn flex flex-1 items-center justify-center gap-1 text-[11px]" onClick={() => closeNativeDrawing(true)} disabled={!drawingSession.active} title="Close drawing without saving">
            <X size={11} /> Close
          </button>
        </div>
      </div>
    </section>
  );

  return (
    <>
      {layerPanelOpen && (
        <aside className="nc-panel relative flex shrink-0 flex-col overflow-hidden border-r" style={{ width: layerPanelWidth }}>
          <div className="nc-pane-header">
            <Layers size={12} />
            <span>Layers</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {renderLayerSection('intensity', groupedLayers.intensity)}
            {renderLayerSection('segmentation', groupedLayers.segmentation)}
            {renderDrawingTools()}
            {renderLayerSection('surface', groupedLayers.surface)}
          </div>
          <div
            role="separator"
            aria-orientation="vertical"
            className="nc-resize-handle nc-resize-handle-right"
            onMouseDown={onStartLayerPanelResize}
          />
        </aside>
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
              onReady={handlePaneReady}
              onLocationChange={handlePaneLocation}
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
          onDragModeChange={handleDragModeChange}
          onViewModeChange={handleViewModeChange}
          onOpenHelp={() => setHelpOpen(true)}
          onResetView={resetView}
        />
      </main>

      {helpOpen && <ViewerHelpDialog onClose={() => setHelpOpen(false)} />}
    </>
  );
});

NeuroCadeCaseViewer.displayName = 'NeuroCadeCaseViewer';
