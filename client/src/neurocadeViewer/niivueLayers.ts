import { Niivue, NVImage, NVMesh } from '@niivue/niivue';

import { isSurfaceLayer, type LayerType, type SurfaceLayer, type Volume } from '../types';
import type { LocationInfo } from '../types';
import { appFetchUrl } from '../utils/api';
import {
  asNiivueInterop,
  type NiivueInterop,
  type NiivueLabelLut,
  type NiivueMeshInterop,
  type NiivueVolumeInterop,
  type SurfaceCompanionLayer,
} from '../utils/niivueInterop';
import {
  applyBrightnessContrast,
  getBinarySegmentationLabelLut,
  resolveVolumeColormap,
  resolveVolumeLabelColormap,
} from '../utils/volumeColormap';
import { resolveSurfaceLayerColorMode, surfaceColor } from '../utils/surfaceColors';
import type { ViewerSliceType } from './viewerControls';
import { labelInfoFromLut, type LabelLookupResult } from './labelLookup';

// NIfTI intent code Niivue uses to route label maps through its atlas shader.
const LABEL_INTENT_CODE = 1002;
const NIFTI_INTENT_NONE = 0;
const RGBA32_DATATYPE_CODE = 2304;

interface ViewerTile {
  axCorSag: ViewerSliceType;
  leftTopWidthHeight: number[];
}

export type NiivueViewerInterop = NiivueInterop & {
  screenSlices?: ViewerTile[];
  scene?: {
    pan2Dxyzmm?: number[];
    clipPlaneDepthAziElevs?: number[][];
    renderAzimuth?: number;
    renderElevation?: number;
  };
  uiData?: {
    activeClipPlaneIndex?: number;
  };
  drawScene?: () => void;
  setClipPlane?: (depthAzimuthElevation: number[]) => void;
  setRenderAzimuthElevation?: (azimuth: number, elevation: number) => void;
  sliceScale?: () => { volScale: number[] };
};

interface NiivueLocationObject {
  mm?: number[];
}

interface SafeLocationNiivue {
  createOnLocationChange?: (axCorSag?: number) => void;
  frac2mm?: (frac: number[], mni?: number, isForceSliceMM?: boolean) => number[];
  frac2vox?: (frac: number[]) => number[];
  mousePos?: number[];
  scene?: {
    crosshairPos?: number[];
  };
  _emitEvent?: (name: string, detail: unknown) => void;
  onLocationChange: (location: unknown) => void;
}

export function layerType(volume: Volume): LayerType {
  return volume.type ?? 'intensity';
}

function isLabelVolume(volume: Volume): boolean {
  return volume.type === 'segmentation';
}

export function shouldUseVoxelExactLabelRendering(volume: Volume): boolean {
  return volume.type === 'segmentation';
}

function intentCodeForLabelVolume(volume: Volume): number {
  // Niivue's atlas shader softens label boundaries by averaging alpha with
  // neighbouring voxels. Segmentation overlays should display
  // categorical voxel values exactly, so keep the label LUT but avoid the atlas
  // intent for them.
  return shouldUseVoxelExactLabelRendering(volume) ? NIFTI_INTENT_NONE : LABEL_INTENT_CODE;
}

function loadedVolumeDims(loaded: NiivueVolumeInterop): [number, number, number] | null {
  const dims = loaded.dims ?? loaded.hdr?.dims;
  const x = Math.trunc(dims?.[1] ?? 0);
  const y = Math.trunc(dims?.[2] ?? 0);
  const z = Math.trunc(dims?.[3] ?? 0);
  return x > 0 && y > 0 && z > 0 ? [x, y, z] : null;
}

function fallbackLabelColor(label: number): [number, number, number, number] {
  const value = Math.abs(Math.trunc(label));
  return [
    (value * 53 + 97) % 256,
    (value * 97 + 61) % 256,
    (value * 193 + 29) % 256,
    255,
  ];
}

function labelColor(label: number, labelLut: NiivueLabelLut): [number, number, number, number] {
  if (label <= 0) return [0, 0, 0, 0];
  const min = labelLut.min ?? 0;
  const offset = (label - min) * 4;
  if (offset >= 0 && offset + 3 < labelLut.lut.length) {
    const alpha = labelLut.lut[offset + 3];
    if (alpha > 0) {
      return [
        labelLut.lut[offset],
        labelLut.lut[offset + 1],
        labelLut.lut[offset + 2],
        alpha,
      ];
    }
  }
  return fallbackLabelColor(label);
}

