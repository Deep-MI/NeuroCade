import { lookupLut, type LutMap } from './LutParser';
import { multiplyAffine, type Matrix4x4, type VolumeData } from './VolumeLoader';
import type { IntensityVolumeLayer, SegmentationVolumeLayer } from '../types';

type Vec3 = [number, number, number];

interface SliceSamplingPlane {
  origin: Vec3;
  stepX: Vec3;
  stepY: Vec3;
}

interface CurrentSlices {
  x: number;
  y: number;
  z: number;
}

export interface IntensityStats {
  min: number;
  max: number;
}

interface DrawMriSliceArgs {
  canvas: HTMLCanvasElement | null;
  axis: number;
  sliceIdx: number;
  baseVolumeUrl: string | null;
  loadedVolumes: Map<string, VolumeData>;
  visibleIntensityLayers: IntensityVolumeLayer[];
  visibleSegmentationLayers: SegmentationVolumeLayer[];
  currentSlices: CurrentSlices;
  lut: LutMap | null;
  binaryLut: LutMap;
  customLuts: Map<string, LutMap>;
  intensityStats: Map<string, IntensityStats>;
}

const IDENTITY_MATRIX: Matrix4x4 = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function computeIntensityStats(data: VolumeData['data']): IntensityStats {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  const stride = Math.max(1, Math.floor(data.length / 50_000));
  for (let i = 0; i < data.length; i += stride) {
    const value = data[i];
    if (value < min) min = value;
    if (value > max) max = value;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return { min: 0, max: 0 };
  }
  return { min, max };
}

function vec3(x: number, y: number, z: number): Vec3 {
  return [x, y, z];
}

function computeSliceSamplingPlane(baseToSeg: Matrix4x4, axis: number, sliceIdx: number, height: number): SliceSamplingPlane {
  if (axis === 0) {
    return {
      origin: vec3(
        baseToSeg[0][0] * sliceIdx + baseToSeg[0][2] * (height - 1) + baseToSeg[0][3],
        baseToSeg[1][0] * sliceIdx + baseToSeg[1][2] * (height - 1) + baseToSeg[1][3],
        baseToSeg[2][0] * sliceIdx + baseToSeg[2][2] * (height - 1) + baseToSeg[2][3],
      ),
      stepX: vec3(baseToSeg[0][1], baseToSeg[1][1], baseToSeg[2][1]),
      stepY: vec3(-baseToSeg[0][2], -baseToSeg[1][2], -baseToSeg[2][2]),
    };
  }
  if (axis === 1) {
    return {
      origin: vec3(
        baseToSeg[0][1] * sliceIdx + baseToSeg[0][2] * (height - 1) + baseToSeg[0][3],
        baseToSeg[1][1] * sliceIdx + baseToSeg[1][2] * (height - 1) + baseToSeg[1][3],
        baseToSeg[2][1] * sliceIdx + baseToSeg[2][2] * (height - 1) + baseToSeg[2][3],
      ),
      stepX: vec3(baseToSeg[0][0], baseToSeg[1][0], baseToSeg[2][0]),
      stepY: vec3(-baseToSeg[0][2], -baseToSeg[1][2], -baseToSeg[2][2]),
    };
  }
  return {
    origin: vec3(
      baseToSeg[0][2] * sliceIdx + baseToSeg[0][1] * (height - 1) + baseToSeg[0][3],
      baseToSeg[1][2] * sliceIdx + baseToSeg[1][1] * (height - 1) + baseToSeg[1][3],
      baseToSeg[2][2] * sliceIdx + baseToSeg[2][1] * (height - 1) + baseToSeg[2][3],
    ),
    stepX: vec3(baseToSeg[0][0], baseToSeg[1][0], baseToSeg[2][0]),
    stepY: vec3(-baseToSeg[0][1], -baseToSeg[1][1], -baseToSeg[2][1]),
  };
}

function sampleNearestData(volumeData: VolumeData, x: number, y: number, z: number): number {
  const sx = Math.round(x);
  const sy = Math.round(y);
  const sz = Math.round(z);
  const [dimX, dimY, dimZ] = volumeData.dims;
  if (sx < 0 || sy < 0 || sz < 0 || sx >= dimX || sy >= dimY || sz >= dimZ) {
    return 0;
  }
  return volumeData.data[sx + sy * dimX + sz * dimX * dimY];
}

