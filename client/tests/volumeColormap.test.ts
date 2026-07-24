import assert from 'node:assert/strict';
import test from 'node:test';

import { parseLUT } from '../src/utils/LutParser.js';
import { lutMapToNiivueColorMap } from '../src/utils/niivueColorMap.js';

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