function voxelExactLabelKey(volume: Volume, rawData: ArrayLike<number>, labelLut: NiivueLabelLut): string {
  return `${volume.id}:${volume.type}:${rawData.length}:${labelLut.min ?? 0}:${labelLut.max ?? 0}:${labelLut.lut.length}`;
}

export function applyVoxelExactLabelRendering(loaded: NiivueVolumeInterop, volume: Volume, labelLut: NiivueLabelLut | null | undefined): void {
  if (!shouldUseVoxelExactLabelRendering(volume) || !labelLut) return;
  const dims = loadedVolumeDims(loaded);
  if (!dims) return;
  const rawData = loaded.__rawLabelData ?? loaded.img;
  if (!rawData) return;

  const key = voxelExactLabelKey(volume, rawData, labelLut);
  if (loaded.__voxelExactLabelKey === key && loaded.hdr?.datatypeCode === RGBA32_DATATYPE_CODE) return;

  const voxelCount = dims[0] * dims[1] * dims[2];
  const rgba = new Uint8Array(voxelCount * 4);
  const count = Math.min(voxelCount, rawData.length);
  for (let index = 0; index < count; index += 1) {
    const label = Math.round(Number(rawData[index]) || 0);
    const [red, green, blue, alpha] = labelColor(label, labelLut);
    const offset = index * 4;
    rgba[offset] = red;
    rgba[offset + 1] = green;
    rgba[offset + 2] = blue;
    rgba[offset + 3] = alpha;
  }

  loaded.__rawLabelData = rawData;
  loaded.__rawLabelDims = dims;
  loaded.__rawLabelColormap = labelLut;
  loaded.__voxelExactLabelKey = key;
  loaded.img = rgba;
  loaded.colormapLabel = null;
  if (loaded.hdr) {
    loaded.hdr.datatypeCode = RGBA32_DATATYPE_CODE;
    loaded.hdr.intent_code = NIFTI_INTENT_NONE;
    loaded.hdr.cal_min = 0;
    loaded.hdr.cal_max = 255;
  }
  loaded.cal_min = 0;
  loaded.cal_max = 255;
}

// Render priority across groups: intensity images are the anatomical underlay
// (rank 0, lower Niivue indices); segmentations are composited on top (rank 1,
// higher indices) so an opaque intensity can never hide a segmentation.
function volumeRenderRank(volume: Volume): number {
  return isLabelVolume(volume) ? 1 : 0;
}

// Desired Niivue volume order, bottom-to-top (ascending index = background →
// top overlay). The layer panel lists the top-most layer first, so within each
// group the list order is reversed here. Surfaces are meshes, handled elsewhere.
export function volumesInRenderOrder(sources: Volume[]): Volume[] {
  const nonSurface = sources.filter((volume) => !isSurfaceLayer(volume));
  const intensities = nonSurface.filter((volume) => volumeRenderRank(volume) === 0);
  const segmentations = nonSurface.filter((volume) => volumeRenderRank(volume) === 1);
  return [...intensities.reverse(), ...segmentations.reverse()];
}

// Reorders the already-loaded Niivue volumes to match volumesInRenderOrder
// (top-of-list on top, segmentations above intensities) without re-fetching.
export function enforceVolumeRenderOrder(nv: Niivue, sources: Volume[]) {
  const interop = asNiivueInterop(nv);
  const current = interop.volumes;
  if (current.length < 2) return;
  const orderIds = volumesInRenderOrder(sources).map((volume) => volume.id);
  const rankOf = (loaded: NiivueVolumeInterop) => {
    const position = loaded.id ? orderIds.indexOf(loaded.id) : -1;
    return position === -1 ? Number.MAX_SAFE_INTEGER : position;
  };
  const desired = current
    .map((loaded, index) => ({ loaded, index, rank: rankOf(loaded) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.loaded);
  if (desired.every((loaded, index) => loaded === current[index])) return;
  const orderable = nv as unknown as {
    volumes: NiivueVolumeInterop[];
    back: NiivueVolumeInterop | null;
    overlays: NiivueVolumeInterop[];
  };
  orderable.volumes = desired;
  orderable.back = desired[0] ?? null;
  orderable.overlays = desired.slice(1);
  nv.updateGLVolume();
}

export function clampOpacity(value: number | undefined, fallback = 0.75) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
  return Math.max(0, Math.min(1, value));
}

// Default opacity for a layer when none is set: segmentations are translucent
// overlays, everything else is opaque.
export function layerDefaultOpacity(volume: Volume): number {
  return isLabelVolume(volume) ? 0.55 : 1;
}

// Opacity to hand Niivue for a layer, honouring its visibility (hidden = 0).
export function effectiveLayerOpacity(volume: Volume): number {
  return volume.visible ? clampOpacity(volume.opacity, layerDefaultOpacity(volume)) : 0;
}

async function fetchArrayBuffer(url: string, signal: AbortSignal): Promise<ArrayBuffer> {
  const response = await appFetchUrl(url, { signal });
  if (!response.ok) {
    throw new Error(`Failed to load ${url}: ${response.status}`);
  }
  return await response.arrayBuffer();
}

type IdleWindow = typeof window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
};

