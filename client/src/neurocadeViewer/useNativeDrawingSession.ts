import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import type Niivue from '@niivue/niivue';

import type { SegmentationVolumeLayer } from '../types';
import { asNiivueInterop, type NiivueColorMap, type NiivueVolumeInterop } from '../utils/niivueInterop';
import { getFreesurferColorMap } from '../utils/volumeColormap';
import {
  DEFAULT_DRAWING_OPTIONS,
  drawingSourceFromSegmentation,
  filenameForSegmentationDrawing,
  loadDrawingSourceFile,
  makeDrawingFilename,
  type DrawingLut,
  type DrawingOptions,
  type DrawingSession,
} from './nativeDrawing';

export interface DrawingLabelOption {
  value: number;
  name: string;
  color: string;
}

export interface SavedDrawingPayload {
  filename: string;
  data: Uint8Array;
  lut: DrawingLut;
  source?: DrawingSession['source'];
}

interface UseNativeDrawingSessionArgs {
  canAddLayers: boolean;
  caseId: string;
  instanceRef: MutableRefObject<Niivue | null>;
  onSaveDrawing?: (drawing: SavedDrawingPayload) => Promise<void>;
}

const BINARY_DRAWING_COLORMAP: NiivueColorMap = {
  R: [0, 255],
  G: [0, 120],
  B: [0, 0],
  A: [0, 255],
  I: [0, 1],
  labels: ['Background', 'Structure'],
};

function labelOptions(colorMap: NiivueColorMap): DrawingLabelOption[] {
  const indices = colorMap.I ?? colorMap.R.map((_, index) => index);
  return indices.flatMap((value, index) => {
    // NiiVue's native drawing volume is Uint8, so values outside this range
    // cannot be round-tripped without truncation.
    if (value <= 0 || value > 255) return [];
    return [{
      value,
      name: colorMap.labels?.[index] ?? `Label ${value}`,
      color: `rgb(${colorMap.R[index]}, ${colorMap.G[index]}, ${colorMap.B[index]})`,
    }];
  });
}

