import type Niivue from '@niivue/niivue';

import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop.js';
import {
  referenceVoxelToWorldFromGeometry,
  type ReferenceGeometry,
} from './referenceGeometry.js';

export type NiivueOpacityUpdate = 'none' | 'updated';
export type WorldCoordinate = [number, number, number];

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

// NiiVue's setCrosshairPos API accepts world millimetres, while assistant
// cursor commands use RAS voxel coordinates from the current base volume.
export function referenceVoxelToWorld(
  nv: Niivue,
  voxel: [number, number, number],
  geometry?: ReferenceGeometry | null,
): WorldCoordinate | null {
  if (geometry) return referenceVoxelToWorldFromGeometry(geometry, voxel);
  const matrix = asNiivueInterop(nv).volumes[0]?.matRAS;
  if (!matrix || matrix.length < 12) return null;
  const [x, y, z] = voxel;
  const position: WorldCoordinate = [
    matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
    matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
    matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
  ];
  return isFiniteCoordinate(position) ? position : null;
}

export function setLoadedVolumeOpacity(nv: Niivue, loaded: NiivueVolumeInterop, opacity: number): NiivueOpacityUpdate {
  const nextOpacity = Math.max(0, Math.min(1, opacity));
  if (loaded.opacity === nextOpacity) return 'none';
  const index = asNiivueInterop(nv).volumes.indexOf(loaded);
  if (index < 0) return 'none';
  loaded.opacity = nextOpacity;
  loaded.isDirty = true;
  return 'updated';
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