// Resolves once the browser is idle (or after `timeout` ms as a safety net), so
// background work only proceeds when the main thread is free — and yields the
// event loop between items.
export function whenIdle(signal: AbortSignal, timeout = 2000): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const idleWindow = window as IdleWindow;
    if (typeof idleWindow.requestIdleCallback === 'function') {
      idleWindow.requestIdleCallback(() => resolve(), { timeout });
    } else {
      setTimeout(resolve, 150);
    }
  });
}

function filenameFromUrl(url: string, fallback: string): string {
  try {
    const pathname = new URL(url, window.location.origin).pathname;
    const filename = pathname.split('/').pop();
    if (filename?.includes('.')) {
      return decodeURIComponent(filename);
    }
    return fallback;
  } catch {
    return fallback;
  }
}

function surfaceRgba(surface: SurfaceLayer): [number, number, number, number] {
  const color = surfaceColor(surface);
  return [
    Math.round(color[0] * 255),
    Math.round(color[1] * 255),
    Math.round(color[2] * 255),
    255,
  ];
}

// Niivue's default tile sizing (calculateSliceDimensions) *fits* the slice into
// the tile, letterboxing non-square slices. This is the "cover" counterpart: it
// scales the slice to fill the tile's larger axis and overflow the other. Each
// pane is a single full-canvas slice, so the canvas framebuffer clips the
// overflow — no per-tile scissor needed.
function coverSliceDimensions(sliceType: number, volScale: number[], containerWidth: number, containerHeight: number): [number, number] {
  let xScale: number;
  let yScale: number;
  switch (sliceType) {
    case 0: xScale = volScale[0]; yScale = volScale[1]; break; // axial
    case 1: xScale = volScale[0]; yScale = volScale[2]; break; // coronal
    case 2: xScale = volScale[1]; yScale = volScale[2]; break; // sagittal
    default: return [containerWidth, containerHeight];
  }
  const aspectRatio = xScale / yScale;
  if (!Number.isFinite(aspectRatio) || aspectRatio <= 0 || containerWidth <= 0 || containerHeight <= 0) {
    return [containerWidth, containerHeight];
  }
  const containerAspect = containerWidth / containerHeight;
  return aspectRatio > containerAspect
    ? [containerHeight * aspectRatio, containerHeight]
    : [containerWidth, containerWidth / aspectRatio];
}

// Patches a single-plane instance so its slice *covers* the canvas (fills it
// without stretching; the framebuffer clips the overflow). Only the cover-dims
// override is needed here because every pane is one full-canvas slice — there
// are no neighbouring tiles to bleed into, so no scissor is required.
export function installCoverRendering(nv: Niivue) {
  const patch = nv as unknown as {
    calculateWidthHeight?: (sliceType: number, volScale: number[], containerWidth: number, containerHeight: number) => [number, number];
  };
  if (typeof patch.calculateWidthHeight !== 'function') return;
  patch.calculateWidthHeight = (sliceType, volScale, containerWidth, containerHeight) =>
    coverSliceDimensions(sliceType, volScale, containerWidth, containerHeight);
}

function isToFixedDigitsRangeError(error: unknown): boolean {
  return error instanceof RangeError && String(error.message).includes('toFixed() digits');
}

export function installSafeLocationChange(nv: Niivue) {
  const patch = nv as unknown as SafeLocationNiivue;
  if (typeof patch.createOnLocationChange !== 'function') return;
  const createOnLocationChange = patch.createOnLocationChange.bind(nv);

  patch.createOnLocationChange = (axCorSag = NaN) => {
    try {
      createOnLocationChange(axCorSag);
    } catch (error) {
      if (!isToFixedDigitsRangeError(error)) throw error;
      const frac = patch.scene?.crosshairPos;
      if (!frac) return;
      const mm = typeof patch.frac2mm === 'function'
        ? patch.frac2mm(frac, 0, true)
        : null;
      if (!mm?.every((value) => Number.isFinite(value))) return;

      const msg = {
        mm,
        axCorSag,
        vox: typeof patch.frac2vox === 'function' ? patch.frac2vox(frac) : [NaN, NaN, NaN],
        frac,
        xy: [patch.mousePos?.[0] ?? NaN, patch.mousePos?.[1] ?? NaN],
        values: [],
        string: `${mm[0]}×${mm[1]}×${mm[2]}`,
      };
      patch._emitEvent?.('locationChange', msg);
      patch.onLocationChange(msg);
    }
  };
}

