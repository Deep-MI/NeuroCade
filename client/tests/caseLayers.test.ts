import assert from 'node:assert/strict';
import test from 'node:test';

import { outputVolumesToViewerLayers } from '../src/utils/caseLayers.js';
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
