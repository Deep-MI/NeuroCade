import type Niivue from '@niivue/niivue';

import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop.js';

export type NiivueOpacityUpdate = 'none' | 'updated';

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
  for (let targetIndex = 0; targetIndex < desired.length; targetIndex += 1) {
    const currentIndex = interop.volumes.indexOf(desired[targetIndex]);
    if (currentIndex !== targetIndex) {
      interop.model.moveVolume(currentIndex, targetIndex);
    }
  }
  return true;
}
