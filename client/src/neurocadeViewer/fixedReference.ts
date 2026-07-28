import type { NiivueVolumeInterop } from '../utils/niivueInterop.js';

export const FIXED_REFERENCE_ID = '__neurocade_reference_grid__';

const NIFTI_HEADER_SIZE = 352;
const MAX_REFERENCE_AXIS = 512;
const MAX_REFERENCE_VOXELS = 64 * 1024 * 1024;

export interface FixedReferenceGrid {
  buffer: ArrayBuffer;
  dimensions: [number, number, number];
  spacing: [number, number, number];
  affine: [
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number],
  ];
}

function finiteBounds(
  volume: NiivueVolumeInterop,
): { minimum: [number, number, number]; maximum: [number, number, number] } | null {
  const minimum = Array.from(volume.extentsMin ?? []).slice(0, 3).map(Number);
  const maximum = Array.from(volume.extentsMax ?? []).slice(0, 3).map(Number);
  if (
    minimum.length !== 3
    || maximum.length !== 3
    || !minimum.every(Number.isFinite)
    || !maximum.every(Number.isFinite)
    || maximum.some((value, axis) => value <= minimum[axis])
  ) {
    return null;
  }
  return {
    minimum: minimum as [number, number, number],
    maximum: maximum as [number, number, number],
  };
}

function fitGridToLimits(
  extents: [number, number, number],
  requestedSpacing: [number, number, number],
): { dimensions: [number, number, number]; spacing: [number, number, number] } {
  let scale = 1;
  const dimensionsForScale = (factor: number) => extents.map((extent, axis) => (
    Math.max(2, Math.ceil(extent / (requestedSpacing[axis] * factor)))
  )) as [number, number, number];
  let dimensions = dimensionsForScale(scale);
  const voxelCount = () => dimensions[0] * dimensions[1] * dimensions[2];

  scale = Math.max(
    scale,
    dimensions[0] / MAX_REFERENCE_AXIS,
    dimensions[1] / MAX_REFERENCE_AXIS,
    dimensions[2] / MAX_REFERENCE_AXIS,
    Math.cbrt(voxelCount() / MAX_REFERENCE_VOXELS),
  );
  dimensions = dimensionsForScale(scale);
  while (
    dimensions.some((dimension) => dimension > MAX_REFERENCE_AXIS)
    || voxelCount() > MAX_REFERENCE_VOXELS
  ) {
    scale *= 1.01;
    dimensions = dimensionsForScale(scale);
  }

  const spacing = requestedSpacing.map((value) => value * scale) as [number, number, number];
  return { dimensions, spacing };
}

function voxelSpacing(volume: NiivueVolumeInterop): [number, number, number] | null {
  const pixDims = Array.from(volume.pixDimsRAS ?? []).slice(1, 4).map((value) => Math.abs(Number(value)));
  if (pixDims.length === 3 && pixDims.every((value) => Number.isFinite(value) && value > 0)) {
    return pixDims as [number, number, number];
  }

  const matrix = Array.from(volume.matRAS ?? []).map(Number);
  if (matrix.length < 12) return null;
  const spacing = [0, 1, 2].map((column) => Math.hypot(
    matrix[column],
    matrix[4 + column],
    matrix[8 + column],
  )) as [number, number, number];
  return spacing.every((value) => Number.isFinite(value) && value > 0) ? spacing : null;
}

export function createFixedReferenceGrid(
  volumes: NiivueVolumeInterop[],
  resolutionSource: NiivueVolumeInterop = volumes[0],
): FixedReferenceGrid {
  const bounds = volumes
    .map(finiteBounds)
    .filter((value): value is NonNullable<typeof value> => value !== null);
  if (bounds.length === 0) {
    throw new Error('Cannot create a reference grid without finite volume bounds.');
  }

  const minimum = [0, 1, 2].map((axis) => (
    Math.min(...bounds.map((bound) => bound.minimum[axis]))
  )) as [number, number, number];
  const maximum = [0, 1, 2].map((axis) => (
    Math.max(...bounds.map((bound) => bound.maximum[axis]))
  )) as [number, number, number];
  const extents = maximum.map((value, axis) => value - minimum[axis]) as [number, number, number];
  const requestedSpacing = voxelSpacing(resolutionSource);
  if (!requestedSpacing) {
    throw new Error('Cannot create a reference grid without finite voxel spacing.');
  }
  const { dimensions, spacing } = fitGridToLimits(extents, requestedSpacing);
  const affine = [0, 1, 2].map((axis) => {
    const row = [0, 0, 0, minimum[axis] + spacing[axis] * 0.5];
    row[axis] = spacing[axis];
    return row;
  }) as FixedReferenceGrid['affine'];

  const voxelCount = dimensions[0] * dimensions[1] * dimensions[2];
  const buffer = new ArrayBuffer(NIFTI_HEADER_SIZE + voxelCount);
  const header = new DataView(buffer);
  header.setInt32(0, 348, true);
  header.setInt16(40, 3, true);
  dimensions.forEach((dimension, axis) => header.setInt16(42 + axis * 2, dimension, true));
  header.setInt16(48, 1, true);
  header.setInt16(70, 2, true);
  header.setInt16(72, 8, true);
  header.setFloat32(76, 1, true);
  spacing.forEach((value, axis) => header.setFloat32(80 + axis * 4, value, true));
  header.setFloat32(108, NIFTI_HEADER_SIZE, true);
  header.setFloat32(112, 1, true);
  header.setUint8(123, 2);
  header.setInt16(254, 1, true);
  affine.forEach((row, rowIndex) => {
    row.forEach((value, columnIndex) => {
      header.setFloat32(280 + (rowIndex * 4 + columnIndex) * 4, value, true);
    });
  });
  new Uint8Array(buffer, 344, 4).set([110, 43, 49, 0]);

  return { buffer, dimensions, spacing, affine };
}
