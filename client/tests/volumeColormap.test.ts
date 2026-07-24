import assert from 'node:assert/strict';
import test from 'node:test';

import { parseLUT } from '../src/utils/LutParser.js';
import {
  compileNiivueLabelColorMap,
  lutMapToNiivueColorMap,
} from '../src/utils/niivueColorMap.js';

void test('FreeSurfer LUT transparency is converted to NiiVue alpha', () => {
  const lut = parseLUT([
    '0 Unknown 0 0 0 0',
    '2 Left-Cerebral-White-Matter 245 245 245 0',
    '902 Artery 204 0 0 255',
  ].join('\n'));
  const colorMap = lutMapToNiivueColorMap(lut);

  assert.deepEqual(colorMap.I, [0, 2, 902]);
  assert.deepEqual(colorMap.A, [0, 255, 0]);
  assert.deepEqual(colorMap.R, [0, 245, 204]);
});

void test('compiled NiiVue label LUT preserves sparse FreeSurfer indices', () => {
  const lut = compileNiivueLabelColorMap({
    R: [0, 230, 70],
    G: [0, 148, 130],
    B: [0, 34, 180],
    A: [0, 255, 255],
    I: [0, 8, 10],
    labels: ['Unknown', 'Left-Cerebellum-Cortex', 'Left-Thalamus'],
  });

  assert.equal(lut.min, 0);
  assert.equal(lut.max, 10);
  assert.deepEqual([...lut.lut.slice(8 * 4, 8 * 4 + 4)], [230, 148, 34, 255]);
  assert.equal(lut.labels?.[8], 'Left-Cerebellum-Cortex');
  assert.equal(lut.labels?.[9], '?');
});
