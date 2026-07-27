import type Niivue from '@niivue/niivue';

import { isSurfaceLayer, type Volume } from '../types.js';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop.js';
import type { WorldCoordinate } from './loadedVolumeDisplay.js';

type Matrix4 = [
  number, number, number, number,
  number, number, number, number,
  number, number, number, number,
  number, number, number, number,
];

export interface ReferenceGeometry {
  sourceId: string;
  dimsRAS: [number, number, number, number];
  matRAS: Matrix4;
  tex2mm: Matrix4;
  mm2tex: Matrix4;
  extentsMin: [number, number, number];
  extentsMax: [number, number, number];
  pivot3D: [number, number, number];
  furthestFromPivot: number;
}

function copyMatrix(matrix: ArrayLike<number> | null | undefined): Matrix4 | null {
  if (!matrix || matrix.length < 16) return null;
  const values = Array.from(matrix).slice(0, 16);
  if (!values.every(Number.isFinite)) return null;
  return values as Matrix4;
}

function copyDimensions(dimensions: number[] | undefined): ReferenceGeometry['dimsRAS'] | null {
  if (!dimensions || dimensions.length < 4) return null;
  const values = dimensions.slice(0, 4);
  if (!values.every((value) => Number.isFinite(value) && value > 0)) return null;
  return values as ReferenceGeometry['dimsRAS'];
}

function copyVector3(vector: ArrayLike<number> | null | undefined): [number, number, number] | null {
  if (!vector || vector.length < 3) return null;
  const values = Array.from(vector).slice(0, 3);
  if (!values.every(Number.isFinite)) return null;
  return values as [number, number, number];
}

function invertMatrix4(matrix: Matrix4): Matrix4 | null {
  const [
    a00, a01, a02, a03,
    a10, a11, a12, a13,
    a20, a21, a22, a23,
    a30, a31, a32, a33,
  ] = matrix;
  const b00 = a00 * a11 - a01 * a10;
  const b01 = a00 * a12 - a02 * a10;
  const b02 = a00 * a13 - a03 * a10;
  const b03 = a01 * a12 - a02 * a11;
  const b04 = a01 * a13 - a03 * a11;
  const b05 = a02 * a13 - a03 * a12;
  const b06 = a20 * a31 - a21 * a30;
  const b07 = a20 * a32 - a22 * a30;
  const b08 = a20 * a33 - a23 * a30;
  const b09 = a21 * a32 - a22 * a31;
  const b10 = a21 * a33 - a23 * a31;
  const b11 = a22 * a33 - a23 * a32;
  const determinant = (
    b00 * b11 - b01 * b10 + b02 * b09
    + b03 * b08 - b04 * b07 + b05 * b06
  );
  if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-12) return null;
  const inverseDeterminant = 1 / determinant;
  return [
    (a11 * b11 - a12 * b10 + a13 * b09) * inverseDeterminant,
    (a02 * b10 - a01 * b11 - a03 * b09) * inverseDeterminant,
    (a31 * b05 - a32 * b04 + a33 * b03) * inverseDeterminant,
    (a22 * b04 - a21 * b05 - a23 * b03) * inverseDeterminant,
    (a12 * b08 - a10 * b11 - a13 * b07) * inverseDeterminant,
    (a00 * b11 - a02 * b08 + a03 * b07) * inverseDeterminant,
    (a32 * b02 - a30 * b05 - a33 * b01) * inverseDeterminant,
    (a20 * b05 - a22 * b02 + a23 * b01) * inverseDeterminant,
    (a10 * b10 - a11 * b08 + a13 * b06) * inverseDeterminant,
    (a01 * b08 - a00 * b10 - a03 * b06) * inverseDeterminant,
    (a30 * b04 - a31 * b02 + a33 * b00) * inverseDeterminant,
    (a21 * b02 - a20 * b04 - a23 * b00) * inverseDeterminant,
    (a11 * b07 - a10 * b09 - a12 * b06) * inverseDeterminant,
    (a00 * b09 - a01 * b07 + a02 * b06) * inverseDeterminant,
    (a31 * b01 - a30 * b03 - a32 * b00) * inverseDeterminant,
    (a20 * b03 - a21 * b01 + a22 * b00) * inverseDeterminant,
  ];
}

function loadedMatchesSource(loaded: NiivueVolumeInterop, source: Volume): boolean {
  return loaded.id === source.id
    || loaded.url === source.url
    || loaded.name === source.filename
    || loaded.name === source.name;
}

export function selectReferenceVolumeSource(sources: Volume[]): Volume | null {
  const volumes = sources.filter((volume) => !isSurfaceLayer(volume));
  const intensities = volumes.filter((volume) => volume.type !== 'segmentation');
  const segmentations = volumes.filter((volume) => volume.type === 'segmentation');
  const canonicalOrig = intensities.find((volume) => (
    (volume.filename || volume.name).split('/').at(-1)?.toLowerCase() === 'orig.mgz'
  ));
  return canonicalOrig ?? intensities[0] ?? segmentations[0] ?? null;
}

