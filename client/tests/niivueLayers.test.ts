import assert from 'node:assert/strict';
import test from 'node:test';

import { labelInfoFromLut } from '../src/neurocadeViewer/labelLookup.js';

void test('labelInfoFromLut resolves label names from LUTs with non-zero minimum labels', () => {
  const labelLut = {
    min: 1000,
    max: 1002,
    lut: new Uint8ClampedArray([
      10, 20, 30, 255,
      40, 50, 60, 255,
      70, 80, 90, 255,
    ]),
    labels: ['Region 1000', 'Region 1001', 'Region 1002'],
  };

  const label = labelInfoFromLut(1001, labelLut);

  assert.deepEqual(label, {
    index: 1001,
    name: 'Region 1001',
    color: [40, 50, 60],
  });
});
