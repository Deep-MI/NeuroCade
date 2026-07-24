import type { SegmentationVolumeLayer, Volume } from '../types.js';

export type DrawingMode = 'none' | 'pen';
export type DrawingLut = 'binary' | 'freesurfer';

export interface DrawingOptions {
  mode: DrawingMode;
  penValue: number;
  penFill: boolean;
  erase: boolean;
  opacity: number;
  filename: string;
}

export interface DrawingSource {
  layerId?: string;
  artifactId?: string;
  name: string;
  filename: string;
  url?: string;
  lut?: DrawingLut;
  colormap?: string;
}

/** Maximum number of bitmaps retained in the central cross-pane undo history. */
export const MAX_DRAWING_UNDO = 12;

export interface DrawingSession extends DrawingOptions {
  active: boolean;
  dirty: boolean;
  source?: DrawingSource;
  error?: string | null;
}

export const DEFAULT_DRAWING_OPTIONS: DrawingOptions = {
  mode: 'none',
  penValue: 1,
  penFill: true,
  erase: false,
  opacity: 0.8,
  filename: 'drawing.nii',
};

export function makeDrawingFilename(base: string): string {
  const trimmed = base.trim() || 'drawing';
  if (trimmed.toLowerCase().endsWith('.nii')) return trimmed;
  if (trimmed.toLowerCase().endsWith('.nii.gz')) return trimmed.slice(0, -3);
  return `${trimmed.replace(/\.(mgz|mgh)$/i, '')}.nii`;
}

export function filenameForSegmentationDrawing(source: Volume): string {
  return makeDrawingFilename(`drawing-${source.filename}`);
}

export function maxDrawingValue(bitmap?: Uint8Array | null): number {
  if (!bitmap) return 0;
  let max = 0;
  for (const value of bitmap) {
    if (value > max) max = value;
  }
  return max;
}

export function inferSavedDrawingLut(bitmap: Uint8Array | null | undefined, source?: DrawingSource): DrawingLut {
  if (source?.lut === 'binary' || source?.lut === 'freesurfer') return source.lut;
  return maxDrawingValue(bitmap) <= 1 ? 'binary' : 'freesurfer';
}

export function drawingSourceFromSegmentation(source: SegmentationVolumeLayer): DrawingSource {
  return {
    layerId: source.id,
    artifactId: source.artifactId,
    name: source.name,
    filename: source.filename,
    url: source.url,
    lut: source.lut,
    colormap: source.colormap,
  };
}

/**
 * Append a bitmap to the central undo history, dropping the oldest entries once
 * the cap is exceeded. Returns a new array; entries are stored by reference, so
 * callers must pass a bitmap they own (do not mutate it afterwards).
 */
export function pushUndoBitmap(stack: Uint8Array[], bitmap: Uint8Array, max = MAX_DRAWING_UNDO): Uint8Array[] {
  const next = stack.length >= max ? stack.slice(stack.length - max + 1) : stack.slice();
  next.push(bitmap);
  return next;
}

/**
 * Pop the most recent bitmap, returning the trimmed stack and the bitmap that is
 * now current. The first (baseline) entry is never removed so a session always
 * retains its starting state.
 */
export function popUndoBitmap(stack: Uint8Array[]): { stack: Uint8Array[]; current: Uint8Array | null } {
  if (stack.length <= 1) {
    return { stack, current: stack[stack.length - 1] ?? null };
  }
  const next = stack.slice(0, -1);
  return { stack: next, current: next[next.length - 1] ?? null };
}
