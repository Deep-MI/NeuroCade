import { cmapper, type Niivue } from '@niivue/niivue';

export type NiivueColorMap = Parameters<typeof cmapper.makeLabelLut>[0];
export type NiivueLabelLut = ReturnType<typeof cmapper.makeLabelLut>;

export interface NiivueVolumeInterop {
  id?: string;
  name?: string;
  url?: string;
  opacity?: number;
  colormap?: string;
  colormapLabel?: NiivueLabelLut | null;
  colorbarVisible?: boolean;
  frame4D?: number;
  global_min?: number;
  global_max?: number;
  cal_min?: number;
  cal_max?: number;
  dims?: number[];
  dimsRAS?: number[];
  pixDims?: number[];
  pixDimsRAS?: number[];
  matRAS?: number[] | number[][];
  extentsMinOrtho?: number[];
  extentsMaxOrtho?: number[];
  mm2ortho?: number[] | number[][];
  img?: ArrayLike<number>;
  __rawLabelData?: ArrayLike<number>;
  __rawLabelDims?: [number, number, number];
  __rawLabelColormap?: NiivueLabelLut | null;
  __voxelExactLabelKey?: string;
  hdr?: {
    datatypeCode?: number;
    intent_code?: number;
    cal_max?: number;
    cal_min?: number;
    dims?: number[];
    pixDims?: number[];
    affine?: number[][];
  };
  mm2vox?: (mm: number[]) => number[];
  getValue?: (i: number, j: number, k: number, frame?: number) => unknown;
  getAffine?: () => number[][];
  getVolumeData?: (voxStart?: number[], voxEnd?: number[], dataType?: string) => [ArrayLike<number>, number[]];
  setVolumeData?: (voxStart?: number[], voxEnd?: number[], img?: unknown) => void;
}

export interface NiivueDrawingInterop {
  drawBitmap?: Uint8Array | null;
  drawFillOverwrites?: boolean;
  drawUndoBitmaps?: Uint8Array[];
  currentDrawUndoBitmap?: number;
  drawOpacity?: number;
  onDrawingChanged?: (action: string) => void;
  onDrawingEnabled?: (enabled: boolean) => void;
  setDrawingEnabled?: (enabled: boolean) => void;
  setPenValue?: (penValue: number, isFilledPen?: boolean) => void;
  setDrawOpacity?: (opacity: number) => void;
  setDrawColormap?: (name: string) => void;
  drawClearAllUndoBitmaps?: () => void;
  refreshDrawing?: (isForceRedraw?: boolean, useClickToSegmentBitmap?: boolean) => void;
  closeDrawing?: () => void;
  loadDrawing?: (drawingBitmap: unknown) => boolean;
  saveImage?: (options: { filename?: string; isSaveDrawing?: boolean; volumeByIndex?: number }) => Promise<boolean | Uint8Array>;
}

export interface NiivueMeshInterop {
  id?: string;
  name?: string;
  opacity?: number;
  visible?: boolean;
  pts?: Float32Array;
  furthestVertexFromOrigin?: number;
  layers?: unknown[];
  __originalPts?: Float32Array;
  __surfaceReferenceAffine?: number[][];
  __surfaceTransformKey?: string;
  updateMesh?: (gl: WebGL2RenderingContext | null | undefined) => void;
}

export interface MeshLoadOptions {
  url: string;
  name: string;
  opacity: number;
  rgba255: [number, number, number, number];
  visible: boolean;
  meshShaderIndex: number;
}

export interface SurfaceCompanionLayer {
  url: string;
  name: string;
  opacity: number;
  colormap: string;
  colormapNegative?: string;
  useNegativeCmap?: boolean;
}

export interface NiivueInterop extends NiivueDrawingInterop {
  gl?: WebGL2RenderingContext | null;
  opts: Niivue['opts'] & { multiplanarShowRender?: number; multiplanarLayout?: number; meshThicknessOn2D?: number; isSliceMM?: boolean };
  volumes: NiivueVolumeInterop[];
  back?: NiivueVolumeInterop | null;
  meshes?: NiivueMeshInterop[];
  addMeshesFromUrl?: (meshes: MeshLoadOptions[]) => Promise<void>;
  loadMeshes?: (meshes: MeshLoadOptions[]) => Promise<void>;
  loadMatCapTexture?: (url: string) => Promise<WebGLTexture | null>;
  moveCrosshairInVox?: (x: number, y: number, z: number) => void;
  setCrosshairPosition?: (coordinate: [number, number, number]) => void;
  setScale?: (scale: number) => void;
  resetBriCon?: () => void;
  updateGLVolume?: () => void;
}

export function asNiivueInterop(nv: Niivue): NiivueInterop {
  return nv as unknown as NiivueInterop;
}
