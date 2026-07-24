import Niivue from '@niivue/niivue';

import { isSurfaceLayer, type LayerType, type SurfaceLayer, type Volume } from '../types';
import type { LocationInfo } from '../types';
import { appFetchUrl } from '../utils/api';
import {
  asNiivueInterop,
  type NiivueMeshInterop,
  type NiivueVolumeInterop,
  type SurfaceCompanionLayer,
} from '../utils/niivueInterop';
import {
  applyBrightnessContrast,
  resolveVolumeColormap,
  resolveVolumeLabelColorMap,
} from '../utils/volumeColormap';
import { resolveSurfaceLayerColorMode, surfaceColor } from '../utils/surfaceColors';
import { reorderLoadedVolumes, setLoadedVolumeOpacity } from './loadedVolumeDisplay';

interface NiivueLocationObject {
  mm?: number[];
  vox?: number[];
  values?: { id: string; value: number; label?: string }[];
}

interface LabelLookupResult {
  index: number;
  name: string;
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
  return buffer;
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

async function addSurfaceCompanionLayer(nv: Niivue, mesh: NiivueMeshInterop, surface: SurfaceLayer, signal: AbortSignal) {
  const colorMode = resolveSurfaceLayerColorMode(surface);
  const companionUrl = colorMode === 'annotation' ? surface.annotationUrl : colorMode === 'curvature' ? surface.curvatureUrl : undefined;
  if (!companionUrl) return;

  const companionName = filenameFromUrl(
    companionUrl,
    colorMode === 'annotation' ? `${surface.filename}.annot` : `${surface.filename}.curv`,
  );
  const buffer = await fetchCachedArrayBuffer(companionUrl, signal);
  const file = new File([buffer], companionName);
  const layer: SurfaceCompanionLayer = colorMode === 'annotation'
    ? {
      url: file,
      name: companionName,
      opacity: 1,
      colormap: 'freesurfer',
    }
    : {
      url: file,
      name: companionName,
      opacity: 1,
      colormap: 'gray',
      colormapNegative: 'gray',
    };

  const meshIndex = asNiivueInterop(nv).meshes.indexOf(mesh);
  if (meshIndex >= 0) await nv.addMeshLayer(meshIndex, layer);
}

export function surfaceDisplayKey(surface: SurfaceLayer): string {
  const colorMode = resolveSurfaceLayerColorMode(surface);
  const companionUrl = colorMode === 'annotation' ? surface.annotationUrl : colorMode === 'curvature' ? surface.curvatureUrl : '';
  return `${colorMode}:${companionUrl ?? ''}`;
}

export async function syncNiivueSurfaceDisplay(nv: Niivue, mesh: NiivueMeshInterop, surface: SurfaceLayer, signal: AbortSignal): Promise<void> {
  const keyedMesh = mesh as NiivueMeshInterop & { __surfaceDisplayKey?: string };
  const nextKey = surfaceDisplayKey(surface);
  if (keyedMesh.__surfaceDisplayKey === nextKey) return;
  keyedMesh.__surfaceDisplayKey = nextKey;
  const meshIndex = asNiivueInterop(nv).meshes.indexOf(mesh);
  if (meshIndex < 0) return;
  while (mesh.layers.length > 0) await nv.removeMeshLayer(meshIndex, mesh.layers.length - 1);
  const colorMode = resolveSurfaceLayerColorMode(surface);
  if (colorMode === 'solid') {
    await nv.setMesh(meshIndex, { color: surfaceRgba(surface).map((value) => value / 255) as [number, number, number, number] });
    return;
  }
  try {
    await addSurfaceCompanionLayer(nv, mesh, surface, signal);
  } catch (error) {
    if (!signal.aborted) {
      console.warn(`[NiivuePane] Could not update surface coloring for ${surface.name}:`, error);
    }
  }
}

export async function addNiivueVolumeLayer(nv: Niivue, volume: Volume, signal: AbortSignal) {
  const filename = volume.filename || volume.name;
  const buffer = await fetchCachedArrayBuffer(volume.url, signal);
  const file = new File([buffer], filename);
  await nv.addVolume({
    url: file,
    name: filename,
    colormap: resolveVolumeColormap(volume),
    isColorbarVisible: false,
    opacity: effectiveLayerOpacity(volume),
    isTransparentBelowCalMin: volume.type === 'segmentation',
  });
  const loaded = nv.volumes.at(-1) as NiivueVolumeInterop | undefined;
  if (loaded) {
    loaded.id = volume.id;
    loaded.name = volume.name || volume.filename;
    loaded.url = volume.url;
    if (isSegmentationVolume(volume)) {
      const labelMap = await resolveVolumeLabelColorMap(volume);
      const volumeIndex = nv.volumes.indexOf(loaded);
      if (labelMap && volumeIndex >= 0) await nv.setColormapLabel(volumeIndex, labelMap);
    }
    loaded.isColorbarVisible = false;
    if (volume.type === undefined || volume.type === 'intensity') {
      applyBrightnessContrast(loaded, volume);
      const volumeIndex = nv.volumes.indexOf(loaded);
      if (volumeIndex >= 0) await nv.setVolume(volumeIndex, { calMin: loaded.calMin, calMax: loaded.calMax });
    }
  }
}

export async function addNiivueSurfaceLayer(nv: Niivue, surface: SurfaceLayer, signal: AbortSignal) {
  const filename = surface.filename || surface.name;
  const buffer = await fetchCachedArrayBuffer(surface.url, signal);
  const meshOptions = {
    url: new File([buffer], filename),
    name: filename,
    opacity: effectiveLayerOpacity(surface),
    color: surfaceRgba(surface).map((value) => value / 255) as [number, number, number, number],
    visible: surface.visible,
    shaderType: 'phong',
    sliceShaderType: 'crosscut',
  };
  await nv.addMesh(meshOptions);
  const nvInterop = asNiivueInterop(nv);
  const mesh = (nvInterop.meshes ?? []).find((item) => item.name === filename);
  if (mesh) {
    mesh.id = surface.id;
    // Per-pane shader: 2D planes use 'Crosscut' (cross-section outline); the 3D
    // pane is left on Niivue's default shader to show the full shaded mesh.
    await syncNiivueSurfaceDisplay(nv, mesh, surface, signal);
  }
}

function getCurrentLabelInfo(location: NiivueLocationObject, sourceVolumes: Volume[]): LabelLookupResult {
  const visibleSegmentationIds = sourceVolumes
    .filter((volume) => volume.visible && isSegmentationVolume(volume))
    .map((volume) => volume.id);

  for (const id of visibleSegmentationIds) {
    const value = location.values?.find((item) => item.id === id);
    const labelIndex = Math.round(Number(value?.value) || 0);
    if (labelIndex <= 0) continue;
    return { index: labelIndex, name: value?.label ?? String(labelIndex) };
  }

  return { index: 0, name: 'Background' };
}

export function locationFromNiivue(locationObject: unknown, nv: Niivue, sourceVolumes: Volume[]): LocationInfo | null {
  const location = locationObject as NiivueLocationObject | null;
  if (!location?.mm) return null;
  if (asNiivueInterop(nv).volumes.length === 0) return null;
  const voxel = location.vox ?? [0, 0, 0];
  const vox: [number, number, number] = [
    Math.round(voxel[0] ?? 0),
    Math.round(voxel[1] ?? 0),
    Math.round(voxel[2] ?? 0),
  ];

  const label = getCurrentLabelInfo(location, sourceVolumes);
  return {
    vox,
    labelIndex: label.index,
    labelName: label.name,
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
