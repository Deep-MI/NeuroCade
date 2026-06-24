import type { SurfaceLayer } from '../types.js';
import type { NiivueVolumeInterop } from '../utils/niivueInterop.js';
import {
  affineFromVolume,
  normalizeAffine,
  surfaceReferenceToBackgroundTransform,
  transformSurfaceVertices,
} from './surfaceTransforms.js';
import type { ViewerPlaneSliceType } from './viewerControls.js';

type Axis = 0 | 1 | 2;
type Mat4 = number[][];
type Point3 = [number, number, number];

export interface MeshGeometry {
  pts: Float32Array;
  tris: Uint32Array;
  coordinateSpace?: 'scanner-ras';
}

export interface AxisContourSet {
  sliceCoordinates: number[];
  segmentsBySlice: Float32Array[];
}

export interface SurfaceContourSet {
  axes: [AxisContourSet, AxisContourSet, AxisContourSet];
  bounds: [Point3, Point3];
}

export interface VolumeContourGeometry {
  affine: Mat4 | null;
  displayTransform: Mat4 | null;
  dims: [number, number, number];
  bounds: [Point3, Point3];
  spacing: [number, number, number];
  sliceCoordinates: [number[], number[], number[]];
  key: string;
}

const EPSILON = 1e-5;

function affineKey(affine: Mat4 | null): string {
  if (!affine) return 'none';
  return affine.flat().map((value) => Number(value).toPrecision(8)).join(',');
}

function normalizeFlatAffine(value: unknown): Mat4 | null {
  if (!Array.isArray(value) && !(value instanceof Float32Array) && !(value instanceof Float64Array)) return null;
  if (value.length < 16) return null;
  const values = Array.from(value as ArrayLike<number>);
  if (!values.slice(0, 16).every((entry) => typeof entry === 'number' && Number.isFinite(entry))) return null;
  return [
    [values[0], values[4], values[8], values[12]],
    [values[1], values[5], values[9], values[13]],
    [values[2], values[6], values[10], values[14]],
    [values[3], values[7], values[11], values[15]],
  ];
}

function normalizeDisplayTransform(value: unknown): Mat4 | null {
  return normalizeAffine(value) ?? normalizeFlatAffine(value);
}

function arrayKey(values: number[]): string {
  return values.map((value) => Number(value).toPrecision(8)).join(',');
}

function loadedVolumeDims(loaded: NiivueVolumeInterop | null | undefined): [number, number, number] | null {
  const dims = loaded?.dimsRAS ?? loaded?.dims ?? loaded?.hdr?.dims;
  const x = Math.trunc(dims?.[1] ?? dims?.[0] ?? 0);
  const y = Math.trunc(dims?.[2] ?? dims?.[1] ?? 0);
  const z = Math.trunc(dims?.[3] ?? dims?.[2] ?? 0);
  return x > 0 && y > 0 && z > 0 ? [x, y, z] : null;
}

function volumePixDims(loaded: NiivueVolumeInterop | null | undefined): [number, number, number] {
  const pixDims = loaded?.pixDimsRAS ?? loaded?.pixDims ?? loaded?.hdr?.pixDims;
  return [
    Math.max(Math.abs(Number(pixDims?.[1] ?? pixDims?.[0] ?? 1)) || 1, EPSILON),
    Math.max(Math.abs(Number(pixDims?.[2] ?? pixDims?.[1] ?? 1)) || 1, EPSILON),
    Math.max(Math.abs(Number(pixDims?.[3] ?? pixDims?.[2] ?? 1)) || 1, EPSILON),
  ];
}

function renderedAffineFromVolume(loaded: NiivueVolumeInterop | null | undefined): Mat4 | null {
  return normalizeDisplayTransform(loaded?.matRAS) ?? affineFromVolume(loaded);
}

function transformPoint(affine: Mat4 | null, x: number, y: number, z: number): Point3 {
  if (!affine) return [x, y, z];
  return [
    affine[0][0] * x + affine[0][1] * y + affine[0][2] * z + affine[0][3],
    affine[1][0] * x + affine[1][1] * y + affine[1][2] * z + affine[1][3],
    affine[2][0] * x + affine[2][1] * y + affine[2][2] * z + affine[2][3],
  ];
}

function expandBounds(bounds: [Point3, Point3], point: Point3): void {
  for (let axis = 0; axis < 3; axis += 1) {
    bounds[0][axis] = Math.min(bounds[0][axis], point[axis]);
    bounds[1][axis] = Math.max(bounds[1][axis], point[axis]);
  }
}

