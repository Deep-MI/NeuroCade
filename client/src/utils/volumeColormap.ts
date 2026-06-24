import { cmapper } from '@niivue/niivue';

import { appFetch } from './api';
import { parseLUT, type LutMap } from './LutParser';
import type { IntensityVolumeLayer, Volume } from '../types';
import type { NiivueColorMap, NiivueLabelLut, NiivueVolumeInterop } from './niivueInterop';

const BINARY_SEGMENTATION_COLORMAP = {
  R: [0, 255],
  G: [0, 120],
  B: [0, 0],
  A: [0, 255],
  I: [0, 1],
};

const OPAQUE_GRAY_COLORMAP_NAME = 'neurocade_gray_opaque';
const OPAQUE_GRAY_COLORMAP = {
  R: [0, 255],
  G: [0, 255],
  B: [0, 255],
  A: [255, 255],
  I: [0, 255],
};

const DEFAULT_FREESURFER_MAX_LABEL = 4095;

let freesurferColorMap: NiivueColorMap | null = null;
let freesurferColorMapPromise: Promise<NiivueColorMap> | null = null;
const freesurferLabelLutCache = new Map<number, NiivueLabelLut>();
let binarySegmentationLabelLut: NiivueLabelLut | null = null;

function ensureOpaqueGrayColormap(): void {
  cmapper.addColormap(OPAQUE_GRAY_COLORMAP_NAME, OPAQUE_GRAY_COLORMAP);
}

ensureOpaqueGrayColormap();

export function getBinarySegmentationLabelLut(): NiivueLabelLut {
  binarySegmentationLabelLut ??= cmapper.makeLabelLut(BINARY_SEGMENTATION_COLORMAP);
  return binarySegmentationLabelLut;
}

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
    return colormap.toLowerCase() === 'gray' ? OPAQUE_GRAY_COLORMAP_NAME : colormap;
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

export function maxLabelForVolume(volume?: NiivueVolumeInterop): number {
  const candidates = [volume?.global_max, volume?.cal_max, volume?.hdr?.cal_max];
  const maxLabel = candidates.find((value) => typeof value === 'number' && Number.isFinite(value) && value > 0);
  return Math.max(1, Math.ceil(maxLabel ?? DEFAULT_FREESURFER_MAX_LABEL));
}

function capNiivueColorMap(colorMap: NiivueColorMap, maxLabel: number): NiivueColorMap {
  const capped = colorMap.I
    .map((label, index) => ({ label, index }))
    .filter(({ label }) => label <= maxLabel);
  return {
    R: capped.map(({ index }) => colorMap.R[index]),
    G: capped.map(({ index }) => colorMap.G[index]),
    B: capped.map(({ index }) => colorMap.B[index]),
    A: capped.map(({ index }) => colorMap.A[index]),
    I: capped.map(({ label }) => label),
    labels: capped.map(({ index }) => colorMap.labels?.[index] ?? String(colorMap.I[index])),
  };
}

export function getCachedFreesurferLabelLut(maxLabel: number): NiivueLabelLut | undefined {
  const cached = freesurferLabelLutCache.get(maxLabel);
  if (cached) return cached;
  if (!freesurferColorMap) return undefined;
  const labelLut = cmapper.makeLabelLut(capNiivueColorMap(freesurferColorMap, maxLabel), 255, maxLabel);
  freesurferLabelLutCache.set(maxLabel, labelLut);
  return labelLut;
}

export async function getFreesurferLabelLut(maxLabel: number): Promise<NiivueLabelLut> {
  const cached = getCachedFreesurferLabelLut(maxLabel);
  if (cached) return cached;
  const colorMap = await getFreesurferColorMap();
  const labelLut = cmapper.makeLabelLut(capNiivueColorMap(colorMap, maxLabel), 255, maxLabel);
  freesurferLabelLutCache.set(maxLabel, labelLut);
  return labelLut;
}

export async function resolveVolumeLabelColormap(volume: Volume, loaded?: NiivueVolumeInterop): Promise<NiivueLabelLut | undefined> {
  if (volume.type !== 'segmentation') return undefined;
  const lut = inferredSegmentationLut(volume);
  if (lut === 'binary') return getBinarySegmentationLabelLut();
  if (lut === 'freesurfer') return getFreesurferLabelLut(maxLabelForVolume(loaded));
  return undefined;
}

export function resolveCachedVolumeLabelColormap(volume: Volume, loaded?: NiivueVolumeInterop): NiivueLabelLut | undefined {
  if (volume.type !== 'segmentation') return undefined;
  const lut = inferredSegmentationLut(volume);
  if (lut === 'binary') return getBinarySegmentationLabelLut();
  if (lut === 'freesurfer') return getCachedFreesurferLabelLut(maxLabelForVolume(loaded));
  return undefined;
}

export function applyBrightnessContrast(loaded: NiivueVolumeInterop, volume: IntensityVolumeLayer): void {
  const brightness = volume.brightness ?? 0;
  const contrast = volume.contrast ?? 1.0;
  const min = loaded.global_min ?? 0;
  const max = loaded.global_max ?? 1;
  const range = max - min;
  if (range === 0) return;
  const center = (min + max) / 2 + (brightness / 100) * range;
  const width = range / Math.max(0.01, contrast);
  loaded.cal_min = center - width / 2;
  loaded.cal_max = center + width / 2;
}
