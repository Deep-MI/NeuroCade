import {
  DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
  DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
} from '../constants.js';
import type { SurfaceColorMode, SurfaceLayer } from '../types.js';

type SurfaceRgb = [number, number, number];

export function curvatureNegativeThreshold(layer: SurfaceLayer): number {
  return Math.abs(layer.curvatureNegativeThreshold ?? DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD);
}

export function curvaturePositiveThreshold(layer: SurfaceLayer): number {
  return Math.abs(layer.curvaturePositiveThreshold ?? DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD);
}

export function surfaceColor(layer: SurfaceLayer): SurfaceRgb {
  const filename = layer.filename.toLowerCase();
  if (filename.startsWith('lh.')) return [0.47, 0.72, 0.96];
  if (filename.startsWith('rh.')) return [0.96, 0.61, 0.42];
  return [0.78, 0.72, 0.62];
}

export const SURFACE_COLOR_MODE_LABELS: Record<SurfaceColorMode, string> = {
  solid: 'solid',
  curvature: 'curvature',
  annotation: 'parcellation',
};

export function surfaceColorModeAvailable(layer: SurfaceLayer, mode: SurfaceColorMode): boolean {
  if (mode === 'curvature') return !!layer.curvatureUrl;
  if (mode === 'annotation') return !!layer.annotationUrl;
  return true;
}

export function defaultSurfaceColorModeForLayer(layer: SurfaceLayer): SurfaceColorMode {
  if (layer.curvatureUrl) return 'curvature';
  if (layer.annotationUrl) return 'annotation';
  return 'solid';
}

export function resolveSurfaceLayerColorMode(layer: SurfaceLayer): SurfaceColorMode {
  const requested = layer.surfaceColorMode ?? defaultSurfaceColorModeForLayer(layer);
  return surfaceColorModeAvailable(layer, requested) ? requested : defaultSurfaceColorModeForLayer(layer);
}
