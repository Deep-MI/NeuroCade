import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import Niivue from '@niivue/niivue';

import type { SegmentationVolumeLayer } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import {
  DEFAULT_DRAWING_OPTIONS,
  drawingSourceFromSegmentation,
  filenameForSegmentationDrawing,
  inferSavedDrawingLut,
  loadDrawingSourceFile,
  makeDrawingFilename,
  popUndoBitmap,
  pushUndoBitmap,
  type DrawingLut,
  type DrawingOptions,
  type DrawingSession,
} from './nativeDrawing';

export interface SavedDrawingPayload {
  filename: string;
  data: Uint8Array;
  lut: DrawingLut;
  source?: DrawingSession['source'];
}

interface UseNativeDrawingSessionArgs {
  canAddLayers: boolean;
  instanceRef: MutableRefObject<Niivue | null>;
  referenceVolumeId: string | null;
  onSaveDrawing?: (drawing: SavedDrawingPayload) => Promise<void>;
}

export function useNativeDrawingSession({
  canAddLayers,
  instanceRef,
  referenceVolumeId,
  onSaveDrawing,
}: UseNativeDrawingSessionArgs) {
  const drawingSessionRef = useRef<DrawingSession>({ ...DEFAULT_DRAWING_OPTIONS, active: false, dirty: false, error: null });
  const drawingBitmapRef = useRef<Uint8Array | null>(null);
  const drawingUndoStackRef = useRef<Uint8Array[]>([]);
  const applyingBitmapRef = useRef(false);
  const registeredInstanceRef = useRef<Niivue | null>(null);
  const [drawingSession, setDrawingSession] = useState<DrawingSession>(drawingSessionRef.current);
  const [canUndo, setCanUndo] = useState(false);
  const [drawingMenuOpen, setDrawingMenuOpen] = useState(false);
  drawingSessionRef.current = drawingSession;

  const setDrawingError = useCallback((message: string | null) => {
    const next = { ...drawingSessionRef.current, error: message };
    drawingSessionRef.current = next;
    setDrawingSession(next);
  }, []);

  const drawingBitmap = useCallback((nv: Niivue): Uint8Array | null => {
    const image = nv.drawingVolume?.img;
    return image instanceof Uint8Array ? image : null;
  }, []);

  const applySessionDrawColormap = useCallback((nv: Niivue, session: DrawingSession) => {
    const colormap = session.source?.colormap;
    if (!colormap) return;
    try {
      nv.drawColormap = colormap;
    } catch {
      // Unknown colormap name: keep NiiVue's default draw palette.
    }
  }, []);

  const applyDrawingOptions = useCallback((nv: Niivue, options: DrawingOptions) => {
    nv.drawOpacity = options.opacity;
    nv.drawIsFillOverwriting = options.penFill;
    nv.drawPenValue = options.erase ? 0 : options.penValue;
    nv.drawPenFilled = options.mode === 'pen' && options.penFill;
    nv.drawIsEnabled = options.mode !== 'none';
  }, []);

  const applyBitmap = useCallback((nv: Niivue, bitmap: Uint8Array, options: DrawingOptions) => {
    if (!nv.drawingVolume) nv.createEmptyDrawing();
    if (!nv.drawingVolume) return;
    applyingBitmapRef.current = true;
    try {
      nv.drawIsEnabled = true;
      nv.drawingVolume.img = new Uint8Array(bitmap);
      nv.drawUndoBitmaps = [];
      nv.currentDrawUndoBitmap = 0;
      nv.refreshDrawing();
      applyDrawingOptions(nv, options);
    } finally {
      applyingBitmapRef.current = false;
    }
  }, [applyDrawingOptions]);

  const closeDrawing = useCallback(() => {
    const nv = instanceRef.current;
    if (nv) {
      nv.drawIsEnabled = false;
      nv.drawPenValue = 0;
      nv.closeDrawing();
    }
    drawingBitmapRef.current = null;
    drawingUndoStackRef.current = [];
  }, [instanceRef]);

  const closeNativeDrawing = useCallback((resetSession: boolean) => {
    closeDrawing();
    setCanUndo(false);
    if (resetSession) {
      const next = { ...DEFAULT_DRAWING_OPTIONS, active: false, dirty: false, error: null };
      drawingSessionRef.current = next;
      setDrawingSession(next);
    }
  }, [closeDrawing]);

  const recordDrawingStroke = useCallback((nv: Niivue, action: string) => {
    if (applyingBitmapRef.current || action !== 'stroke' || !drawingSessionRef.current.active) return;
    const sourceBitmap = drawingBitmap(nv);
    if (!sourceBitmap) return;
    const bitmap = new Uint8Array(sourceBitmap);
    drawingBitmapRef.current = bitmap;
    drawingUndoStackRef.current = pushUndoBitmap(drawingUndoStackRef.current, bitmap);
    setCanUndo(drawingUndoStackRef.current.length > 1);
    const next = { ...drawingSessionRef.current, dirty: true, error: null };
    drawingSessionRef.current = next;
    setDrawingSession(next);
  }, [drawingBitmap]);

  const registerDrawingPane = useCallback((nv: Niivue) => {
    if (registeredInstanceRef.current === nv) return;
    registeredInstanceRef.current = nv;
    nv.addEventListener('drawingChanged', (event) => recordDrawingStroke(nv, event.detail.action));
    const session = drawingSessionRef.current;
    const bitmap = drawingBitmapRef.current;
    if (session.active && bitmap) {
      applyBitmap(nv, bitmap, session);
      applySessionDrawColormap(nv, session);
    }
  }, [applyBitmap, applySessionDrawColormap, recordDrawingStroke]);

  const updateDrawingOptions = useCallback((updates: Partial<DrawingOptions>) => {
    const next = { ...drawingSessionRef.current, ...updates, error: null };
    drawingSessionRef.current = next;
    const nv = instanceRef.current;
    if (nv) applyDrawingOptions(nv, next);
    setDrawingSession(next);
  }, [applyDrawingOptions, instanceRef]);

  const referenceVolumeForDrawing = useCallback((): NiivueVolumeInterop | null => {
    const nv = instanceRef.current;
    return nv ? asNiivueInterop(nv).volumes[0] ?? null : null;
  }, [instanceRef]);

  const beginBlankDrawing = useCallback(() => {
    const nv = instanceRef.current;
    if (!canAddLayers) {
      setDrawingError('Load or create a case before starting a drawing.');
      return;
    }
    if (!nv || !referenceVolumeForDrawing()) {
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
    nv.drawIsEnabled = true;
    nv.createEmptyDrawing();
    nv.drawUndoBitmaps = [];
    nv.currentDrawUndoBitmap = 0;
    applyDrawingOptions(nv, session);
    const bitmap = drawingBitmap(nv);
    drawingBitmapRef.current = bitmap ? new Uint8Array(bitmap) : null;
    drawingUndoStackRef.current = drawingBitmapRef.current ? [drawingBitmapRef.current] : [];
    setCanUndo(false);
    setDrawingSession(session);
  }, [applyDrawingOptions, canAddLayers, closeNativeDrawing, drawingBitmap, instanceRef, referenceVolumeForDrawing, setDrawingError]);

  const beginDrawingFromSegmentation = useCallback(async (source: SegmentationVolumeLayer) => {
    const nv = instanceRef.current;
    if (!canAddLayers) {
      setDrawingError('Load or create a case before editing a label map.');
      return;
    }
    if (!nv || !referenceVolumeForDrawing()) {
      setDrawingError('Load an intensity volume before editing a label map.');
      return;
    }

    setDrawingError(null);
    try {
      const sourceFile = await loadDrawingSourceFile(source);
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
      const loaded = await nv.loadDrawing(sourceFile);
      const sourceBitmap = drawingBitmap(nv);
      if (loaded === false || !sourceBitmap) {
        closeNativeDrawing(true);
        setDrawingError('Could not initialize drawing from the selected label map.');
        return;
      }
      const bitmap = new Uint8Array(sourceBitmap);
      drawingBitmapRef.current = bitmap;
      drawingUndoStackRef.current = [bitmap];
      setCanUndo(false);
      applyDrawingOptions(nv, session);
      applySessionDrawColormap(nv, session);
      setDrawingSession(session);
    } catch (error) {
      setDrawingError(error instanceof Error ? error.message : String(error));
    }
  }, [
    applyDrawingOptions,
    applySessionDrawColormap,
    canAddLayers,
    closeNativeDrawing,
    drawingBitmap,
    instanceRef,
    referenceVolumeForDrawing,
    setDrawingError,
  ]);

  const handleDrawUndo = useCallback(() => {
    const { stack, current } = popUndoBitmap(drawingUndoStackRef.current);
    const nv = instanceRef.current;
    if (stack === drawingUndoStackRef.current || !current || !nv) return;
    drawingUndoStackRef.current = stack;
    drawingBitmapRef.current = current;
    setCanUndo(stack.length > 1);
    const session = { ...drawingSessionRef.current, dirty: stack.length > 1 };
    drawingSessionRef.current = session;
    setDrawingSession(session);
    applyBitmap(nv, current, session);
  }, [applyBitmap, instanceRef]);

  const handleSaveDrawing = useCallback(async () => {
    const session = drawingSessionRef.current;
    const nv = instanceRef.current;
    if (!session.active || !nv || !drawingBitmap(nv)) {
      if (session.active) setDrawingError('No drawing is available to save.');
      return;
    }
    try {
      const saved = await nv.saveVolume({
        filename: '',
        isSaveDrawing: true,
        volumeByIndex: 0,
      });
      if (!(saved instanceof Uint8Array)) {
        setDrawingError('Could not export the current drawing.');
        return;
      }
      await onSaveDrawing?.({
        filename: makeDrawingFilename(session.filename),
        data: saved,
        lut: inferSavedDrawingLut(drawingBitmap(nv), session.source),
        source: session.source,
      });
      closeNativeDrawing(true);
    } catch (error) {
      setDrawingError(error instanceof Error ? error.message : String(error));
    }
  }, [closeNativeDrawing, drawingBitmap, instanceRef, onSaveDrawing, setDrawingError]);

  useEffect(() => {
    if (drawingSessionRef.current.active) closeNativeDrawing(true);
  }, [referenceVolumeId, closeNativeDrawing]);

  useEffect(() => () => {
    closeDrawing();
    registeredInstanceRef.current = null;
  }, [closeDrawing]);

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
