import type { SegmentationVolumeLayer, Volume } from '../types.js';

export type DrawingTool = 'navigate' | 'paint' | 'erase';
export type DrawingLut = 'binary' | 'freesurfer';

export interface DrawingOptions {
  tool: DrawingTool;
  lut: DrawingLut;
  penValue: number;
  brushSize: number;
  fillOutline: boolean;
  overwrite: boolean;
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

export interface DrawingSession extends DrawingOptions {
  active: boolean;
  dirty: boolean;
  source?: DrawingSource;
  error?: string | null;
}

export const DEFAULT_DRAWING_OPTIONS: DrawingOptions = {
  tool: 'navigate',
  lut: 'binary',
  penValue: 1,
  brushSize: 1,
  fillOutline: false,
  overwrite: true,
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
