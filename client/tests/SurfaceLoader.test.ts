import assert from 'node:assert/strict';
import test from 'node:test';

import {
  freeSurferAnnotationToMz3,
  freeSurferCurvatureToMz3,
  parseFreeSurferAnnotation,
  parseFreeSurferCurvature,
} from '../src/utils/SurfaceLoader.js';

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

  for (const [index, name, rgba] of [
    [0, strings[1], [0, 0, 0, 255]],
    [1, strings[2], [10, 20, 30, 0]],
  ] as const) {
    view.setInt32(offset, index, false);
    offset += 4;
    bytes.set(name, offset);
    offset += name.length;
    for (const value of rgba) {
      view.setInt32(offset, value, false);
      offset += 4;
    }
  }
  return buffer;
}

function makeCurvature(values: number[]): ArrayBuffer {
  const buffer = new ArrayBuffer(15 + values.length * 4);
  const view = new DataView(buffer);
  view.setUint8(0, 255);
  view.setUint8(1, 255);
  view.setUint8(2, 255);
  view.setUint32(3, values.length, false);
  view.setUint32(7, 2, false);
  view.setUint32(11, 1, false);
  for (let index = 0; index < values.length; index += 1) {
    view.setFloat32(15 + index * 4, values[index], false);
  }
  return buffer;
}

void test('parseFreeSurferAnnotation reads labels and color tables', () => {
  const annotation = parseFreeSurferAnnotation(makeAnnotation(), 3);

  assert.deepEqual([...annotation.labels], [-1, 1, 1]);
  assert.deepEqual([...annotation.colorTable.slice(4, 8)], [10, 20, 30, 255]);
  assert.equal(annotation.names[1], 'region');
});

void test('freeSurferAnnotationToMz3 creates an opaque labeled mesh overlay', () => {
  const mz3 = freeSurferAnnotationToMz3(makeAnnotation(), 3);
  const view = new DataView(mz3);
  const colorMapLength = view.getUint32(12, true);
  const colorMap = JSON.parse(new TextDecoder().decode(mz3.slice(16, 16 + colorMapLength))) as {
    A: number[];
    I: number[];
    labels: string[];
  };
  const scalarOffset = 16 + colorMapLength;

  assert.equal(view.getUint16(0, true), 23_117);
  assert.equal(view.getUint16(2, true), 72);
  assert.deepEqual(colorMap.I, [0, 1, 2]);
  assert.deepEqual(colorMap.A, [0, 0, 255]);
  assert.deepEqual(colorMap.labels, ['Unassigned', 'unknown', 'region']);
  assert.deepEqual([
    view.getFloat32(scalarOffset, true),
    view.getFloat32(scalarOffset + 4, true),
    view.getFloat32(scalarOffset + 8, true),
  ], [0, 2, 2]);
});

void test('parseFreeSurferCurvature preserves signed scalar values', () => {
  const curvature = parseFreeSurferCurvature(makeCurvature([-0.35, 0, 0.25]), 3);

  assert.deepEqual([...curvature], [-0.3499999940395355, 0, 0.25]);
});

void test('freeSurferCurvatureToMz3 preserves the signed curvature scale', () => {
  const mz3 = freeSurferCurvatureToMz3(makeCurvature([-0.35, 0, 0.25]), 3);
  const view = new DataView(mz3);

  assert.equal(view.getUint16(0, true), 23_117);
  assert.equal(view.getUint16(2, true), 8);
  assert.equal(view.getUint32(4, true), 0);
  assert.equal(view.getUint32(8, true), 3);
  assert.deepEqual([
    view.getFloat32(16, true),
    view.getFloat32(20, true),
    view.getFloat32(24, true),
  ], [-0.3499999940395355, 0, 0.25]);
});
