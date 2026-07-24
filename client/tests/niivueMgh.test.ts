import assert from 'node:assert/strict';
import test from 'node:test';

import { prepareNiivueVolumeInline } from '../src/utils/niivueMghCore.js';

function makeInt16Mgh(): ArrayBuffer {
  const buffer = new ArrayBuffer(284 + 4);
  const view = new DataView(buffer);
  view.setInt32(0, 1, false);
  view.setInt32(4, 2, false);
  view.setInt32(8, 1, false);
  view.setInt32(12, 1, false);
  view.setInt32(16, 1, false);
  view.setInt32(20, 4, false);
  view.setFloat32(30, 1, false);
  view.setFloat32(34, 2, false);
  view.setFloat32(38, 3, false);
  view.setFloat32(42, 1, false);
  view.setFloat32(58, 1, false);
  view.setFloat32(74, 1, false);
  view.setInt16(284, 1, false);
  view.setInt16(286, -2, false);
  return buffer;
}

void test('prepareNiivueVolume converts big-endian MGH voxels to little-endian NIfTI', async () => {
  const prepared = await prepareNiivueVolumeInline(makeInt16Mgh(), 'brain.mgh');
  const view = new DataView(prepared.buffer);

  assert.equal(prepared.filename, 'brain.nii');
  assert.equal(view.getInt32(0, true), 348);
  assert.deepEqual([
    view.getInt16(42, true),
    view.getInt16(44, true),
    view.getInt16(46, true),
  ], [2, 1, 1]);
  assert.equal(view.getInt16(70, true), 4);
  assert.equal(view.getInt16(72, true), 16);
  assert.equal(view.getFloat32(80, true), 1);
  assert.equal(view.getFloat32(84, true), 2);
  assert.equal(view.getFloat32(88, true), 3);
  assert.equal(view.getInt16(352, true), 1);
  assert.equal(view.getInt16(354, true), -2);
});

void test('prepareNiivueVolume leaves non-MGH volumes unchanged', async () => {
  const buffer = new ArrayBuffer(8);
  const prepared = await prepareNiivueVolumeInline(buffer, 'brain.nii.gz');

  assert.equal(prepared.buffer, buffer);
  assert.equal(prepared.filename, 'brain.nii.gz');
});
