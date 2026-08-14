import type Niivue from '@niivue/niivue';

import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop.js';

type NiivueOpacityUpdate = 'none' | 'updated';
type WorldCoordinate = [number, number, number];

function isFiniteCoordinate(value: unknown): value is WorldCoordinate {
  return Array.isArray(value)
    && value.length >= 3
    && value.slice(0, 3).every((component) => typeof component === 'number' && Number.isFinite(component));
}

export function getCrosshairWorld(nv: Niivue): WorldCoordinate | null {
  if (typeof nv.getCrosshairPos !== 'function') return null;
  const position = nv.getCrosshairPos();
  return isFiniteCoordinate(position) ? [position[0], position[1], position[2]] : null;
}

export function restoreCrosshairWorld(nv: Niivue, position: WorldCoordinate | null): void {
  if (!position || typeof nv.setCrosshairPos !== 'function') return;
  nv.setCrosshairPos(position);
}

function coordinateVolume(nv: Niivue, sourceId?: string | null): NiivueVolumeInterop | undefined {
  const volumes = asNiivueInterop(nv).volumes;
  if (sourceId) {
    const source = volumes.find((volume) => volume.id === sourceId);
    if (source) return source;
  }
  const fixedReference = volumes[0];
  if (fixedReference?.__neurocadeFixedReference) {
    const source = volumes.find((volume) => volume.id === fixedReference.__neurocadeCoordinateSourceId);
    if (source) return source;
  }
  return fixedReference;
}

// NiiVue's setCrosshairPos API accepts world millimetres, while assistant
// cursor commands use RAS voxel coordinates from the current base volume.
export function referenceVoxelToWorld(
  nv: Niivue,
  voxel: [number, number, number],
  sourceId?: string | null,
): WorldCoordinate | null {
  const matrix = coordinateVolume(nv, sourceId)?.matRAS;
  if (!matrix || matrix.length < 12) return null;
  const [x, y, z] = voxel;
  const position: WorldCoordinate = [
    matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
    matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
    matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
  ];
  return isFiniteCoordinate(position) ? position : null;
}

export function referenceWorldToVoxel(
  nv: Niivue,
  world: ArrayLike<number>,
  sourceId?: string | null,
): [number, number, number] | null {
  if (world.length < 3) return null;
  const matrix = coordinateVolume(nv, sourceId)?.matRAS;
  if (!matrix || matrix.length < 12) return null;
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
  sourceId: string | null | undefined,
  delta: [number, number, number],
): boolean {
  const loaded = coordinateVolume(nv, sourceId);
  const current = referenceWorldToVoxel(nv, getCrosshairWorld(nv) ?? [], sourceId);
  if (!loaded?.dimsRAS || !current) return false;
  const next = current.map((value, axis) => (
    Math.max(0, Math.min((loaded.dimsRAS?.[axis + 1] ?? 1) - 1, Math.round(value) + delta[axis]))
  )) as [number, number, number];
  const world = referenceVoxelToWorld(nv, next, sourceId);
  if (!world) return false;
  nv.setCrosshairPos(world);
  return true;
}

export function setLoadedVolumeOpacity(nv: Niivue, loaded: NiivueVolumeInterop, opacity: number): NiivueOpacityUpdate {
  const nextOpacity = Math.max(0, Math.min(1, opacity));
  if (loaded.opacity === nextOpacity) return 'none';
  const index = asNiivueInterop(nv).volumes.indexOf(loaded);
  if (index < 0) return 'none';
  loaded.opacity = nextOpacity;
  return 'updated';
}

export function syncLoadedVolumeOpacities(
  nv: Niivue,
  opacityById: ReadonlyMap<string, number>,
): boolean {
  let changed = false;
  for (const loaded of asNiivueInterop(nv).volumes) {
    const opacity = loaded.id ? opacityById.get(loaded.id) : undefined;
    if (opacity === undefined) continue;
    if (setLoadedVolumeOpacity(nv, loaded, opacity) === 'updated') changed = true;
  }
  return changed;
}

export function reorderLoadedVolumes(nv: Niivue, desired: NiivueVolumeInterop[]): boolean {
  const interop = asNiivueInterop(nv);
  if (desired.length !== interop.volumes.length) return false;
  if (desired.every((loaded, index) => loaded === interop.volumes[index])) return false;
  const crosshairWorld = getCrosshairWorld(nv);
  for (let targetIndex = 0; targetIndex < desired.length; targetIndex += 1) {
    const currentIndex = interop.volumes.indexOf(desired[targetIndex]);
    if (currentIndex !== targetIndex) {
      interop.model.moveVolume(currentIndex, targetIndex);
    }
  }
  restoreCrosshairWorld(nv, crosshairWorld);
  return true;
}
