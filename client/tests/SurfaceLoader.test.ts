import assert from 'node:assert/strict';
import test from 'node:test';

import { parseFreeSurferAnnotation, parseFreeSurferCurvature, parseFreeSurferSurface } from '../src/utils/SurfaceLoader.js';
import { SURFACE_LIGHT_DIRECTIONS } from '../src/utils/surfaceLighting.js';

const TRIANGLE_FILE_MAGIC = 16_777_214;
const NEW_CURV_FILE_MAGIC = 16_777_215;

function writeInt3(view: DataView, offset: number, value: number): void {
  view.setUint8(offset, (value >> 16) & 0xff);
  view.setUint8(offset + 1, (value >> 8) & 0xff);
  view.setUint8(offset + 2, value & 0xff);
}

function makeSurface(vertices: number[], indices: number[]): ArrayBuffer {
  const header = new TextEncoder().encode('created by test\ncomment\n');
  const vertexCount = vertices.length / 3;
  const faceCount = indices.length / 3;
  const buffer = new ArrayBuffer(3 + header.byteLength + 8 + vertexCount * 3 * 4 + faceCount * 3 * 4);
  const view = new DataView(buffer);
  let offset = 0;
  writeInt3(view, offset, TRIANGLE_FILE_MAGIC);
  offset += 3;
  new Uint8Array(buffer, offset, header.byteLength).set(header);
  offset += header.byteLength;
  view.setInt32(offset, vertexCount, false);
  offset += 4;
  view.setInt32(offset, faceCount, false);
  offset += 4;

  for (const value of vertices) {
    view.setFloat32(offset, value, false);
    offset += 4;
  }
  for (const index of indices) {
    view.setUint32(offset, index, false);
    offset += 4;
  }
  return buffer;
}

function makeSurfaceWithVolumeInfo(vertices: number[], indices: number[], volumeInfo: string): ArrayBuffer {
  const base = new Uint8Array(makeSurface(vertices, indices));
  const footer = new TextEncoder().encode(volumeInfo);
  const buffer = new ArrayBuffer(base.byteLength + 12 + footer.byteLength);
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  bytes.set(base, 0);
  let offset = base.byteLength;
  view.setUint32(offset, 2, false);
  offset += 4;
  view.setUint32(offset, 0, false);
  offset += 4;
  view.setUint32(offset, 20, false);
  offset += 4;
  bytes.set(footer, offset);
  return buffer;
}

function makeTriangleSurface(): ArrayBuffer {
  return makeSurface(
    [0, 0, 0, 1, 0, 0, 0, 1, 0],
    [0, 1, 2],
  );
}

function dot(a: number[], b: number[]): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function makeNewCurvature(values: number[]): ArrayBuffer {
  const buffer = new ArrayBuffer(3 + 12 + values.length * 4);
  const view = new DataView(buffer);
  let offset = 0;
  writeInt3(view, offset, NEW_CURV_FILE_MAGIC);
  offset += 3;
  view.setInt32(offset, values.length, false);
  offset += 4;
  view.setInt32(offset, 1, false);
  offset += 4;
  view.setInt32(offset, 1, false);
  offset += 4;
  for (const value of values) {
    view.setFloat32(offset, value, false);
    offset += 4;
  }
  return buffer;
}

function makeLegacyCurvature(values: number[]): ArrayBuffer {
  const buffer = new ArrayBuffer(3 + 3 + values.length * 2);
  const view = new DataView(buffer);
  let offset = 0;
  writeInt3(view, offset, values.length);
  offset += 3;
  writeInt3(view, offset, 1);
  offset += 3;
  for (const value of values) {
    view.setInt16(offset, value * 100, false);
    offset += 2;
  }
  return buffer;
}

function fsStringBytes(value: string): Uint8Array {
  const encoded = new TextEncoder().encode(value);
  const bytes = new Uint8Array(4 + encoded.length + 1);
  new DataView(bytes.buffer).setInt32(0, encoded.length + 1, false);
  bytes.set(encoded, 4);
  return bytes;
}

function makeAnnotation(): ArrayBuffer {
  const strings = [fsStringBytes('test.ctab'), fsStringBytes('unknown'), fsStringBytes('region')];
  const byteLength = 4 + 3 * 8 + 4 + 4 + 4 + strings[0].length + 4
    + 4 + strings[1].length + 16
    + 4 + strings[2].length + 16;
  const buffer = new ArrayBuffer(byteLength);
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  let offset = 0;
  view.setInt32(offset, 3, false);
  offset += 4;
  const regionCode = 10 + (20 << 8) + (30 << 16);
  for (const [vertex, label] of [[0, 0], [1, regionCode], [2, regionCode]]) {
    view.setInt32(offset, vertex, false);
    offset += 4;
    view.setInt32(offset, label, false);
    offset += 4;
  }
  view.setInt32(offset, 1, false);
  offset += 4;
  view.setInt32(offset, -2, false);
  offset += 4;
  view.setInt32(offset, 2, false);
  offset += 4;
  bytes.set(strings[0], offset);
  offset += strings[0].length;
  view.setInt32(offset, 2, false);
  offset += 4;

  view.setInt32(offset, 0, false);
  offset += 4;
  bytes.set(strings[1], offset);
  offset += strings[1].length;
  for (const value of [0, 0, 0, 255]) {
    view.setInt32(offset, value, false);
    offset += 4;
  }

  view.setInt32(offset, 1, false);
  offset += 4;
  bytes.set(strings[2], offset);
  offset += strings[2].length;
  for (const value of [10, 20, 30, 0]) {
    view.setInt32(offset, value, false);
    offset += 4;
  }
  return buffer;
}

