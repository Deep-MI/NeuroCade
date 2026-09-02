import { isSurfaceLayer, type SurfaceLayer, type Volume } from '../types.js';
import {
  curvatureNegativeThreshold,
  curvaturePositiveThreshold,
  resolveSurfaceLayerColorMode,
} from '../utils/surfaceColors.js';

export function clampOpacity(value: number | undefined, fallback = 0.75): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
  return Math.max(0, Math.min(1, value));
}

export function layerDefaultOpacity(volume: Volume): number {
  return volume.type === 'segmentation' ? 0.55 : 1;
}

export function effectiveLayerOpacity(volume: Volume): number {
  return volume.visible ? clampOpacity(volume.opacity, layerDefaultOpacity(volume)) : 0;
}

export function parseEditableSliderValue(
  text: string,
  min: number,
  max: number,
  constrainToSliderRange: boolean,
): number | null {
  if (text.trim() === '') return null;
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return null;
  return constrainToSliderRange
    ? Math.max(min, Math.min(max, parsed))
    : parsed;
}

/**
 * NiiVue renders volumes from bottom to top. The layer panel presents them in
 * the opposite direction, with anatomical intensity volumes below label maps.
 */
export function volumesInRenderOrder(sources: Volume[]): Volume[] {
  const volumes = sources.filter((volume) => !isSurfaceLayer(volume));
  const intensities = volumes.filter((volume) => volume.type !== 'segmentation');
  const segmentations = volumes.filter((volume) => volume.type === 'segmentation');
  return [...intensities.reverse(), ...segmentations.reverse()];
}

/**
 * Return the pane's bottom intensity/volume for voxel coordinate readouts.
 * NiiVue volume zero is the separate fixed reference grid.
 */
export function orderedReferenceCandidate(sources: Volume[]): Volume | null {
  return volumesInRenderOrder(sources)[0] ?? null;
}

/**
 * Everything that requires rebuilding a surface companion layer. Keeping this
 * key next to the ordering rules prevents React synchronization and NiiVue
 * reconciliation from drifting apart.
 */
export function surfaceDisplayKey(surface: SurfaceLayer): string {
  const colorMode = resolveSurfaceLayerColorMode(surface);
  if (colorMode === 'annotation') {
    return `${colorMode}:${surface.annotationUrl ?? ''}`;
  }
  if (colorMode === 'curvature') {
    return [
      colorMode,
      surface.curvatureUrl ?? '',
      curvatureNegativeThreshold(surface),
      curvaturePositiveThreshold(surface),
    ].join(':');
  }
  return colorMode;
}
