import { appFetch } from './api';
import { parseLUT, type LutMap } from './LutParser';
import type { IntensityVolumeLayer, Volume } from '../types';
import type { NiivueColorMap, NiivueVolumeInterop } from './niivueInterop';

const BINARY_SEGMENTATION_COLORMAP = {
  R: [0, 255],
  G: [0, 120],
  B: [0, 0],
  A: [0, 255],
  I: [0, 1],
};

let freesurferColorMap: NiivueColorMap | null = null;
let freesurferColorMapPromise: Promise<NiivueColorMap> | null = null;

export function inferredSegmentationLut(volume: Volume): 'binary' | 'freesurfer' | undefined {
  if (volume.type !== 'segmentation') return undefined;
  if (volume.lut === 'binary' || volume.lut === 'freesurfer') return volume.lut;
  const normalized = `${volume.filename} ${volume.name} ${volume.url}`.toLowerCase();
  if (normalized.includes('mask') || normalized.includes('brainmask') || normalized.includes('_bin')) {
    return 'binary';
  }
  return 'freesurfer';
}

export function resolveVolumeColormap(volume: Volume): string {
  if (volume.type !== 'segmentation') {
    const colormap = volume.colormap || 'gray';
    return colormap;
  }
  const lut = inferredSegmentationLut(volume);
  if (lut === 'binary') return 'red';
  if (lut === 'freesurfer') return 'freesurfer';
  return volume.colormap || 'red';
}

function lutMapToNiivueColorMap(lut: LutMap): NiivueColorMap {
  const entries = [...lut.entries()].sort(([left], [right]) => left - right);
  return {
    R: entries.map(([, entry]) => entry.rgb[0]),
    G: entries.map(([, entry]) => entry.rgb[1]),
    B: entries.map(([, entry]) => entry.rgb[2]),
    A: entries.map(([index, entry]) => index === 0 ? 0 : entry.alpha),
    I: entries.map(([index]) => index),
    labels: entries.map(([, entry]) => entry.name),
  };
}

export async function getFreesurferColorMap(): Promise<NiivueColorMap> {
  if (freesurferColorMap) return freesurferColorMap;
  freesurferColorMapPromise ??= appFetch('/static/luts/freesurfer')
    .then((response) => {
      if (!response.ok) throw new Error(`LUT fetch failed: ${response.status}`);
      return response.text();
    })
    .then((text) => {
      freesurferColorMap = lutMapToNiivueColorMap(parseLUT(text));
      return freesurferColorMap;
    })
    .catch((error) => {
      freesurferColorMapPromise = null;
      throw error;
    });
  return freesurferColorMapPromise;
}

export async function resolveVolumeLabelColorMap(volume: Volume): Promise<NiivueColorMap | undefined> {
  if (volume.type !== 'segmentation') return undefined;
  const lut = inferredSegmentationLut(volume);
  if (lut === 'binary') return BINARY_SEGMENTATION_COLORMAP;
  if (lut === 'freesurfer') return getFreesurferColorMap();
  return undefined;
}

export function applyBrightnessContrast(loaded: NiivueVolumeInterop, volume: IntensityVolumeLayer): void {
  const brightness = volume.brightness ?? 0;
  const contrast = volume.contrast ?? 1.0;
  const min = loaded.globalMin ?? 0;
  const max = loaded.globalMax ?? 1;
  const range = max - min;
  if (range === 0) return;
  const center = (min + max) / 2 + (brightness / 100) * range;
  const width = range / Math.max(0.01, contrast);
  loaded.calMin = center - width / 2;
  loaded.calMax = center + width / 2;
}