export function useNativeDrawingSession({
  canAddLayers,
  caseId,
  instanceRef,
  onSaveDrawing,
}: UseNativeDrawingSessionArgs) {
  const initialSession: DrawingSession = {
    ...DEFAULT_DRAWING_OPTIONS,
    active: false,
    dirty: false,
    error: null,
  };
  const drawingSessionRef = useRef(initialSession);
  const registeredInstanceRef = useRef<Niivue | null>(null);
  const strokeCountRef = useRef(0);
  const [drawingSession, setDrawingSession] = useState(initialSession);
  const [drawingLabels, setDrawingLabels] = useState<DrawingLabelOption[]>([]);
  const [canUndo, setCanUndo] = useState(false);
  drawingSessionRef.current = drawingSession;

  const replaceSession = useCallback((session: DrawingSession) => {
    drawingSessionRef.current = session;
    setDrawingSession(session);
  }, []);

  const setDrawingError = useCallback((message: string | null) => {
    replaceSession({ ...drawingSessionRef.current, error: message });
  }, [replaceSession]);

  const drawingBitmap = useCallback((nv: Niivue): Uint8Array | null => {
    const image = nv.drawingVolume?.img;
    return image instanceof Uint8Array ? image : null;
  }, []);

  const applyDrawingPalette = useCallback(async (nv: Niivue, lut: DrawingLut) => {
    const colorMap = lut === 'freesurfer'
      ? await getFreesurferColorMap()
      : BINARY_DRAWING_COLORMAP;
    const name = lut === 'freesurfer' ? 'NeuroCade FreeSurfer drawing' : 'NeuroCade binary drawing';
    nv.drawColormap = nv.addColormap(name, colorMap);
    setDrawingLabels(labelOptions(colorMap));
  }, []);

  const applyDrawingOptions = useCallback((nv: Niivue, options: DrawingOptions) => {
    const drawingEnabled = options.tool !== 'navigate';
    nv.drawOpacity = options.opacity;
    nv.drawPenValue = options.tool === 'erase' ? 0 : options.penValue;
    nv.drawPenSize = options.brushSize;
    nv.drawPenAutoClose = options.fillOutline;
    nv.drawPenFilled = options.fillOutline;
    nv.drawIsFillOverwriting = options.overwrite || options.tool === 'erase';
    nv.drawIsEnabled = drawingEnabled;
  }, []);

  const resetUndoState = useCallback(() => {
    strokeCountRef.current = 0;
    setCanUndo(false);
  }, []);

  const closeDrawing = useCallback(() => {
    const nv = instanceRef.current;
    if (nv?.drawingVolume) nv.closeDrawing();
    resetUndoState();
  }, [instanceRef, resetUndoState]);

  const closeNativeDrawing = useCallback((resetSession: boolean) => {
    closeDrawing();
    if (resetSession) {
      replaceSession({
        ...DEFAULT_DRAWING_OPTIONS,
        active: false,
        dirty: false,
        error: null,
      });
    }
  }, [closeDrawing, replaceSession]);

  const registerDrawingPane = useCallback((nv: Niivue) => {
    if (registeredInstanceRef.current === nv) return;
    registeredInstanceRef.current = nv;
    nv.addEventListener('drawingChanged', (event) => {
      if (!drawingSessionRef.current.active) return;
      if (event.detail.action === 'stroke') {
        strokeCountRef.current += 1;
      } else if (event.detail.action === 'undo') {
        strokeCountRef.current = Math.max(0, strokeCountRef.current - 1);
      } else {
        return;
      }
      setCanUndo(strokeCountRef.current > 0);
      replaceSession({
        ...drawingSessionRef.current,
        dirty: strokeCountRef.current > 0,
        error: null,
      });
    });
  }, [replaceSession]);

  const updateDrawingOptions = useCallback((updates: Partial<DrawingOptions>) => {
    const next = { ...drawingSessionRef.current, ...updates, error: null };
    replaceSession(next);
    const nv = instanceRef.current;
    if (!nv) return;
    applyDrawingOptions(nv, next);
    if (updates.lut) {
      void applyDrawingPalette(nv, next.lut).catch((error) => {
        setDrawingError(error instanceof Error ? error.message : String(error));
      });
    }
  }, [applyDrawingOptions, applyDrawingPalette, instanceRef, replaceSession, setDrawingError]);

  const referenceVolumeForDrawing = useCallback((): NiivueVolumeInterop | null => {
    const nv = instanceRef.current;
    return nv ? asNiivueInterop(nv).volumes[0] ?? null : null;
  }, [instanceRef]);

  const validateDrawingStart = useCallback((): Niivue | null => {
    if (!canAddLayers) {
      setDrawingError('Load or create a case before starting a label edit.');
      return null;
    }
    const nv = instanceRef.current;
    if (!nv || !referenceVolumeForDrawing()) {
      setDrawingError('Load an intensity volume before starting a label edit.');
      return null;
    }
    return nv;
  }, [canAddLayers, instanceRef, referenceVolumeForDrawing, setDrawingError]);

  const beginBlankDrawing = useCallback(() => {
    const nv = validateDrawingStart();
    if (!nv) return;
    closeNativeDrawing(false);
    const session: DrawingSession = {
      ...DEFAULT_DRAWING_OPTIONS,
      tool: 'paint',
      active: true,
      dirty: false,
      error: null,
      filename: `labels-${Date.now()}.nii`,
    };
    replaceSession(session);
    nv.createEmptyDrawing();
    applyDrawingOptions(nv, session);
    resetUndoState();
    void applyDrawingPalette(nv, session.lut).catch((error) => {
      setDrawingError(error instanceof Error ? error.message : String(error));
    });
  }, [
    applyDrawingOptions,
    applyDrawingPalette,
    closeNativeDrawing,
    replaceSession,
    resetUndoState,
    setDrawingError,
    validateDrawingStart,
  ]);

  const beginDrawingFromSegmentation = useCallback(async (source: SegmentationVolumeLayer) => {
    const nv = validateDrawingStart();
    if (!nv) return;
    setDrawingError(null);
    try {
      const sourceFile = await loadDrawingSourceFile(source);
      closeNativeDrawing(false);
      const session: DrawingSession = {
        ...DEFAULT_DRAWING_OPTIONS,
        tool: 'paint',
        lut: source.lut ?? 'freesurfer',
        active: true,
        dirty: false,
        error: null,
        filename: filenameForSegmentationDrawing(source),
        source: drawingSourceFromSegmentation(source),
      };
      replaceSession(session);
      const loaded = await nv.loadDrawing(sourceFile);
      if (loaded === false || !drawingBitmap(nv)) {
        closeNativeDrawing(true);
        setDrawingError('Could not initialize labels from the selected segmentation.');
        return;
      }
      applyDrawingOptions(nv, session);
      await applyDrawingPalette(nv, session.lut);
      resetUndoState();
    } catch (error) {
      setDrawingError(error instanceof Error ? error.message : String(error));
    }
  }, [
    applyDrawingOptions,
    applyDrawingPalette,
    closeNativeDrawing,
    drawingBitmap,
    replaceSession,
    resetUndoState,
    setDrawingError,
    validateDrawingStart,
  ]);

  const handleDrawUndo = useCallback(() => {
    if (!canUndo) return;
    instanceRef.current?.drawUndo();
  }, [canUndo, instanceRef]);

  const handleSaveDrawing = useCallback(async () => {
    const session = drawingSessionRef.current;
    const nv = instanceRef.current;
    if (!session.active || !nv || !drawingBitmap(nv)) {
      if (session.active) setDrawingError('No labels are available to save.');
      return;
    }
    if (!onSaveDrawing) {
      setDrawingError('Saving labels is not available in this case.');
      return;
    }
    try {
      const saved = await nv.saveVolume({
        filename: '',
        isSaveDrawing: true,
        volumeByIndex: 0,
      });
      if (!(saved instanceof Uint8Array)) {
        setDrawingError('Could not export the current labels.');
        return;
      }
      await onSaveDrawing({
        filename: makeDrawingFilename(session.filename),
        data: saved,
        lut: session.lut,
        source: session.source,
      });
      closeNativeDrawing(true);
    } catch (error) {
      setDrawingError(error instanceof Error ? error.message : String(error));
    }
  }, [closeNativeDrawing, drawingBitmap, instanceRef, onSaveDrawing, setDrawingError]);

  useEffect(() => {
    if (drawingSessionRef.current.active) closeNativeDrawing(true);
  }, [caseId, closeNativeDrawing]);

  useEffect(() => () => {
    closeDrawing();
    registeredInstanceRef.current = null;
  }, [closeDrawing]);

  return {
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
  };
}