function sliceCoordinates(min: number, max: number, count: number): number[] {
  const sliceCount = Math.max(1, Math.trunc(count));
  const lo = Math.min(min, max);
  const hi = Math.max(min, max);
  const step = (hi - lo) / sliceCount;
  if (!Number.isFinite(step) || Math.abs(step) < EPSILON) return [(lo + hi) / 2];
  const coordinates: number[] = [];
  for (let index = 0; index < sliceCount; index += 1) {
    coordinates.push(lo + (index + 0.5) * step);
  }
  return coordinates;
}

export function volumeContourGeometry(loaded: NiivueVolumeInterop | null | undefined, isSliceMM = false): VolumeContourGeometry | null {
  const dims = loadedVolumeDims(loaded);
  if (!dims) return null;

  const affine = renderedAffineFromVolume(loaded);
  const orthoMin = loaded?.extentsMinOrtho;
  const orthoMax = loaded?.extentsMaxOrtho;
  const hasOrthoBounds = Array.isArray(orthoMin)
    && Array.isArray(orthoMax)
    && orthoMin.length >= 3
    && orthoMax.length >= 3;
  const displayTransform = !isSliceMM ? normalizeDisplayTransform(loaded?.mm2ortho) : null;
  const spacing = volumePixDims(loaded);
  let bounds: [Point3, Point3];

  if (!isSliceMM && hasOrthoBounds) {
    const min = orthoMin;
    const max = orthoMax;
    bounds = [
      [min[0], min[1], min[2]],
      [max[0], max[1], max[2]],
    ];
  } else {
    bounds = [
      [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY],
      [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY],
    ];
    for (const x of [0, dims[0] - 1]) {
      for (const y of [0, dims[1] - 1]) {
        for (const z of [0, dims[2] - 1]) {
          expandBounds(bounds, transformPoint(affine, x, y, z));
        }
      }
    }
  }

  const coordinates: [number[], number[], number[]] = [
    sliceCoordinates(bounds[0][0], bounds[1][0], dims[0]),
    sliceCoordinates(bounds[0][1], bounds[1][1], dims[1]),
    sliceCoordinates(bounds[0][2], bounds[1][2], dims[2]),
  ];

  return {
    affine,
    displayTransform,
    dims,
    bounds,
    spacing,
    sliceCoordinates: coordinates,
    key: `${loaded?.id ?? ''}|${loaded?.url ?? ''}|${isSliceMM ? 'mm' : 'ortho'}|${dims.join('x')}|${arrayKey(spacing)}|${affineKey(affine)}|${affineKey(displayTransform)}|${arrayKey(bounds.flat())}`,
  };
}

function pointFromVertices(vertices: Float32Array, index: number): Point3 {
  const offset = index * 3;
  return [vertices[offset], vertices[offset + 1], vertices[offset + 2]];
}

function addUniquePoint(points: Point3[], point: Point3): void {
  if (points.some((existing) => (
    Math.abs(existing[0] - point[0]) < EPSILON
    && Math.abs(existing[1] - point[1]) < EPSILON
    && Math.abs(existing[2] - point[2]) < EPSILON
  ))) return;
  points.push(point);
}

function interpolatePoint(a: Point3, b: Point3, t: number): Point3 {
  return [
    a[0] + (b[0] - a[0]) * t,
    a[1] + (b[1] - a[1]) * t,
    a[2] + (b[2] - a[2]) * t,
  ];
}

function squaredDistance(a: Point3, b: Point3): number {
  const dx = a[0] - b[0];
  const dy = a[1] - b[1];
  const dz = a[2] - b[2];
  return dx * dx + dy * dy + dz * dz;
}

export function trianglePlaneSegment(vertices: Float32Array, tri: [number, number, number], axis: Axis, planeCoordinate: number): [Point3, Point3] | null {
  const triangle = tri.map((index) => pointFromVertices(vertices, index)) as [Point3, Point3, Point3];
  const points: Point3[] = [];

  for (const [start, end] of [[0, 1], [1, 2], [2, 0]] as const) {
    const a = triangle[start];
    const b = triangle[end];
    const da = a[axis] - planeCoordinate;
    const db = b[axis] - planeCoordinate;
    if (Math.abs(da) < EPSILON && Math.abs(db) < EPSILON) continue;
    if (Math.abs(da) < EPSILON) {
      addUniquePoint(points, a);
    } else if (Math.abs(db) < EPSILON) {
      addUniquePoint(points, b);
    } else if (da * db < 0) {
      addUniquePoint(points, interpolatePoint(a, b, da / (da - db)));
    }
  }

  if (points.length < 2) return null;
  if (points.length === 2) {
    return squaredDistance(points[0], points[1]) > EPSILON * EPSILON ? [points[0], points[1]] : null;
  }

  let best: [Point3, Point3] | null = null;
  let bestDistance = 0;
  for (let i = 0; i < points.length; i += 1) {
    for (let j = i + 1; j < points.length; j += 1) {
      const distance = squaredDistance(points[i], points[j]);
      if (distance > bestDistance) {
        bestDistance = distance;
        best = [points[i], points[j]];
      }
    }
  }
  return bestDistance > EPSILON * EPSILON ? best : null;
}

