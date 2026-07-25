import { isSurfaceLayer, type SurfaceLayer, type Volume } from '../types.js';
import {
  curvatureNegativeThreshold,
  curvaturePositiveThreshold,
  resolveSurfaceLayerColorMode,
} from '../utils/surfaceColors.js';

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