void test('parseFreeSurferSurface reads binary triangle surfaces and computes normals', () => {
  const surface = parseFreeSurferSurface(makeTriangleSurface());

  assert.equal(surface.vertexCount, 3);
  assert.equal(surface.faceCount, 1);
  assert.deepEqual([...surface.indices], [0, 1, 2]);
  assert.deepEqual([...surface.vertices], [0, 0, 0, 1, 0, 0, 0, 1, 0]);
  assert.deepEqual([...surface.normals], [0, 0, 1, 0, 0, 1, 0, 0, 1]);
});

void test('parseFreeSurferSurface reads optional FreeSurfer volume info footer', () => {
  const surface = parseFreeSurferSurface(makeSurfaceWithVolumeInfo(
    [0, 0, 0, 1, 0, 0, 0, 1, 0],
    [0, 1, 2],
    [
      'valid = 1  # volume info valid',
      'filename = /subject/mri/wm.mgz',
      'volume = 320 320 320',
      'voxelsize = 0.8 0.8 0.8',
      'xras   = -1 0 0',
      'yras   = 0 0 -1',
      'zras   = 0 1 0',
      'cras   = -0.1 23.4 -25.6',
    ].join('\n'),
  ));

  assert.deepEqual(surface.volumeInfo?.volume, [320, 320, 320]);
  assert.deepEqual(surface.volumeInfo?.voxelsize, [0.8, 0.8, 0.8]);
  assert.deepEqual(surface.volumeInfo?.xras, [-1, 0, 0]);
  assert.deepEqual(surface.volumeInfo?.yras, [0, 0, -1]);
  assert.deepEqual(surface.volumeInfo?.zras, [0, 1, 0]);
  assert.deepEqual(surface.volumeInfo?.cras, [-0.1, 23.4, -25.6]);
});

void test('parseFreeSurferSurface keeps closed mesh normals outward', () => {
  const surface = parseFreeSurferSurface(makeSurface(
    [
      1, 1, 1,
      -1, -1, 1,
      -1, 1, -1,
      1, -1, -1,
    ],
    [
      0, 2, 1,
      0, 1, 3,
      0, 3, 2,
      1, 2, 3,
    ],
  ));

  for (let i = 0; i < surface.vertices.length; i += 3) {
    const vertex = [...surface.vertices.slice(i, i + 3)];
    const normal = [...surface.normals.slice(i, i + 3)];
    assert.ok(dot(vertex, normal) > 0.99, `normal ${normal.join(',')} should point outward from ${vertex.join(',')}`);
  }
});

void test('surface lights point toward the camera-facing side', () => {
  for (const direction of SURFACE_LIGHT_DIRECTIONS) {
    assert.ok(direction[2] < 0, `expected camera-side light z to be negative, got ${direction.join(',')}`);
    assert.ok(Math.hypot(...direction) > 0.01);
  }
});

void test('parseFreeSurferSurface rejects unsupported magic numbers', () => {
  const buffer = makeTriangleSurface();
  writeInt3(new DataView(buffer), 0, 12_345);

  assert.throws(
    () => parseFreeSurferSurface(buffer),
    /Unsupported FreeSurfer surface magic/,
  );
});

void test('parseFreeSurferCurvature reads new-format float curvature', () => {
  const values = parseFreeSurferCurvature(makeNewCurvature([-0.12, 0, 0.34]), 3);

  assert.equal(values.length, 3);
  assert.ok(Math.abs(values[0] + 0.12) < 1e-6);
  assert.equal(values[1], 0);
  assert.ok(Math.abs(values[2] - 0.34) < 1e-6);
});

void test('parseFreeSurferCurvature reads legacy scaled int curvature', () => {
  const values = parseFreeSurferCurvature(makeLegacyCurvature([-2, 0, 3]), 3);

  assert.deepEqual([...values], [-2, 0, 3]);
});

void test('parseFreeSurferCurvature rejects mismatched vertex counts', () => {
  assert.throws(
    () => parseFreeSurferCurvature(makeNewCurvature([0.1, 0.2]), 3),
    /does not match surface vertex count/,
  );
});

void test('parseFreeSurferAnnotation reads labels and color tables', () => {
  const annotation = parseFreeSurferAnnotation(makeAnnotation(), 3);

  assert.deepEqual([...annotation.labels], [-1, 1, 1]);
  assert.deepEqual([...annotation.colorTable.slice(4, 8)], [10, 20, 30, 255]);
  assert.equal(annotation.names[1], 'region');
});
