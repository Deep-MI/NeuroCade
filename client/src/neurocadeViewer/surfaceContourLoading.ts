import { NVMesh } from '@niivue/niivue';

import type { SurfaceLayer } from '../types';
import { appFetchUrl } from '../utils/api';
import { parseFreeSurferSurface } from '../utils/SurfaceLoader';
import {
  buildSurfaceContours,
  transformedSurfaceVertices,
  type MeshGeometry,
  type SurfaceContourSet,
  type VolumeContourGeometry,
} from './surfaceContours';

const geometryCache = new Map<string, Promise<MeshGeometry>>();
const contourCache = new Map<string, Promise<SurfaceContourSet>>();

function filenameFromUrl(url: string, fallback: string): string {
  try {
    const pathname = new URL(url, window.location.origin).pathname;
    return decodeURIComponent(pathname.split('/').pop() ?? fallback);
  } catch {
    return fallback;
  }
}

function arrayKey(values: number[]): string {
  return values.map((value) => Number(value).toPrecision(8)).join(',');
}

function translatedSurfaceVertices(vertices: Float32Array, cras: [number, number, number]): Float32Array {
  const translated = new Float32Array(vertices.length);
  for (let index = 0; index < vertices.length; index += 3) {
    translated[index] = vertices[index] + cras[0];
    translated[index + 1] = vertices[index + 1] + cras[1];
    translated[index + 2] = vertices[index + 2] + cras[2];
  }
  return translated;
}

function parseFreeSurferMeshGeometry(buffer: ArrayBuffer): MeshGeometry | null {
  try {
    const surface = parseFreeSurferSurface(buffer);
    const cras = surface.volumeInfo?.cras;
    return {
      pts: cras ? translatedSurfaceVertices(surface.vertices, cras) : new Float32Array(surface.vertices),
      tris: new Uint32Array(surface.indices),
      coordinateSpace: cras ? 'scanner-ras' : undefined,
    };
  } catch {
    return null;
  }
}

export async function loadSurfaceGeometry(surface: SurfaceLayer, gl: WebGL2RenderingContext, signal: AbortSignal): Promise<MeshGeometry> {
  const key = `${surface.url}|${surface.filename ?? surface.name}`;
  const cached = geometryCache.get(key);
  if (cached) return cached;

  const promise = (async () => {
    const filename = surface.filename ?? surface.name ?? filenameFromUrl(surface.url, 'surface.mesh');
    const response = await appFetchUrl(surface.url, { signal });
    if (!response.ok) throw new Error(`Failed to load surface ${filename}: ${response.status}`);
    const buffer = await response.arrayBuffer();
    if (signal.aborted) throw new DOMException('Surface load aborted', 'AbortError');
    const parsed = parseFreeSurferMeshGeometry(buffer);
    if (parsed) return parsed;

    const mesh = await NVMesh.loadFromUrl({
      url: filename,
      name: filename,
      buffer,
      gl,
      opacity: 1,
      rgba255: [255, 255, 255, 255],
      visible: false,
    });
    if (!mesh.pts || !mesh.tris) throw new Error(`Surface ${filename} did not include triangle geometry`);
    return {
      pts: new Float32Array(mesh.pts),
      tris: new Uint32Array(mesh.tris),
    };
  })().catch((error) => {
    geometryCache.delete(key);
    throw error;
  });
  geometryCache.set(key, promise);
  return promise;
}

export async function contoursForSurface(surface: SurfaceLayer, gl: WebGL2RenderingContext, geometry: VolumeContourGeometry, signal: AbortSignal): Promise<SurfaceContourSet> {
  const key = `${surface.id}|${surface.url}|${arrayKey(surface.surfaceReferenceAffine?.flat() ?? [])}|${geometry.key}`;
  const cached = contourCache.get(key);
  if (cached) return cached;

  const promise = (async () => {
    const mesh = await loadSurfaceGeometry(surface, gl, signal);
    if (signal.aborted) throw new DOMException('Surface contour build aborted', 'AbortError');
    const vertices = transformedSurfaceVertices(surface, mesh, geometry);
    return buildSurfaceContours(vertices, mesh.tris, geometry);
  })().catch((error) => {
    contourCache.delete(key);
    throw error;
  });
  contourCache.set(key, promise);
  return promise;
}
