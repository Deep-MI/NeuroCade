import assert from 'node:assert/strict';
import test from 'node:test';

import type { SegmentationVolumeLayer } from '../src/types.js';
import {
  MAX_DRAWING_UNDO,
  drawingSourceFromSegmentation,
  filenameForSegmentationDrawing,
  inferSavedDrawingLut,
  makeDrawingFilename,
  maxDrawingValue,
  popUndoBitmap,
  pushUndoBitmap,
} from '../src/neurocadeViewer/nativeDrawingHelpers.js';

void test('makeDrawingFilename keeps .nii and normalizes other extensions', () => {
  assert.equal(makeDrawingFilename('seg.nii'), 'seg.nii');
  assert.equal(makeDrawingFilename('SEG.NII'), 'SEG.NII');
  assert.equal(makeDrawingFilename('seg.nii.gz'), 'seg.nii');
  assert.equal(makeDrawingFilename('seg.mgz'), 'seg.nii');
  assert.equal(makeDrawingFilename('seg.mgh'), 'seg.nii');
  assert.equal(makeDrawingFilename('seg'), 'seg.nii');
  assert.equal(makeDrawingFilename('  '), 'drawing.nii');
  assert.equal(makeDrawingFilename('  trimmed  '), 'trimmed.nii');
});

void test('filenameForSegmentationDrawing derives a drawing- prefixed .nii name', () => {
  const source = { filename: 'aseg.mgz', name: 'aseg' } as SegmentationVolumeLayer;
  assert.equal(filenameForSegmentationDrawing(source), 'drawing-aseg.nii');
});

void test('maxDrawingValue finds the largest label', () => {
  assert.equal(maxDrawingValue(new Uint8Array([0, 0, 1, 0])), 1);
  assert.equal(maxDrawingValue(new Uint8Array([0, 5, 2, 17])), 17);
  assert.equal(maxDrawingValue(new Uint8Array()), 0);
  assert.equal(maxDrawingValue(null), 0);
});

void test('inferSavedDrawingLut prefers the source LUT, then label range', () => {
  const seg = { lut: 'freesurfer' } as ReturnType<typeof drawingSourceFromSegmentation>;
  assert.equal(inferSavedDrawingLut(new Uint8Array([0, 1]), seg), 'freesurfer');
  assert.equal(inferSavedDrawingLut(new Uint8Array([0, 1])), 'binary');
  assert.equal(inferSavedDrawingLut(new Uint8Array([0, 1, 4])), 'freesurfer');
  assert.equal(inferSavedDrawingLut(null), 'binary');
});

void test('drawingSourceFromSegmentation carries colormap and identity fields', () => {
  const layer = {
    id: 'layer-1',
    artifactId: 'art-1',
    name: 'aseg',
    filename: 'aseg.mgz',
    url: '/aseg.mgz',
    lut: 'freesurfer',
    colormap: 'freesurfer',
  } as SegmentationVolumeLayer;
  assert.deepEqual(drawingSourceFromSegmentation(layer), {
    layerId: 'layer-1',
    artifactId: 'art-1',
    name: 'aseg',
    filename: 'aseg.mgz',
    url: '/aseg.mgz',
    lut: 'freesurfer',
    colormap: 'freesurfer',
  });
});

void test('pushUndoBitmap appends without mutating the input stack', () => {
  const base = new Uint8Array([0]);
  const stack: Uint8Array[] = [base];
  const next = pushUndoBitmap(stack, new Uint8Array([1]));
  assert.equal(stack.length, 1, 'original stack is not mutated');
  assert.equal(next.length, 2);
  assert.equal(next[0], base, 'baseline entry is retained by reference');
});

void test('pushUndoBitmap caps the history at the maximum depth', () => {
  let stack: Uint8Array[] = [];
  for (let i = 0; i < MAX_DRAWING_UNDO + 5; i += 1) {
    stack = pushUndoBitmap(stack, new Uint8Array([i]));
  }
  assert.equal(stack.length, MAX_DRAWING_UNDO);
  // Oldest entries dropped: last entry is the most recently pushed value.
  assert.equal(stack[stack.length - 1][0], MAX_DRAWING_UNDO + 4);
});

void test('popUndoBitmap removes the latest entry and reports the new current', () => {
  const a = new Uint8Array([0]);
  const b = new Uint8Array([1]);
  const c = new Uint8Array([2]);
  const result = popUndoBitmap([a, b, c]);
  assert.deepEqual(result.stack, [a, b]);
  assert.equal(result.current, b);
});

void test('popUndoBitmap is a no-op at the baseline entry', () => {
  const a = new Uint8Array([0]);
  const stack = [a];
  const result = popUndoBitmap(stack);
  assert.equal(result.stack, stack, 'returns the same stack reference to signal no-op');
  assert.equal(result.current, a);
});