function lowerBound(values: number[], target: number): number {
  let lo = 0;
  let hi = values.length;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (values[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function buildAxisContours(vertices: Float32Array, tris: Uint32Array, axis: Axis, coordinates: number[]): AxisContourSet {
  const segmentValues = coordinates.map(() => [] as number[]);
  for (let triIndex = 0; triIndex + 2 < tris.length; triIndex += 3) {
    const tri: [number, number, number] = [tris[triIndex], tris[triIndex + 1], tris[triIndex + 2]];
    const a = vertices[tri[0] * 3 + axis];
    const b = vertices[tri[1] * 3 + axis];
    const c = vertices[tri[2] * 3 + axis];
    const min = Math.min(a, b, c) - EPSILON;
    const max = Math.max(a, b, c) + EPSILON;
    const first = Math.max(0, lowerBound(coordinates, min));
    const last = Math.min(coordinates.length - 1, lowerBound(coordinates, max + EPSILON));
    for (let sliceIndex = first; sliceIndex <= last; sliceIndex += 1) {
      const segment = trianglePlaneSegment(vertices, tri, axis, coordinates[sliceIndex]);
      if (!segment) continue;
      segmentValues[sliceIndex].push(...segment[0], ...segment[1]);
    }
  }

  return {
    sliceCoordinates: coordinates,
    segmentsBySlice: segmentValues.map((values) => new Float32Array(values)),
  };
}

export function buildSurfaceContours(vertices: Float32Array, tris: Uint32Array, geometry: VolumeContourGeometry): SurfaceContourSet {
  return {
    axes: [
      buildAxisContours(vertices, tris, 0, geometry.sliceCoordinates[0]),
      buildAxisContours(vertices, tris, 1, geometry.sliceCoordinates[1]),
      buildAxisContours(vertices, tris, 2, geometry.sliceCoordinates[2]),
    ],
    bounds: geometry.bounds,
  };
}

export function transformedSurfaceVertices(surface: SurfaceLayer, mesh: MeshGeometry, geometry: VolumeContourGeometry): Float32Array {
  if (mesh.coordinateSpace === 'scanner-ras') {
    return geometry.displayTransform
      ? transformSurfaceVertices(mesh.pts, geometry.displayTransform)
      : new Float32Array(mesh.pts);
  }

  const referenceAffine = normalizeAffine(surface.surfaceReferenceAffine)
    ?? geometry.affine
    ?? [
      [1, 0, 0, 0],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1],
    ];
  const transform = geometry.affine
    ? surfaceReferenceToBackgroundTransform(referenceAffine, geometry.affine)
    : null;
  const backgroundVertices = transform ? transformSurfaceVertices(mesh.pts, transform) : new Float32Array(mesh.pts);
  return geometry.displayTransform ? transformSurfaceVertices(backgroundVertices, geometry.displayTransform) : backgroundVertices;
}

export function contourAxisForSliceType(sliceType: ViewerPlaneSliceType): Axis {
  return sliceType === 2 ? 0 : sliceType === 1 ? 1 : 2;
}

export function planeCoordinatePair(sliceType: ViewerPlaneSliceType): [Axis, Axis] {
  if (sliceType === 2) return [1, 2];
  if (sliceType === 1) return [0, 2];
  return [0, 1];
}

export function nearestSliceIndex(axisContours: AxisContourSet, coordinate: number): number {
  const coordinates = axisContours.sliceCoordinates;
  if (coordinates.length === 0) return -1;
  const index = lowerBound(coordinates, coordinate);
  if (index <= 0) return 0;
  if (index >= coordinates.length) return coordinates.length - 1;
  return Math.abs(coordinates[index] - coordinate) < Math.abs(coordinates[index - 1] - coordinate)
    ? index
    : index - 1;
}
