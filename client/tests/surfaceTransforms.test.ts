import assert from 'node:assert/strict';
import test from 'node:test';

import type { SurfaceLayer } from '../src/types.js';
import type { NiivueMeshInterop, NiivueVolumeInterop } from '../src/utils/niivueInterop.js';
import {
  applySurfaceReferenceTransform,
  surfaceReferenceToBackgroundTransform,
  transformSurfaceVertices,
} from '../src/neurocadeViewer/surfaceTransforms.js';

function affine(scale: [number, number, number], offset: [number, number, number]): number[][] {
  return [
    [scale[0], 0, 0, offset[0]],
    [0, scale[1], 0, offset[1]],
    [0, 0, scale[2], offset[2]],
    [0, 0, 0, 1],
  ];
}

function volumeWithAffine(matrix: number[][]): NiivueVolumeInterop {
  return { hdr: { affine: matrix } };
}

function surfaceLayer(referenceAffine?: number[][]): SurfaceLayer {
  return {
    id: 'lh.pial',
    name: 'Left pial',
    filename: 'lh.pial',
    url: '/lh.pial',
    opacity: 1,
    colormap: 'surface',
    visible: true,
    type: 'surface',
    surfaceReferenceAffine: referenceAffine,
  };
}

function assertFloatArrayAlmostEqual(actual: Float32Array, expected: number[], epsilon = 1e-6): void {
  assert.equal(actual.length, expected.length);
  for (let index = 0; index < actual.length; index += 1) {
    assert.ok(
      Math.abs(actual[index] - expected[index]) <= epsilon,
      `index ${index}: expected ${expected[index]}, got ${actual[index]}`,
    );
  }
}

void test('surfaceReferenceToBackgroundTransform maps reference spacing and origin into the active background', () => {
  const reference = affine([1, 1, 1], [0, 0, 0]);
  const background = affine([2, 3, 4], [10, -20, 5]);
  const transform = surfaceReferenceToBackgroundTransform(reference, background);

  assert.deepEqual(transform, background);
  assertFloatArrayAlmostEqual(
    transformSurfaceVertices(new Float32Array([1, 2, 3]), transform),
    [12, -14, 17],
  );
});

void test('surface transform accounts for non-identity reference spacing', () => {
  const reference = affine([2, 2, 2], [4, 0, -2]);
  const background = affine([1, 1, 1], [0, 10, 0]);
  const transform = surfaceReferenceToBackgroundTransform(reference, background);

  assert.ok(transform);
  assertFloatArrayAlmostEqual(
    transformSurfaceVertices(new Float32Array([6, 4, 2]), transform),
    [1, 12, 2],
  );
});

void test('surface transform supports rotated reference affines', () => {
  const reference = [
    [0, -1, 0, 0],
    [1, 0, 0, 0],
    [0, 0, 1, 0],
    [0, 0, 0, 1],
  ];
  const background = affine([1, 1, 1], [10, 0, 0]);
  const transform = surfaceReferenceToBackgroundTransform(reference, background);

  assert.ok(transform);
  assertFloatArrayAlmostEqual(
    transformSurfaceVertices(new Float32Array([0, 1, 2]), transform),
    [11, 0, 2],
  );
});

void test('applySurfaceReferenceTransform caches original vertices and skips unchanged backgrounds', () => {
  let updateCount = 0;
  const reference = affine([1, 1, 1], [0, 0, 0]);
  const background = affine([1, 1, 1], [5, 0, 0]);
  const mesh: NiivueMeshInterop = {
    id: 'lh.pial',
    name: 'lh.pial',
    pts: new Float32Array([0, 0, 0, 1, 1, 1]),
    updateMesh: () => { updateCount += 1; },
  };

  assert.equal(applySurfaceReferenceTransform(mesh, surfaceLayer(reference), background), true);
  assertFloatArrayAlmostEqual(mesh.pts!, [5, 0, 0, 6, 1, 1]);
  assertFloatArrayAlmostEqual(mesh.__originalPts!, [0, 0, 0, 1, 1, 1]);
  assert.equal(updateCount, 1);

  assert.equal(applySurfaceReferenceTransform(mesh, surfaceLayer(reference), background), false);
  assertFloatArrayAlmostEqual(mesh.pts!, [5, 0, 0, 6, 1, 1]);
  assert.equal(updateCount, 1);
});

void test('applySurfaceReferenceTransform reuses captured reference affine when active background changes', () => {
  const firstBackground = affine([1, 1, 1], [0, 0, 0]);
  const secondBackground = affine([1, 1, 1], [0, 7, 0]);
  const mesh: NiivueMeshInterop = {
    id: 'lh.pial',
    name: 'lh.pial',
    pts: new Float32Array([2, 3, 4]),
  };
  const surface = surfaceLayer();

  assert.equal(applySurfaceReferenceTransform(mesh, surface, firstBackground), true);
  assertFloatArrayAlmostEqual(mesh.pts!, [2, 3, 4]);

  assert.equal(applySurfaceReferenceTransform(mesh, surface, secondBackground), true);
  assertFloatArrayAlmostEqual(mesh.pts!, [2, 10, 4]);
});

void test('applySurfaceReferenceTransform falls back cleanly when a background volume has no affine', () => {
  const mesh: NiivueMeshInterop = {
    id: 'lh.pial',
    name: 'lh.pial',
    pts: new Float32Array([2, 3, 4]),
  };

  assert.equal(applySurfaceReferenceTransform(mesh, surfaceLayer(), null), true);
  assertFloatArrayAlmostEqual(mesh.pts!, [2, 3, 4]);
  assert.equal(applySurfaceReferenceTransform(mesh, surfaceLayer(), null), false);

  const background = volumeWithAffine(affine([1, 1, 1], [1, 2, 3]));
  assert.equal(applySurfaceReferenceTransform(mesh, surfaceLayer(), background.hdr?.affine ?? null), true);
  assertFloatArrayAlmostEqual(mesh.pts!, [3, 5, 7]);
});
