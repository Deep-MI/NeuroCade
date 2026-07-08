import { Niivue, NVImage, NVMesh } from '@niivue/niivue';

import { isSurfaceLayer, type LayerType, type SurfaceLayer, type Volume } from '../types';
import type { LocationInfo } from '../types';
import { appFetchUrl } from '../utils/api';
import {
  asNiivueInterop,
  type NiivueInterop,
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
import { reorderLoadedVolumes, setLoadedVolumeOpacity } from './loadedVolumeDisplay';
import { applySegmentationRgbaRendering } from './segmentationRgba';

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

function isSegmentationVolume(volume: Volume): boolean {
  return volume.type === 'segmentation';
}

// Desired Niivue volume order, bottom-to-top (ascending index = background →
// top overlay). Intensity images remain anatomical underlays; segmentations
// remain overlays. Within each group, the layer panel/source array is
// top-to-bottom, so reverse the group directly. Surfaces are meshes, handled
// elsewhere.
export function volumesInRenderOrder(sources: Volume[]): Volume[] {
  const nonSurface = sources.filter((volume) => !isSurfaceLayer(volume));
  const intensities = nonSurface.filter((volume) => volume.type !== 'segmentation');
  const segmentations = nonSurface.filter((volume) => volume.type === 'segmentation');
  return [...intensities.reverse(), ...segmentations.reverse()];
}

// Reorders the already-loaded Niivue volumes to match volumesInRenderOrder
// without re-fetching. The layer panel is user-facing top-to-bottom, while
// NiiVue renders the first volume as the bottom/reference layer.
export function enforceVolumeRenderOrder(nv: Niivue, sources: Volume[]): boolean {
  const interop = asNiivueInterop(nv);
  const current = interop.volumes;
  if (current.length < 2) return false;
  const orderIds = volumesInRenderOrder(sources).map((volume) => volume.id);
  const rankOf = (loaded: NiivueVolumeInterop) => {
    const position = loaded.id ? orderIds.indexOf(loaded.id) : -1;
    return position === -1 ? Number.MAX_SAFE_INTEGER : position;
  };
  const desired = current
    .map((loaded, index) => ({ loaded, index, rank: rankOf(loaded) }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.loaded);
  if (desired.every((loaded, index) => loaded === current[index])) return false;
  return reorderLoadedVolumes(nv, desired);
}

export function clampOpacity(value: number | undefined, fallback = 0.75) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
  return Math.max(0, Math.min(1, value));
}

// Default opacity for a layer when none is set: segmentations are translucent
// overlays, everything else is opaque.
export function layerDefaultOpacity(volume: Volume): number {
  return isSegmentationVolume(volume) ? 0.55 : 1;
}

// Opacity to hand Niivue for a layer, honouring its visibility (hidden = 0).
export function effectiveLayerOpacity(volume: Volume): number {
  return volume.visible ? clampOpacity(volume.opacity, layerDefaultOpacity(volume)) : 0;
}

export const setNiivueVolumeOpacity = setLoadedVolumeOpacity;

const arrayBufferCache = new Map<string, Promise<ArrayBuffer>>();

export async function fetchCachedArrayBuffer(url: string, signal: AbortSignal): Promise<ArrayBuffer> {
  if (signal.aborted) {
    throw new DOMException('Artifact fetch aborted', 'AbortError');
  }
  let cached = arrayBufferCache.get(url);
  if (!cached) {
    cached = appFetchUrl(url)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Failed to load ${url}: ${response.status}`);
        }
        return await response.arrayBuffer();
      })
      .catch((error) => {
        arrayBufferCache.delete(url);
        throw error;
      });
    arrayBufferCache.set(url, cached);
  }
  const buffer = await cached;
  if (signal.aborted) {
    throw new DOMException('Artifact fetch aborted', 'AbortError');
  }
  return buffer.slice(0);
}

export function clearNiivueLayerBufferCache(): void {
  arrayBufferCache.clear();
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

// NiiVue 0.69 fills top-level single-slice canvases better than 0.68, but it
// still does not expose an explicit cover/contain/fill option for custom
// per-pane canvases. This is the "cover" counterpart to calculateSliceDimensions:
// scale the slice to fill the larger axis and let the canvas clip overflow.
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

// Keep this patch narrowly scoped to our single-plane instances. Re-check with
// screenshots before removing it after future NiiVue layout changes.
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

function isLocationConversionError(error: unknown): boolean {
  if (isToFixedDigitsRangeError(error)) return true;
  return error instanceof TypeError && String(error.message).includes("Cannot read properties of undefined (reading '1')");
}

function tryFrac2mm(patch: SafeLocationNiivue, frac: number[]): number[] | null {
  try {
    return typeof patch.frac2mm === 'function'
      ? patch.frac2mm(frac, 0, true)
      : null;
  } catch {
    return null;
  }
}

function tryFrac2vox(patch: SafeLocationNiivue, frac: number[]): number[] {
  try {
    return typeof patch.frac2vox === 'function' ? patch.frac2vox(frac) : [NaN, NaN, NaN];
  } catch {
    return [NaN, NaN, NaN];
  }
}

export function installSafeLocationChange(nv: Niivue) {
  const patch = nv as unknown as SafeLocationNiivue;
  if (typeof patch.createOnLocationChange !== 'function') return;
  const createOnLocationChange = patch.createOnLocationChange.bind(nv);

  patch.createOnLocationChange = (axCorSag = NaN) => {
    try {
      createOnLocationChange(axCorSag);
    } catch (error) {
      if (!isLocationConversionError(error)) throw error;
      const frac = patch.scene?.crosshairPos;
      if (!frac) return;
      const mm = tryFrac2mm(patch, frac);
      if (!mm?.every((value) => Number.isFinite(value))) return;

      const msg = {
        mm,
        axCorSag,
        vox: tryFrac2vox(patch, frac),
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
  const buffer = await fetchCachedArrayBuffer(companionUrl, signal);
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

export function surfaceDisplayKey(surface: SurfaceLayer): string {
  const colorMode = resolveSurfaceLayerColorMode(surface);
  const companionUrl = colorMode === 'annotation' ? surface.annotationUrl : colorMode === 'curvature' ? surface.curvatureUrl : '';
  return `${colorMode}:${companionUrl ?? ''}`;
}

export async function syncNiivueSurfaceDisplay(nv: Niivue, mesh: NiivueMeshInterop, surface: SurfaceLayer, signal: AbortSignal): Promise<void> {
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
  try {
    await addSurfaceCompanionLayer(nv, mesh, surface, signal);
    nv.updateGLVolume();
  } catch (error) {
    if (!signal.aborted) {
      console.warn(`[NiivuePane] Could not update surface coloring for ${surface.name}:`, error);
    }
  }
}

export async function addNiivueVolumeLayer(nv: Niivue, volume: Volume, signal: AbortSignal) {
  const filename = volume.filename || volume.name;
  const buffer = await fetchCachedArrayBuffer(volume.url, signal);
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
    const labelLut = colormapLabel ?? (isSegmentationVolume(volume) ? getBinarySegmentationLabelLut() : null);
    applySegmentationRgbaRendering(loaded, volume, labelLut);
    loaded.colorbarVisible = false;
    if (volume.type === undefined || volume.type === 'intensity') {
      applyBrightnessContrast(loaded, volume);
    }
  }
  nv.addVolume(loadedImage);
}

export async function addNiivueSurfaceLayer(nv: Niivue, surface: SurfaceLayer, signal: AbortSignal, meshShader?: string) {
  const filename = surface.filename || surface.name;
  const buffer = await fetchCachedArrayBuffer(surface.url, signal);
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
    await syncNiivueSurfaceDisplay(nv, mesh, surface, signal);
  }
}

function getCurrentLabelInfo(mm: number[], loadedVolumes: NiivueVolumeInterop[], sourceVolumes: Volume[]): LabelLookupResult {
  const visibleSegmentationIds = sourceVolumes
    .filter((volume) => volume.visible && isSegmentationVolume(volume))
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

// Stable key over layer sources. Visibility deliberately stays out of this key:
// show/hide should use the display sync path and must not trigger full image or
// mesh reconciliation.
export function sourceKeyOf(volumes: Volume[]): string {
  return volumes.map((volume) => [
    volume.id,
    volume.url,
    volume.filename,
    volume.type ?? 'intensity',
    isSurfaceLayer(volume) ? `${volume.curvatureUrl ?? ''}:${volume.annotationUrl ?? ''}` : '',
  ].join(':')).sort().join('|');
}
