import assert from 'node:assert/strict';
import test from 'node:test';

import { configuredOutputLayerType } from '../src/utils/artifactOutputs.js';

void test('configured workflow output types control viewer loading', () => {
  assert.equal(configuredOutputLayerType('intensity_volume'), 'intensity');
  assert.equal(configuredOutputLayerType('segmentation_volume'), 'segmentation');
  assert.equal(configuredOutputLayerType('surface'), 'surface');
  assert.equal(configuredOutputLayerType('other'), null);
  assert.equal(configuredOutputLayerType(undefined), undefined);
});
