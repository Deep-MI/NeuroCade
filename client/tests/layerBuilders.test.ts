import assert from 'node:assert/strict';
import test from 'node:test';

import { createViewerLayer, outputVolumeLayerType, refreshViewerLayer } from '../src/utils/layerBuilders.js';
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


void test('refresh retains segmentation display settings and honors an explicit name', () => {
  const source = { filename: 'brainstemSsLabels.v13.mgz', url: '/old', type: 'segmentation' as const, lut: 'freesurfer' };
  const existing = { ...createViewerLayer({ ...source, name: 'My segmentation' }), opacity: 0.4, visible: false };
  const loaded = createViewerLayer({ ...source, url: '/new', name: 'Brainstem', artifactId: 'artifact-1' });
  const refreshed = refreshViewerLayer(existing, loaded, { name: 'Brainstem' });
  assert.equal(refreshed.name, 'Brainstem');
  assert.equal(refreshed.type, 'segmentation');
  assert.equal(refreshed.opacity, 0.4);
  assert.equal(refreshed.visible, false);
  assert.equal(refreshed.lut, 'freesurfer');
  assert.equal(refreshed.url, '/new');
  assert.equal(refreshed.artifactId, 'artifact-1');
  assert.equal(refreshViewerLayer(existing, loaded, {}).name, 'My segmentation');
});

void test('registered segmentation repairs a previously misclassified intensity layer', () => {
  const source = { filename: 'brainstemSsLabels.v13.mgz', url: '/volume' };
  const existing = createViewerLayer({ ...source, type: 'intensity' });
  const loaded = createViewerLayer({ ...source, type: 'segmentation', name: 'Brainstem' });
  const repaired = refreshViewerLayer(existing, loaded, {});
  assert.equal(repaired.type, 'segmentation');
  assert.equal(repaired.name, 'Brainstem');
  assert.equal(repaired.opacity, 0.7);
  assert.equal(repaired.lut, 'freesurfer');
});
