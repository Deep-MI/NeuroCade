import { isSurfaceLayer, type SurfaceLayer, type Volume } from '../types.js';
import { resolveSurfaceLayerColorMode } from '../utils/surfaceColors.js';

export interface WindowSetting {
  calMin: number;
  calMax: number;
  globalMin: number;
  globalMax: number;
}

function affineKey(affine?: number[][]): string {
  return affine ? affine.flat().map((value) => Number(value).toPrecision(8)).join(',') : '';
}

function surfaceDisplayKey(surface: SurfaceLayer): string {
  const colorMode = resolveSurfaceLayerColorMode(surface);
  const companionUrl = colorMode === 'annotation' ? surface.annotationUrl : colorMode === 'curvature' ? surface.curvatureUrl : '';
  return `${colorMode}:${companionUrl ?? ''}`;
}

function volumesInRenderOrder(sources: Volume[]): Volume[] {
  const nonSurface = sources.filter((volume) => !isSurfaceLayer(volume));
  const intensities = nonSurface.filter((volume) => volume.type !== 'segmentation');
  const segmentations = nonSurface.filter((volume) => volume.type === 'segmentation');
  return [...intensities.reverse(), ...segmentations.reverse()];
}

export function sourceVisibilityKeyOf(volumes: Volume[]): string {
  return volumes
    .filter(isSurfaceLayer)
    .filter((volume) => volume.visible)
    .map((volume) => `${volume.id}:${volume.url}:${volume.filename}:${volume.type ?? 'intensity'}`)
    .sort()
    .join('|');
}

export function volumeVisibilityKeyOf(volumes: Volume[]): string {
  return volumes
    .map((volume) => `${volume.id}:${volume.visible ? 1 : 0}:${volume.opacity}`)
    .sort()
    .join('|');
}

export function volumeAppearanceKeyOf(volumes: Volume[]): string {
  return volumes
    .filter((volume) => !isSurfaceLayer(volume))
    .map((volume) => [
      volume.id,
      volume.colormap,
      volume.type ?? 'intensity',
      volume.type === 'segmentation' ? volume.lut ?? '' : '',
      volume.type === 'segmentation' ? volume.customLutUrl ?? '' : '',
      'brightness' in volume ? volume.brightness ?? '' : '',
      'contrast' in volume ? volume.contrast ?? '' : '',
    ].join(':'))
    .sort()
    .join('|');
}

export function windowingKeyOf(windowings: Record<string, WindowSetting>): string {
  return Object.entries(windowings)
    .map(([id, value]) => `${id}:${value.calMin}:${value.calMax}:${value.globalMin}:${value.globalMax}`)
    .sort()
    .join('|');
}

export function volumeOrderKeyOf(volumes: Volume[]): string {
  return volumesInRenderOrder(volumes).map((volume) => volume.id).join('|');
}

export function surfaceAppearanceKeyOf(volumes: Volume[]): string {
  return volumes
    .filter(isSurfaceLayer)
    .map((surface) => [
      surface.id,
      surfaceDisplayKey(surface),
      surface.curvatureNegativeThreshold ?? '',
      surface.curvaturePositiveThreshold ?? '',
    ].join(':'))
    .sort()
    .join('|');
}

export function surfaceTransformKeyOf(volumes: Volume[]): string {
  return volumes.map((volume) => (
    isSurfaceLayer(volume)
      ? [volume.id, affineKey(volume.surfaceReferenceAffine)].join(':')
      : [volume.id, volume.visible ? 1 : 0, volume.url].join(':')
  )).join('|');
}
