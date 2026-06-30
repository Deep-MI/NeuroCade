import type { Niivue } from '@niivue/niivue';

import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop.js';

export type NiivueOpacityUpdate = 'none' | 'mutated';

export function setLoadedVolumeOpacity(nv: Niivue, loaded: NiivueVolumeInterop, opacity: number): NiivueOpacityUpdate {
  const nextOpacity = Math.max(0, Math.min(1, opacity));
  if (loaded.opacity === nextOpacity) return 'none';
  const interop = asNiivueInterop(nv);
  let index = interop.volumes.indexOf(loaded);
  if (index < 0 && loaded.id && typeof interop.getVolumeIndexByID === 'function') {
    index = interop.getVolumeIndexByID(loaded.id);
  }
  if (index < 0 || !interop.volumes[index]) return 'none';
  interop.volumes[index].opacity = nextOpacity;
  loaded.opacity = nextOpacity;
  return 'mutated';
}

export function reorderLoadedVolumes(nv: Niivue, desired: NiivueVolumeInterop[]): boolean {
  const interop = asNiivueInterop(nv);
  if (desired.length !== interop.volumes.length) return false;
  if (desired.every((loaded, index) => loaded === interop.volumes[index])) return false;

  const orderable = nv as unknown as {
    volumes: NiivueVolumeInterop[];
    back: NiivueVolumeInterop | null;
    overlays: NiivueVolumeInterop[];
  };
  orderable.volumes = desired;
  orderable.back = desired[0] ?? null;
  orderable.overlays = desired.slice(1);
  return true;
}
