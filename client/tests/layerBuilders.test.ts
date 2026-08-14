import assert from 'node:assert/strict';
import test from 'node:test';

import { createViewerLayer, outputVolumeLayerType } from '../src/utils/layerBuilders.js';
import type { OutputVolume } from '../src/types.js';

void test('createViewerLayer applies shared segmentation defaults', () => {
  const layer = createViewerLayer({
    filename: 'mri/aparc.DKTatlas+aseg.deep.mgz',
    url: '/api/app/cases/demo/files/aparc.DKTatlas+aseg.deep.mgz',
    type: 'segmentation',
  });

  assert.equal(layer.type, 'segmentation');
  assert.equal(layer.opacity, 0.7);
  assert.equal(layer.colormap, '');
  assert.equal(layer.visible, true);
  assert.equal(layer.lut, 'freesurfer');
});

void test('createViewerLayer applies shared surface defaults', () => {
  const layer = createViewerLayer({
    filename: 'surf/lh.pial',
    url: '/api/app/cases/demo/files/lh.pial',
    type: 'surface',
    visible: false,
  });

  assert.equal(layer.type, 'surface');
  assert.equal(layer.opacity, 1);
  assert.equal(layer.colormap, 'surface');
  assert.equal(layer.visible, false);
  assert.equal(layer.surfaceColorMode, 'solid');
});

void test('output volumes preserve their required declared layer type', () => {
  const segmentation: OutputVolume = {
    id: 'artifact-1',
    filename: 'mri/brainmask.mgz',
    downloadUrl: '/api/app/artifacts/artifact-1/download',
    kind: 'volume',
    type: 'segmentation',
  };

  assert.equal(outputVolumeLayerType(segmentation), 'segmentation');
});
