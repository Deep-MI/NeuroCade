import type { Volume } from '../types.js';
import type { NiivueLabelLut, NiivueVolumeInterop } from '../utils/niivueInterop.js';

export const RGBA32_DATATYPE_CODE = 2304;
export const NIFTI_INTENT_NONE = 0;

export function fallbackSegmentationColor(label: number): [number, number, number, number] {
  const value = Math.abs(Math.trunc(label));
  return [
    (value * 53 + 97) % 256,
    (value * 97 + 61) % 256,
    (value * 193 + 29) % 256,
    255,
  ];
}

export function segmentationLabelColor(label: number, labelLut: NiivueLabelLut): [number, number, number, number] {
  if (label <= 0) return [0, 0, 0, 0];
  const min = labelLut.min ?? 0;
  const offset = (label - min) * 4;
  if (offset >= 0 && offset + 3 < labelLut.lut.length) {
    const alpha = labelLut.lut[offset + 3];
    if (alpha > 0) {
      return [
        labelLut.lut[offset],
        labelLut.lut[offset + 1],
        labelLut.lut[offset + 2],
        alpha,
      ];
    }
  }
  return fallbackSegmentationColor(label);
}

export function buildSegmentationRgba(rawData: ArrayLike<number>, voxelCount: number, labelLut: NiivueLabelLut): Uint8Array {
  const rgba = new Uint8Array(voxelCount * 4);
  const count = Math.min(voxelCount, rawData.length);
  for (let index = 0; index < count; index += 1) {
    const label = Math.round(Number(rawData[index]) || 0);
    const [red, green, blue, alpha] = segmentationLabelColor(label, labelLut);
    const offset = index * 4;
    rgba[offset] = red;
    rgba[offset + 1] = green;
    rgba[offset + 2] = blue;
    rgba[offset + 3] = alpha;
  }
  return rgba;
}

function loadedVolumeDims(loaded: NiivueVolumeInterop): [number, number, number] | null {
  const dims = loaded.dims ?? loaded.hdr?.dims;
  const x = Math.trunc(dims?.[1] ?? 0);
  const y = Math.trunc(dims?.[2] ?? 0);
  const z = Math.trunc(dims?.[3] ?? 0);
  return x > 0 && y > 0 && z > 0 ? [x, y, z] : null;
}

function segmentationRgbaKey(volume: Volume, rawData: ArrayLike<number>, labelLut: NiivueLabelLut): string {
  let lutHash = 0;
  for (let index = 0; index < labelLut.lut.length; index += 1) {
    lutHash = ((lutHash * 31) + labelLut.lut[index]) >>> 0;
  }
  const lutName = volume.type === 'segmentation' ? volume.lut ?? '' : '';
  const customLutUrl = volume.type === 'segmentation' ? volume.customLutUrl ?? '' : '';
  return [
    volume.id,
    volume.type,
    lutName,
    customLutUrl,
    rawData.length,
    labelLut.min ?? 0,
    labelLut.max ?? 0,
    labelLut.lut.length,
    lutHash,
  ].join(':');
}

export function applySegmentationRgbaRendering(loaded: NiivueVolumeInterop, volume: Volume, labelLut: NiivueLabelLut | null | undefined): void {
  if (volume.type !== 'segmentation' || !labelLut) return;
  const dims = loadedVolumeDims(loaded);
  if (!dims) return;
  const rawData = loaded.__rawLabelData ?? loaded.img;
  if (!rawData) return;

  const key = segmentationRgbaKey(volume, rawData, labelLut);
  if (loaded.__segmentationRgbaKey === key && loaded.hdr?.datatypeCode === RGBA32_DATATYPE_CODE) return;

  const voxelCount = dims[0] * dims[1] * dims[2];
  loaded.__rawLabelData = rawData;
  loaded.__rawLabelDims = dims;
  loaded.__rawLabelColormap = labelLut;
  loaded.__segmentationRgbaKey = key;
  loaded.img = buildSegmentationRgba(rawData, voxelCount, labelLut);
  loaded.colormapLabel = null;
  if (loaded.hdr) {
    loaded.hdr.datatypeCode = RGBA32_DATATYPE_CODE;
    loaded.hdr.intent_code = NIFTI_INTENT_NONE;
    loaded.hdr.cal_min = 0;
    loaded.hdr.cal_max = 255;
  }
  loaded.cal_min = 0;
  loaded.cal_max = 255;
}
