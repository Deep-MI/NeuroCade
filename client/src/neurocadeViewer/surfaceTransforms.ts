import type { SurfaceLayer, Volume } from '../types.js';
import { isSurfaceLayer } from '../types.js';
import type { NiivueMeshInterop, NiivueVolumeInterop } from '../utils/niivueInterop.js';

type Mat4 = number[][];

const IDENTITY_4X4: Mat4 = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];

function isFiniteMat4(value: unknown): value is Mat4 {
  return Array.isArray(value)
    && value.length >= 4
    && value.slice(0, 4).every((row) => (
      Array.isArray(row)
      && row.length >= 4
      && row.slice(0, 4).every((entry) => typeof entry === 'number' && Number.isFinite(entry))
    ));
}

export function normalizeAffine(value: unknown): Mat4 | null {
  return isFiniteMat4(value)
    ? value.slice(0, 4).map((row) => row.slice(0, 4))
    : null;
}

export function affineFromVolume(volume: NiivueVolumeInterop | null | undefined): Mat4 | null {
  return normalizeAffine(
    typeof volume?.getAffine === 'function'
      ? volume.getAffine()
      : volume?.hdr?.affine,
  );
}

function affineKey(affine: Mat4 | null): string {
  if (!affine) return '';
  return affine.flat().map((value) => Number(value).toPrecision(8)).join(',');
}

export function invertAffine4x4(matrix: Mat4): Mat4 | null {
  const m = matrix.flat();
  const inv = new Array<number>(16);

  inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15]
    + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10];
  inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15]
    - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10];
  inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15]
    + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9];
  inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14]
    - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9];
  inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15]
    - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10];
  inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15]
    + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10];
  inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15]
    - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9];
  inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14]
    + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9];
  inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15]
    + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6];
  inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15]
    - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6];
  inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15]
    + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5];
  inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14]
    - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5];
  inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11]
    - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6];
  inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11]
    + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6];
  inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11]
    - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5];
  inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10]
    + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5];

  const determinant = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12];
  if (Math.abs(determinant) < 1e-12) return null;
  const scale = 1 / determinant;
  return [
    [inv[0] * scale, inv[1] * scale, inv[2] * scale, inv[3] * scale],
    [inv[4] * scale, inv[5] * scale, inv[6] * scale, inv[7] * scale],
    [inv[8] * scale, inv[9] * scale, inv[10] * scale, inv[11] * scale],
    [inv[12] * scale, inv[13] * scale, inv[14] * scale, inv[15] * scale],
  ];
}

export function multiplyAffine4x4(a: Mat4, b: Mat4): Mat4 {
  return a.map((row, rowIndex) => row.map((_, colIndex) => (
    a[rowIndex][0] * b[0][colIndex]
    + a[rowIndex][1] * b[1][colIndex]
    + a[rowIndex][2] * b[2][colIndex]
    + a[rowIndex][3] * b[3][colIndex]
  )));
}

export function surfaceReferenceToBackgroundTransform(referenceAffine: Mat4, backgroundAffine: Mat4): Mat4 | null {
  const inverseReference = invertAffine4x4(referenceAffine);
  return inverseReference ? multiplyAffine4x4(backgroundAffine, inverseReference) : null;
}

export function transformSurfaceVertices(vertices: Float32Array, transform: Mat4): Float32Array {
  const transformed = new Float32Array(vertices.length);
  for (let i = 0; i < vertices.length; i += 3) {
    const x = vertices[i];
    const y = vertices[i + 1];
    const z = vertices[i + 2];
    transformed[i] = transform[0][0] * x + transform[0][1] * y + transform[0][2] * z + transform[0][3];
    transformed[i + 1] = transform[1][0] * x + transform[1][1] * y + transform[1][2] * z + transform[1][3];
    transformed[i + 2] = transform[2][0] * x + transform[2][1] * y + transform[2][2] * z + transform[2][3];
  }
  return transformed;
}

function furthestFromOrigin(vertices: Float32Array): number {
  let furthest = 0;
  for (let i = 0; i < vertices.length; i += 3) {
    furthest = Math.max(furthest, Math.hypot(vertices[i], vertices[i + 1], vertices[i + 2]));
  }
  return furthest;
}

function findSurfaceSource(mesh: NiivueMeshInterop, sources: Volume[]): SurfaceLayer | undefined {
  return sources.filter(isSurfaceLayer).find((surface) => surface.id === mesh.id || surface.filename === mesh.name);
}

export function applySurfaceReferenceTransform(
  mesh: NiivueMeshInterop,
  surface: SurfaceLayer,
  backgroundAffine: Mat4 | null,
  gl?: WebGL2RenderingContext | null,
): boolean {
  const originalPts = mesh.__originalPts ?? (mesh.pts ? new Float32Array(mesh.pts) : null);
  if (!originalPts) return false;
  mesh.__originalPts = originalPts;

  const referenceAffine = mesh.__surfaceReferenceAffine
    ?? normalizeAffine(surface.surfaceReferenceAffine)
    ?? backgroundAffine
    ?? IDENTITY_4X4;
  mesh.__surfaceReferenceAffine = referenceAffine;

  const transform = backgroundAffine
    ? surfaceReferenceToBackgroundTransform(referenceAffine, backgroundAffine)
    : IDENTITY_4X4;
  if (!transform) return false;

  const nextKey = `${affineKey(referenceAffine)}|${affineKey(backgroundAffine)}|${originalPts.length}`;
  if (mesh.__surfaceTransformKey === nextKey) return false;

  mesh.pts = transformSurfaceVertices(originalPts, transform);
  mesh.__surfaceTransformKey = nextKey;
  mesh.furthestVertexFromOrigin = furthestFromOrigin(mesh.pts);
  mesh.updateMesh?.(gl);
  return true;
}

export function syncSurfaceReferenceTransforms(
  meshes: NiivueMeshInterop[],
  sources: Volume[],
  backgroundVolume: NiivueVolumeInterop | null | undefined,
  gl?: WebGL2RenderingContext | null,
): boolean {
  const backgroundAffine = affineFromVolume(backgroundVolume);
  let changed = false;
  for (const mesh of meshes) {
    const surface = findSurfaceSource(mesh, sources);
    if (!surface) continue;
    changed = applySurfaceReferenceTransform(mesh, surface, backgroundAffine, gl) || changed;
  }
  return changed;
}
