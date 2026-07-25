import type Niivue from '@niivue/niivue';
import type { ColorMap, NVImage, NVMesh } from '@niivue/niivue';

export type NiivueColorMap = ColorMap;
export type NiivueVolumeInterop = NVImage;

export type NiivueMeshInterop = NVMesh & {
  id?: string;
  visible?: boolean;
  __surfaceDisplayKey?: string;
};

export type NiivueInterop = Omit<Niivue, 'volumes' | 'meshes'> & {
  readonly volumes: NiivueVolumeInterop[];
  readonly meshes: NiivueMeshInterop[];
};

export function asNiivueInterop(nv: Niivue): NiivueInterop {
  return nv as NiivueInterop;
}
