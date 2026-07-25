import assert from 'node:assert/strict';
import test from 'node:test';

import type { SegmentationVolumeLayer } from '../src/types.js';
import {
  drawingSourceFromSegmentation,
  filenameForSegmentationDrawing,
  makeDrawingFilename,
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
