import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { Niivue } from '@niivue/niivue';

import type { SegmentationVolumeLayer } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
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
import type { ViewerSliceType } from './viewerControls';
import { drawingPanesFromInstances } from './viewerPaneAdapter';

export interface SavedDrawingPayload {
  filename: string;
  data: Uint8Array;
  lut: DrawingLut;
  source?: DrawingSession['source'];
}

interface UseNativeDrawingSessionArgs {
  canAddLayers: boolean;
  instancesRef: MutableRefObject<Map<ViewerSliceType, Niivue>>;
  anyInstance: () => Niivue | null;
  referenceVolumeId: string | null;
  onSaveDrawing?: (drawing: SavedDrawingPayload) => Promise<void>;
}

export function useNativeDrawingSession({
  canAddLayers,
  instancesRef,
  anyInstance,
  referenceVolumeId,
  onSaveDrawing,
}: UseNativeDrawingSessionArgs) {
  const drawingSessionRef = useRef<DrawingSession>({ ...DEFAULT_DRAWING_OPTIONS, active: false, dirty: false, error: null });
  const drawingBitmapRef = useRef<Uint8Array | null>(null);
  const drawingUndoStackRef = useRef<Uint8Array[]>([]);
  const drawingSyncingRef = useRef(false);
  const [drawingSession, setDrawingSession] = useState<DrawingSession>(drawingSessionRef.current);
  const [canUndo, setCanUndo] = useState(false);
  const [drawingMenuOpen, setDrawingMenuOpen] = useState(false);
  drawingSessionRef.current = drawingSession;

  const drawingPanes = useCallback((): Niivue[] => (
    drawingPanesFromInstances(instancesRef.current)
  ), [instancesRef]);

  const setDrawingError = useCallback((message: string | null) => {
    const next = { ...drawingSessionRef.current, error: message };
    drawingSessionRef.current = next;
    setDrawingSession(next);
  }, []);

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
    const bitmap = new Uint8Array(source.drawBitmap);
    drawingBitmapRef.current = bitmap;
    drawingUndoStackRef.current = pushUndoBitmap(drawingUndoStackRef.current, bitmap);
    setCanUndo(drawingUndoStackRef.current.length > 1);
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

  const registerDrawingPane = useCallback((nv: Niivue) => {
    asNiivueInterop(nv).onDrawingChanged = (action: string) => syncDrawingBitmapFromPane(nv, action);
    initializeDrawingPane(nv);
  }, [initializeDrawingPane, syncDrawingBitmapFromPane]);

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
    const session = drawingSessionRef.current;
    if (!session.active) return;
    const nv = drawingPanes().find((candidate) => asNiivueInterop(candidate).drawBitmap);
    if (!nv) {
      setDrawingError('No drawing is available to save.');
      return;
    }
    try {
      const filename = makeDrawingFilename(session.filename);
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
        lut: inferSavedDrawingLut(asNiivueInterop(nv).drawBitmap, session.source),
        source: session.source,
      });
      closeNativeDrawing(true);
    } catch (error) {
      setDrawingError(error instanceof Error ? error.message : String(error));
    }
  }, [closeNativeDrawing, drawingPanes, onSaveDrawing, setDrawingError]);

  useEffect(() => {
    if (drawingSessionRef.current.active) {
      closeNativeDrawing(true);
    }
  }, [referenceVolumeId, closeNativeDrawing]);

  useEffect(() => () => {
    closeDrawingPanes();
  }, [closeDrawingPanes]);

  return {
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
  };
}