export function referenceGeometryFromVolume(
  loaded: NiivueVolumeInterop,
  sourceId: string,
): ReferenceGeometry | null {
  const dimsRAS = copyDimensions(loaded.dimsRAS);
  const matRAS = copyMatrix(loaded.matRAS);
  const tex2mm = copyMatrix(loaded.frac2mm);
  const mm2tex = tex2mm ? invertMatrix4(tex2mm) : null;
  const extentsMin = copyVector3(loaded.extentsMin);
  const extentsMax = copyVector3(loaded.extentsMax);
  if (!dimsRAS || !matRAS || !tex2mm || !mm2tex || !extentsMin || !extentsMax) return null;
  const pivot3D = extentsMin.map((minimum, axis) => (
    (minimum + extentsMax[axis]) * 0.5
  )) as [number, number, number];
  const furthestFromPivot = Math.hypot(
    extentsMax[0] - pivot3D[0],
    extentsMax[1] - pivot3D[1],
    extentsMax[2] - pivot3D[2],
  );
  if (!Number.isFinite(furthestFromPivot) || furthestFromPivot <= 0) return null;
  return {
    sourceId,
    dimsRAS,
    matRAS,
    tex2mm,
    mm2tex,
    extentsMin,
    extentsMax,
    pivot3D,
    furthestFromPivot,
  };
}

/**
 * Capture the stable coordinate system without retaining voxel data. A cached
 * preferred intensity remains authoritative while it is hidden, even if a
 * segmentation with a different affine stays loaded.
 */
export function captureReferenceGeometry(
  nv: Niivue,
  sources: Volume[],
  current: ReferenceGeometry | null,
): ReferenceGeometry | null {
  const preferredSource = selectReferenceVolumeSource(sources);
  if (!preferredSource) return null;
  if (current?.sourceId === preferredSource.id) return current;

  const interop = asNiivueInterop(nv);
  const loaded = interop.volumes.find((candidate) => loadedMatchesSource(candidate, preferredSource));
  if (!loaded) return current;

  return referenceGeometryFromVolume(loaded, preferredSource.id) ?? current;
}

/**
 * Keep all panes framed by the canonical reference even when NiiVue changes
 * its base layer. With no loaded volume, restore its transforms as well so
 * surface-only interaction continues to use that reference coordinate system.
 */
export function applyReferenceGeometry(nv: Niivue, geometry: ReferenceGeometry | null): void {
  if (!geometry) return;
  nv.model.extentsMin = new Float32Array(geometry.extentsMin);
  nv.model.extentsMax = new Float32Array(geometry.extentsMax);
  nv.model.pivot3D = new Float32Array(geometry.pivot3D);
  nv.model.furthestFromPivot = geometry.furthestFromPivot;
  if (asNiivueInterop(nv).volumes.length === 0) {
    nv.model.tex2mm = new Float32Array(geometry.tex2mm);
    nv.model.mm2tex = new Float32Array(geometry.mm2tex);
  }
}

export function referenceVoxelToWorldFromGeometry(
  geometry: ReferenceGeometry,
  voxel: [number, number, number],
): WorldCoordinate {
  const matrix = geometry.matRAS;
  const [x, y, z] = voxel;
  return [
    matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
    matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
    matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
  ];
}

export function referenceWorldToVoxel(
  geometry: ReferenceGeometry,
  world: ArrayLike<number>,
): [number, number, number] | null {
  if (world.length < 3) return null;
  const matrix = geometry.matRAS;
  const a00 = matrix[0];
  const a01 = matrix[1];
  const a02 = matrix[2];
  const a10 = matrix[4];
  const a11 = matrix[5];
  const a12 = matrix[6];
  const a20 = matrix[8];
  const a21 = matrix[9];
  const a22 = matrix[10];
  const determinant = (
    a00 * (a11 * a22 - a12 * a21)
    - a01 * (a10 * a22 - a12 * a20)
    + a02 * (a10 * a21 - a11 * a20)
  );
  if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-12) return null;

  const inverseDeterminant = 1 / determinant;
  const x = Number(world[0]) - matrix[3];
  const y = Number(world[1]) - matrix[7];
  const z = Number(world[2]) - matrix[11];
  const voxel: [number, number, number] = [
    ((a11 * a22 - a12 * a21) * x + (a02 * a21 - a01 * a22) * y + (a01 * a12 - a02 * a11) * z) * inverseDeterminant,
    ((a12 * a20 - a10 * a22) * x + (a00 * a22 - a02 * a20) * y + (a02 * a10 - a00 * a12) * z) * inverseDeterminant,
    ((a10 * a21 - a11 * a20) * x + (a01 * a20 - a00 * a21) * y + (a00 * a11 - a01 * a10) * z) * inverseDeterminant,
  ];
  return voxel.every(Number.isFinite) ? voxel : null;
}

export function moveCrosshairInReferenceVox(
  nv: Niivue,
  geometry: ReferenceGeometry,
  delta: [number, number, number],
): boolean {
  const voxel = referenceWorldToVoxel(geometry, nv.getCrosshairPos());
  if (!voxel) return false;
  const next = voxel.map((value, axis) => (
    Math.max(0, Math.min(geometry.dimsRAS[axis + 1] - 1, Math.round(value) + delta[axis]))
  )) as [number, number, number];
  nv.setCrosshairPos(referenceVoxelToWorldFromGeometry(geometry, next));
  return true;
}
