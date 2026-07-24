import type Niivue from '@niivue/niivue';
import type { ColorMap, NVImage, NVMesh } from '@niivue/niivue';

export type NiivueColorMap = ColorMap;
export type NiivueVolumeInterop = NVImage & {
  pixDims?: number[];
  getAffine?: () => number[][];
  getVolumeData?: (voxStart?: number[], voxEnd?: number[], dataType?: string) => [ArrayLike<number>, number[]];
  setVolumeData?: (voxStart?: number[], voxEnd?: number[], img?: unknown) => void;
};

export type NiivueMeshInterop = NVMesh & {
  id?: string;
  visible?: boolean;
  __surfaceDisplayKey?: string;
};

export interface MeshLoadOptions {
  url: string | File;
  name: string;
  opacity: number;
  color: [number, number, number, number];
  visible: boolean;
  shaderType?: string;
}

export interface SurfaceCompanionLayer {
  url: string | File;
  name: string;
  opacity: number;
  colormap: string;
  colormapNegative?: string;
}

export type NiivueInterop = Omit<Niivue, 'volumes' | 'meshes'> & {
  readonly volumes: NiivueVolumeInterop[];
  readonly meshes: NiivueMeshInterop[];
};

export function asNiivueInterop(nv: Niivue): NiivueInterop {
  return nv as NiivueInterop;
}
