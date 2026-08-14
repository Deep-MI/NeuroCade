import assert from 'node:assert/strict';
import test from 'node:test';

import { mergeOutputVolumesIntoViewerLayers, outputVolumesToViewerLayers } from '../src/utils/caseLayers.js';
import { orderedReferenceCandidate } from '../src/neurocadeViewer/layerDisplay.js';
import type { OutputVolume } from '../src/types.js';

function intensity(filename: string): OutputVolume {
  return {
    id: filename,
    filename,
    downloadUrl: `/${filename}`,
    kind: 'volume',
    type: 'intensity',
  };
}

void test('output volume display names do not replace physical filenames', () => {
  const [layer] = outputVolumesToViewerLayers([{
    ...intensity('aparc.DKTatlas+aseg.deep.mgz'),
    name: 'Baseline segmentation',
  }]);

  assert.equal(layer.name, 'Baseline segmentation');
  assert.equal(layer.filename, 'aparc.DKTatlas+aseg.deep.mgz');
});

void test('the first supplied intensity is the ordered reference candidate without filename preferences', () => {
  const first = intensity('scan-a.mgz');
  const orig = intensity('orig.mgz');

  const layers = outputVolumesToViewerLayers([first, orig]);

  assert.deepEqual(layers.map((volume) => volume.id), ['orig.mgz', 'scan-a.mgz']);
  assert.equal(orderedReferenceCandidate(layers)?.id, 'scan-a.mgz');
  assert.equal(layers.find((volume) => volume.id === 'scan-a.mgz')?.visible, true);
  assert.equal(layers.find((volume) => volume.id === 'orig.mgz')?.visible, false);
});

void test('converting output order does not mutate the API response', () => {
  const outputs = [intensity('first.mgz'), intensity('second.mgz')];

  outputVolumesToViewerLayers(outputs);

  assert.deepEqual(outputs.map((volume) => volume.id), ['first.mgz', 'second.mgz']);
});

void test('live output polling adds declared layers without resetting viewer state', () => {
  const initial = outputVolumesToViewerLayers([intensity('input.mgz')]);
  initial[0].visible = false;
  initial[0].opacity = 0.4;
  const segmentation: OutputVolume = {
    id: 'segmentation',
    filename: 'result.mgz',
    downloadUrl: '/result.mgz',
    kind: 'volume',
    outputType: 'segmentation_volume',
    type: 'segmentation',
    lut: 'binary',
    visible: true,
  };

  const merged = mergeOutputVolumesIntoViewerLayers(initial, [intensity('input.mgz'), segmentation]);

  const preservedInput = merged.find((layer) => layer.filename === 'input.mgz');
  const newSegmentation = merged.find((layer) => layer.filename === 'result.mgz');
  assert.equal(preservedInput?.visible, false);
  assert.equal(preservedInput?.opacity, 0.4);
  assert.equal(newSegmentation?.type, 'segmentation');
  assert.equal(newSegmentation?.visible, true);
});