export function drawMriSlice({
  canvas,
  axis,
  sliceIdx,
  baseVolumeUrl,
  loadedVolumes,
  visibleIntensityLayers,
  visibleSegmentationLayers,
  currentSlices,
  lut,
  binaryLut,
  customLuts,
  intensityStats,
}: DrawMriSliceArgs): void {
  if (!canvas || !baseVolumeUrl) return;
  const ctx = canvas.getContext('2d', { alpha: false });
  if (!ctx) return;

  ctx.imageSmoothingEnabled = false;

  const baseVData = loadedVolumes.get(baseVolumeUrl);
  if (!baseVData) return;

  const [dimX, dimY, dimZ] = baseVData.dims;
  const width = axis === 0 ? dimY : dimX;
  const height = axis === 2 ? dimY : dimZ;

  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }

  const imageData = ctx.createImageData(width, height);
  for (let i = 0; i < imageData.data.length; i += 4) {
    imageData.data[i] = 0;
    imageData.data[i + 1] = 0;
    imageData.data[i + 2] = 0;
    imageData.data[i + 3] = 255;
  }

  [...visibleIntensityLayers].reverse().forEach((intensityLayer) => {
    const intensityData = loadedVolumes.get(intensityLayer.url);
    if (!intensityData) return;
    const stats = intensityStats.get(intensityLayer.url) ?? { min: 0, max: 0 };
    const brightnessOffset = ((intensityLayer.brightness ?? 0) / 100) * 127;
    const contrastFactor = intensityLayer.contrast ?? 1.0;
    const alpha = clamp(intensityLayer.opacity ?? 1, 0, 1);
    if (alpha <= 0) return;

    const baseToIntensity = intensityLayer.url === baseVolumeUrl
      ? IDENTITY_MATRIX
      : multiplyAffine(intensityData.worldToVoxel, baseVData.voxelToWorld);
    const plane = computeSliceSamplingPlane(baseToIntensity, axis, sliceIdx, height);

    let rowX = plane.origin[0];
    let rowY = plane.origin[1];
    let rowZ = plane.origin[2];

    for (let j = 0; j < height; j++) {
      let sampleX = rowX;
      let sampleY = rowY;
      let sampleZ = rowZ;

      for (let i = 0; i < width; i++) {
        const val = sampleNearestData(intensityData, sampleX, sampleY, sampleZ);
        const normalized = stats.max > stats.min ? ((val - stats.min) / (stats.max - stats.min)) * 255 : 0;
        const adjusted = clamp((normalized - 128) * contrastFactor + 128 + brightnessOffset, 0, 255);

        const pixelIdx = (i + j * width) * 4;
        imageData.data[pixelIdx] = imageData.data[pixelIdx] * (1 - alpha) + adjusted * alpha;
        imageData.data[pixelIdx + 1] = imageData.data[pixelIdx + 1] * (1 - alpha) + adjusted * alpha;
        imageData.data[pixelIdx + 2] = imageData.data[pixelIdx + 2] * (1 - alpha) + adjusted * alpha;

        sampleX += plane.stepX[0];
        sampleY += plane.stepX[1];
        sampleZ += plane.stepX[2];
      }
      rowX += plane.stepY[0];
      rowY += plane.stepY[1];
      rowZ += plane.stepY[2];
    }
  });

  [...visibleSegmentationLayers].reverse().forEach((segmentationLayer) => {
    const segData = loadedVolumes.get(segmentationLayer.url);
    if (!segData) return;
    const alpha = segmentationLayer.opacity;
    const baseToSeg = multiplyAffine(segData.worldToVoxel, baseVData.voxelToWorld);
    const plane = computeSliceSamplingPlane(baseToSeg, axis, sliceIdx, height);
    const volLut = segmentationLayer.customLutUrl
      ? (customLuts.get(segmentationLayer.customLutUrl) ?? lut)
      : (segmentationLayer.lut === 'binary' ? binaryLut : lut);

    let rowX = plane.origin[0];
    let rowY = plane.origin[1];
    let rowZ = plane.origin[2];

    for (let j = 0; j < height; j++) {
      let sampleX = rowX;
      let sampleY = rowY;
      let sampleZ = rowZ;

      for (let i = 0; i < width; i++) {
        const val = sampleNearestData(segData, sampleX, sampleY, sampleZ);
        if (val > 0) {
          const pixelIdx = (i + j * width) * 4;
          const { rgb: [r, g, b], alpha: lutAlpha } = lookupLut(volLut, val);
          const effectiveAlpha = alpha * (lutAlpha / 255);
          if (effectiveAlpha > 0) {
            imageData.data[pixelIdx] = imageData.data[pixelIdx] * (1 - effectiveAlpha) + r * effectiveAlpha;
            imageData.data[pixelIdx + 1] = imageData.data[pixelIdx + 1] * (1 - effectiveAlpha) + g * effectiveAlpha;
            imageData.data[pixelIdx + 2] = imageData.data[pixelIdx + 2] * (1 - effectiveAlpha) + b * effectiveAlpha;
          }
        }
        sampleX += plane.stepX[0];
        sampleY += plane.stepX[1];
        sampleZ += plane.stepX[2];
      }
      rowX += plane.stepY[0];
      rowY += plane.stepY[1];
      rowZ += plane.stepY[2];
    }
  });
  ctx.putImageData(imageData, 0, 0);

  ctx.strokeStyle = '#262626';
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, width - 1, height - 1);

  ctx.strokeStyle = '#00ff00';
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (axis === 0) {
    const px = currentSlices.y;
    const py = height - 1 - currentSlices.z;
    ctx.moveTo(px, 0); ctx.lineTo(px, height); ctx.moveTo(0, py); ctx.lineTo(width, py);
  } else if (axis === 1) {
    const px = currentSlices.x;
    const py = height - 1 - currentSlices.z;
    ctx.moveTo(px, 0); ctx.lineTo(px, height); ctx.moveTo(0, py); ctx.lineTo(width, py);
  } else {
    const px = currentSlices.x;
    const py = height - 1 - currentSlices.y;
    ctx.moveTo(px, 0); ctx.lineTo(px, height); ctx.moveTo(0, py); ctx.lineTo(width, py);
  }
  ctx.stroke();
}