async function addSurfaceCompanionLayer(nv: Niivue, mesh: NiivueMeshInterop, surface: SurfaceLayer, signal: AbortSignal) {
  const colorMode = resolveSurfaceLayerColorMode(surface);
  const companionUrl = colorMode === 'annotation' ? surface.annotationUrl : colorMode === 'curvature' ? surface.curvatureUrl : undefined;
  if (!companionUrl) return;

  const companionName = filenameFromUrl(
    companionUrl,
    colorMode === 'annotation' ? `${surface.filename}.annot` : `${surface.filename}.curv`,
  );
  const buffer = await fetchArrayBuffer(companionUrl, signal);
  const objectUrl = URL.createObjectURL(new Blob([buffer]));
  const layer: SurfaceCompanionLayer = colorMode === 'annotation'
    ? {
      url: objectUrl,
      name: companionName,
      opacity: 1,
      colormap: 'freesurfer',
    }
    : {
      url: objectUrl,
      name: companionName,
      opacity: 1,
      colormap: 'gray',
      colormapNegative: 'gray',
      useNegativeCmap: false,
    };

  try {
    await NVMesh.loadLayer(layer as never, mesh as never);
    const loadedLayer = mesh.layers?.at(-1) as { name?: string } | undefined;
    if (loadedLayer) loadedLayer.name = companionName;
    mesh.updateMesh?.(asNiivueInterop(nv).gl);
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function surfaceDisplayKey(surface: SurfaceLayer): string {
  const colorMode = resolveSurfaceLayerColorMode(surface);
  const companionUrl = colorMode === 'annotation' ? surface.annotationUrl : colorMode === 'curvature' ? surface.curvatureUrl : '';
  return `${colorMode}:${companionUrl ?? ''}`;
}

export function syncNiivueSurfaceDisplay(nv: Niivue, mesh: NiivueMeshInterop, surface: SurfaceLayer, signal: AbortSignal) {
  const keyedMesh = mesh as NiivueMeshInterop & { __surfaceDisplayKey?: string; rgba255?: [number, number, number, number] };
  const nextKey = surfaceDisplayKey(surface);
  if (keyedMesh.__surfaceDisplayKey === nextKey) return;
  keyedMesh.__surfaceDisplayKey = nextKey;
  keyedMesh.rgba255 = surfaceRgba(surface);
  keyedMesh.layers = [];
  const colorMode = resolveSurfaceLayerColorMode(surface);
  if (colorMode === 'solid') {
    mesh.updateMesh?.(asNiivueInterop(nv).gl);
    nv.updateGLVolume();
    return;
  }
  void addSurfaceCompanionLayer(nv, mesh, surface, signal)
    .then(() => nv.updateGLVolume())
    .catch((error) => {
      if (!signal.aborted) {
        console.warn(`[NiivuePane] Could not update surface coloring for ${surface.name}:`, error);
      }
    });
}

export async function addNiivueVolumeLayer(nv: Niivue, volume: Volume, signal: AbortSignal) {
  const filename = volume.filename || volume.name;
  const buffer = await fetchArrayBuffer(volume.url, signal);
  const imageOptions: Parameters<Niivue['addVolumeFromUrl']>[0] = {
    url: filename,
    name: filename,
    buffer,
    colormap: resolveVolumeColormap(volume),
    colorbarVisible: false,
    opacity: effectiveLayerOpacity(volume),
  };
  const loadedImage = await NVImage.loadFromUrl(imageOptions);
  const loaded = loadedImage as NiivueVolumeInterop;
  const colormapLabel = await resolveVolumeLabelColormap(volume, loaded);
  if (loaded) {
    loaded.id = volume.id;
    loaded.name = volume.name || volume.filename;
    loaded.url = volume.url;
    const labelLut = colormapLabel ?? (isLabelVolume(volume) ? getBinarySegmentationLabelLut() : null);
    if (shouldUseVoxelExactLabelRendering(volume)) {
      applyVoxelExactLabelRendering(loaded, volume, labelLut);
    } else {
      loaded.colormapLabel = labelLut;
    }
    loaded.colorbarVisible = false;
    if (isLabelVolume(volume) && !shouldUseVoxelExactLabelRendering(volume) && loaded.hdr) {
      loaded.hdr.intent_code = intentCodeForLabelVolume(volume);
    }
    if (volume.type === undefined || volume.type === 'intensity') {
      applyBrightnessContrast(loaded, volume);
    }
  }
  nv.addVolume(loadedImage);
}

export async function addNiivueSurfaceLayer(nv: Niivue, surface: SurfaceLayer, signal: AbortSignal, meshShader?: string) {
  const filename = surface.filename || surface.name;
  const buffer = await fetchArrayBuffer(surface.url, signal);
  const meshOptions = [{
    url: filename,
    name: filename,
    buffer,
    opacity: effectiveLayerOpacity(surface),
    rgba255: surfaceRgba(surface),
    visible: surface.visible,
    meshShaderIndex: 0,
  }];
  const nvInterop = asNiivueInterop(nv);
  if (typeof nvInterop.addMeshesFromUrl === 'function') {
    await nvInterop.addMeshesFromUrl(meshOptions);
  } else if (typeof nvInterop.loadMeshes === 'function') {
    await nvInterop.loadMeshes(meshOptions);
  }
  const mesh = (nvInterop.meshes ?? []).find((item) => item.name === filename);
  if (mesh) {
    mesh.id = surface.id;
    // Per-pane shader: 2D planes use 'Crosscut' (cross-section outline); the 3D
    // pane is left on Niivue's default shader to show the full shaded mesh.
    if (meshShader) nv.setMeshShader(surface.id, meshShader);
    syncNiivueSurfaceDisplay(nv, mesh, surface, signal);
  }
}

function getCurrentLabelInfo(mm: number[], loadedVolumes: NiivueVolumeInterop[], sourceVolumes: Volume[]): LabelLookupResult {
  const visibleSegmentationIds = sourceVolumes
    .filter((volume) => volume.visible && isLabelVolume(volume))
    .map((volume) => volume.id);

  const labelVolumes = visibleSegmentationIds
    .map((id) => loadedVolumes.find((volume) => volume.id === id))
    .filter((volume): volume is NiivueVolumeInterop => Boolean(volume?.getValue && volume.mm2vox));

  for (const labelVolume of labelVolumes) {
    const labelVoxel = labelVolume.mm2vox?.(mm);
    if (!labelVoxel) continue;
    const x = Math.round(labelVoxel[0] ?? 0);
    const y = Math.round(labelVoxel[1] ?? 0);
    const z = Math.round(labelVoxel[2] ?? 0);
    const dims = labelVolume.__rawLabelDims;
    const rawLabelData = labelVolume.__rawLabelData;
    const rawIndex = dims && rawLabelData && x >= 0 && y >= 0 && z >= 0 && x < dims[0] && y < dims[1] && z < dims[2]
      ? x + y * dims[0] + z * dims[0] * dims[1]
      : -1;
    const labelIndex = Math.round(Number(
      rawIndex >= 0
        ? rawLabelData?.[rawIndex]
        : labelVolume.getValue?.(x, y, z, labelVolume.frame4D),
    ) || 0);
    if (labelIndex <= 0) continue;
    return labelInfoFromLut(labelIndex, labelVolume.__rawLabelColormap ?? labelVolume.colormapLabel);
  }

  return { index: 0, name: 'Background' };
}

export function locationFromNiivue(locationObject: unknown, nv: Niivue, sourceVolumes: Volume[]): LocationInfo | null {
  const location = locationObject as NiivueLocationObject | null;
  if (!location?.mm) return null;
  const loadedVolumes = asNiivueInterop(nv).volumes;
  if (loadedVolumes.length === 0) return null;
  const firstVolume = loadedVolumes[0];
  if (!firstVolume?.mm2vox) return null;
  const voxel = firstVolume.mm2vox(location.mm);
  const vox: [number, number, number] = [
    Math.round(voxel[0] ?? 0),
    Math.round(voxel[1] ?? 0),
    Math.round(voxel[2] ?? 0),
  ];

  const label = getCurrentLabelInfo(location.mm, loadedVolumes, sourceVolumes);
  return {
    vox,
    labelIndex: label.index,
    labelName: label.name,
    labelColor: label.color,
  };
}

// Stable key over layer sources and visibility — used to trigger reconcile when
// a layer must be loaded or removed without reacting to opacity/order changes.
export function sourceKeyOf(volumes: Volume[]): string {
  return volumes.map((volume) => [
    volume.id,
    volume.url,
    volume.filename,
    volume.type ?? 'intensity',
    volume.visible ? 'visible' : 'hidden',
    isSurfaceLayer(volume) ? `${volume.curvatureUrl ?? ''}:${volume.annotationUrl ?? ''}` : '',
  ].join(':')).sort().join('|');
}
