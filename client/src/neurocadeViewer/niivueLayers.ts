import type Niivue from '@niivue/niivue';
import type { MeshLayerFromUrlOptions } from '@niivue/niivue';

import { type LayerType, type LocationInfo, type SurfaceLayer, type Volume } from '../types.js';
import { appFetchUrl } from '../utils/api.js';
import {
  asNiivueInterop,
  type NiivueColorMap,
  type NiivueMeshInterop,
  type NiivueVolumeInterop,
} from '../utils/niivueInterop.js';
import { compileNiivueLabelColorMap } from '../utils/niivueColorMap.js';
import { prepareNiivueVolume } from '../utils/niivueMgh.js';
import {
  applyBrightnessContrast,
  resolveVolumeColormap,
  resolveVolumeLabelColorMap,
} from '../utils/volumeColormap.js';
import {
  freeSurferAnnotationToMz3,
  freeSurferCurvatureToMz3,
} from '../utils/SurfaceLoader.js';
import {
  curvatureNegativeThreshold,
  curvaturePositiveThreshold,
  resolveSurfaceLayerColorMode,
  surfaceColor,
} from '../utils/surfaceColors.js';
import { effectiveLayerOpacity, surfaceDisplayKey } from './layerDisplay.js';
import {
  referenceWorldToVoxel,
} from './loadedVolumeDisplay.js';

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
  return volume.type;
}

function isSegmentationVolume(volume: Volume): boolean {
  return volume.type === 'segmentation';
}

const arrayBufferCache = new Map<string, Promise<ArrayBuffer>>();
let arrayBufferCacheScope: string | null = null;

export function setNiivueLayerBufferCacheScope(scope: string): void {
  if (scope === arrayBufferCacheScope) return;
  arrayBufferCache.clear();
  arrayBufferCacheScope = scope;
}

async function fetchCachedArrayBuffer(url: string, signal: AbortSignal): Promise<ArrayBuffer> {
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
  const expectedVertexCount = mesh.positions.length / 3;
  const layerBuffer = colorMode === 'annotation'
    ? freeSurferAnnotationToMz3(buffer, expectedVertexCount)
    : freeSurferCurvatureToMz3(buffer, expectedVertexCount);
  const layerName = `${companionName.replace(/\.[^.]+$/i, '')}.mz3`;
  const file = new File([layerBuffer], layerName);
  const layer: MeshLayerFromUrlOptions = colorMode === 'annotation'
    ? {
      url: file,
      name: companionName,
      opacity: 1,
      colormap: 'gray',
    }
    : {
      url: file,
      name: companionName,
      opacity: 1,
      colormap: nv.addColormap('NeuroCade Freeview curvature', {
        R: [0, 255],
        G: [255, 0],
        B: [0, 0],
        A: [255, 255],
        I: [0, 255],
      }),
      calMin: -curvatureNegativeThreshold(surface),
      calMax: curvaturePositiveThreshold(surface),
      colormapType: 0,
      isTransparentBelowCalMin: false,
      isColorbarVisible: false,
    };

  const meshIndex = asNiivueInterop(nv).meshes.indexOf(mesh);
  if (meshIndex >= 0) await nv.addMeshLayer(meshIndex, layer);
}

export async function syncNiivueSurfaceDisplay(nv: Niivue, mesh: NiivueMeshInterop, surface: SurfaceLayer, signal: AbortSignal): Promise<void> {
  const nextKey = surfaceDisplayKey(surface);
  if (mesh.__surfaceDisplayKey === nextKey) return;
  mesh.__surfaceDisplayKey = nextKey;
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

interface PreparedVolumeLayer {
  source: Volume;
  file: File;
  labelMap?: NiivueColorMap;
}

async function prepareVolumeLayer(volume: Volume, signal: AbortSignal): Promise<PreparedVolumeLayer> {
  const filename = volume.filename || volume.name;
  const [buffer, labelMap] = await Promise.all([
    fetchCachedArrayBuffer(volume.url, signal),
    resolveVolumeLabelColorMap(volume),
  ]);
  if (signal.aborted) throw new DOMException('Volume preparation aborted', 'AbortError');
  const prepared = await prepareNiivueVolume(buffer, filename);
  if (signal.aborted) throw new DOMException('Volume preparation aborted', 'AbortError');
  return {
    source: volume,
    file: new File([prepared.buffer], prepared.filename),
    labelMap,
  };
}

function configureLoadedVolume(
  loaded: NiivueVolumeInterop,
  { source, labelMap }: PreparedVolumeLayer,
): void {
  loaded.id = source.id;
  loaded.name = source.name || source.filename;
  loaded.url = source.url;
  loaded.isColorbarVisible = false;
  loaded.opacity = effectiveLayerOpacity(source);
  loaded.colormap = resolveVolumeColormap(source);
  loaded.isTransparentBelowCalMin = source.type === 'segmentation';
  if (isSegmentationVolume(source) && labelMap) {
    loaded.colormapLabel = compileNiivueLabelColorMap(labelMap);
  }
  if (source.type === undefined || source.type === 'intensity') {
    applyBrightnessContrast(loaded, source);
  }
  loaded.isDirty = true;
}

export async function addNiivueVolumeLayers(
  nv: Niivue,
  volumes: Volume[],
  signal: AbortSignal,
): Promise<void> {
  const preparedLayers = await Promise.all(
    volumes.map((volume) => prepareVolumeLayer(volume, signal)),
  );
  if (signal.aborted) throw new DOMException('Volume loading aborted', 'AbortError');

  for (const prepared of preparedLayers) {
    const volumeCount = nv.volumes.length;
    await nv.model.addVolume({
      url: prepared.file,
      name: prepared.source.filename || prepared.source.name,
      colormap: resolveVolumeColormap(prepared.source),
      isColorbarVisible: false,
      opacity: effectiveLayerOpacity(prepared.source),
      isTransparentBelowCalMin: prepared.source.type === 'segmentation',
    });
    if (signal.aborted) throw new DOMException('Volume loading aborted', 'AbortError');
    const loaded = nv.volumes[volumeCount] as NiivueVolumeInterop | undefined;
    if (loaded) {
      configureLoadedVolume(loaded, prepared);
    }
  }
  if (preparedLayers.length > 0) {
    await nv.updateGLVolume();
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

export function locationFromNiivue(
  locationObject: unknown,
  nv: Niivue,
  sourceVolumes: Volume[],
  coordinateSourceId?: string | null,
): LocationInfo | null {
  const location = locationObject as NiivueLocationObject | null;
  if (!location?.mm) return null;
  const voxel = referenceWorldToVoxel(nv, location.mm, coordinateSourceId);
  if (!voxel) return null;
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
