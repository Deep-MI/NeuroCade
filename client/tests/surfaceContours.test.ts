import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildSurfaceContours,
  nearestSliceIndex,
  trianglePlaneSegment,
  transformedSurfaceVertices,
  volumeContourGeometry,
  type VolumeContourGeometry,
} from '../src/neurocadeViewer/surfaceContours.js';
import type { SurfaceLayer } from '../src/types.js';

function assertPointAlmostEqual(actual: number[], expected: number[], epsilon = 1e-6): void {
  assert.equal(actual.length, expected.length);
  for (let index = 0; index < actual.length; index += 1) {
    assert.ok(
      Math.abs(actual[index] - expected[index]) <= epsilon,
      `index ${index}: expected ${expected[index]}, got ${actual[index]}`,
    );
  }
}

function testGeometry(): VolumeContourGeometry {
  return {
    affine: null,
    displayTransform: null,
    dims: [3, 3, 3],
    bounds: [[0, 0, 0], [2, 2, 2]],
    spacing: [1, 1, 1],
    sliceCoordinates: [[0, 1, 2], [0, 1, 2], [0, 1, 2]],
    key: 'test',
  };
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

void test('trianglePlaneSegment returns the exact two edge intersections for a slicing plane', () => {
  const vertices = new Float32Array([
    0, 0, 0,
    2, 0, 0,
    0, 2, 0,
  ]);
  const segment = trianglePlaneSegment(vertices, [0, 1, 2], 0, 1);

  assert.ok(segment);
  assertPointAlmostEqual(segment[0], [1, 0, 0]);
  assertPointAlmostEqual(segment[1], [1, 1, 0]);
});

void test('trianglePlaneSegment skips triangles that do not intersect the plane', () => {
  const vertices = new Float32Array([
    0, 0, 0,
    0, 1, 0,
    0, 0, 1,
  ]);

  assert.equal(trianglePlaneSegment(vertices, [0, 1, 2], 0, 1), null);
});

void test('buildSurfaceContours bins triangle intersections by slice coordinate', () => {
  const vertices = new Float32Array([
    0, 0, 0,
    2, 0, 0,
    0, 2, 0,
  ]);
  const tris = new Uint32Array([0, 1, 2]);
  const contours = buildSurfaceContours(vertices, tris, testGeometry());

  const xAxis = contours.axes[0];
  assert.equal(xAxis.segmentsBySlice[1].length, 6);
  assertPointAlmostEqual(Array.from(xAxis.segmentsBySlice[1]), [1, 0, 0, 1, 1, 0]);
});

void test('nearestSliceIndex chooses the nearest precomputed contour slice', () => {
  const contours = buildSurfaceContours(
    new Float32Array([0, 0, 0, 2, 0, 0, 0, 2, 0]),
    new Uint32Array([0, 1, 2]),
    testGeometry(),
  );

  assert.equal(nearestSliceIndex(contours.axes[0], 0.2), 0);
  assert.equal(nearestSliceIndex(contours.axes[0], 0.8), 1);
  assert.equal(nearestSliceIndex(contours.axes[0], 1.7), 2);
});

void test('transformedSurfaceVertices keeps FreeSurfer scanner RAS vertices in world space', () => {
  const geometry: VolumeContourGeometry = {
    affine: [
      [2, 0, 0, 10],
      [0, 2, 0, 20],
      [0, 0, 2, 30],
      [0, 0, 0, 1],
    ],
    displayTransform: [
      [1, 0, 0, 5],
      [0, 1, 0, 6],
      [0, 0, 1, 7],
      [0, 0, 0, 1],
    ],
    dims: [3, 3, 3],
    bounds: [[0, 0, 0], [2, 2, 2]],
    spacing: [1, 1, 1],
    sliceCoordinates: [[0, 1, 2], [0, 1, 2], [0, 1, 2]],
    key: 'scanner-ras-test',
  };

  const vertices = transformedSurfaceVertices(
    surfaceLayer([
      [1, 0, 0, 100],
      [0, 1, 0, 100],
      [0, 0, 1, 100],
      [0, 0, 0, 1],
    ]),
    {
      pts: new Float32Array([1, 2, 3]),
      tris: new Uint32Array(),
      coordinateSpace: 'scanner-ras',
    },
    geometry,
  );

  assert.deepEqual(Array.from(vertices), [6, 8, 10]);
});

void test('volumeContourGeometry uses Niivue rendered RAS space before native header space', () => {
  const geometry = volumeContourGeometry({
    id: 'orig',
    dimsRAS: [3, 30, 20, 10],
    pixDimsRAS: [1, 4, 3, 2],
    matRAS: [
      4, 0, 0, 0,
      0, 3, 0, 0,
      0, 0, 2, 0,
      100, 200, 300, 1,
    ],
    extentsMinOrtho: [10, 20, 30],
    extentsMaxOrtho: [130, 80, 50],
    hdr: {
      dims: [3, 7, 8, 9],
      pixDims: [1, 7, 8, 9],
      affine: [
        [7, 0, 0, 700],
        [0, 8, 0, 800],
        [0, 0, 9, 900],
        [0, 0, 0, 1],
      ],
    },
  });

  assert.ok(geometry);
  assert.deepEqual(geometry.dims, [30, 20, 10]);
  assert.deepEqual(geometry.spacing, [4, 3, 2]);
  assert.deepEqual(geometry.affine, [
    [4, 0, 0, 100],
    [0, 3, 0, 200],
    [0, 0, 2, 300],
    [0, 0, 0, 1],
  ]);
  assert.deepEqual(geometry.bounds, [[10, 20, 30], [130, 80, 50]]);
  assert.match(geometry.key, /^orig\|/);
});
