import { isSurfaceLayer, type SurfaceLayer, type Volume } from '../types.js';
import { surfaceDisplayKey, volumesInRenderOrder } from './layerDisplay.js';

export interface WindowSetting {
  calMin: number;
  calMax: number;
  globalMin: number;
  globalMax: number;
}

export function layerReconcileKeyOf(volumes: Volume[]): string {
  return volumes
    .map((volume) => [
      volume.id,
      volume.url,
      volume.filename,
      volume.type ?? 'intensity',
      volume.visible ? 1 : 0,
      isSurfaceLayer(volume) ? volume.curvatureUrl ?? '' : '',
      isSurfaceLayer(volume) ? volume.annotationUrl ?? '' : '',
    ].join(':'))
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
    .map((surface: SurfaceLayer) => `${surface.id}:${surfaceDisplayKey(surface)}`)
    .sort()
    .join('|');
}
