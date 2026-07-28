import assert from 'node:assert/strict';
import test from 'node:test';

import { createFixedReferenceGrid } from '../src/neurocadeViewer/fixedReference.js';
import type { NiivueVolumeInterop } from '../src/utils/niivueInterop.js';

function loaded(
  id: string,
  minimum: [number, number, number],
  maximum: [number, number, number],
  spacing: [number, number, number] = [1, 2, 3],
): NiivueVolumeInterop {
  return {
    id,
    name: id,
    url: `/${id}`,
    extentsMin: minimum,
    extentsMax: maximum,
    pixDimsRAS: [1, ...spacing],
  } as NiivueVolumeInterop;
}

void test('fixed reference grid covers the union of loaded volume bounds', () => {
  const grid = createFixedReferenceGrid([
    loaded('first', [-10, -20, -30], [10, 20, 30]),
    loaded('second', [-20, -10, -5], [5, 30, 15]),
  ]);
  const header = new DataView(grid.buffer);

  assert.deepEqual(grid.dimensions, [30, 25, 20]);
  assert.deepEqual(grid.spacing, [1, 2, 3]);
  assert.deepEqual(grid.affine, [
    [1, 0, 0, -19.5],
    [0, 2, 0, -19],
    [0, 0, 3, -28.5],
  ]);
  assert.equal(header.getInt32(0, true), 348);
  assert.equal(header.getInt16(70, true), 2);
  assert.equal(header.getInt16(72, true), 8);
  assert.equal(grid.buffer.byteLength, 352 + 30 * 25 * 20);
});

void test('fixed reference grid stays lightweight for very large fields of view', () => {
  const grid = createFixedReferenceGrid([
    loaded('large', [-1000, -800, -600], [1000, 800, 600]),
  ]);
  const voxelCount = grid.dimensions.reduce((product, dimension) => product * dimension, 1);

  assert.ok(grid.dimensions.every((dimension) => dimension <= 512));
  assert.ok(voxelCount <= 64 * 1024 * 1024);
  assert.ok(grid.spacing.every((spacing, axis) => spacing > [1, 2, 3][axis]));
});

void test('fixed reference grid requires finite three-dimensional bounds', () => {
  assert.throws(
    () => createFixedReferenceGrid([{
      id: 'invalid',
      extentsMin: [0, 0, Number.NaN],
      extentsMax: [1, 1, 1],
    } as NiivueVolumeInterop]),
    /finite volume bounds/,
  );
});
