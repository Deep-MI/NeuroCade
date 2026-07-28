import assert from 'node:assert/strict';
import test from 'node:test';

import { parseEditableSliderValue } from '../src/neurocadeViewer/layerDisplay.js';

void test('editable slider values reject blank and non-finite input', () => {
  assert.equal(parseEditableSliderValue('', 0, 100, true), null);
  assert.equal(parseEditableSliderValue('   ', 0, 100, true), null);
  assert.equal(parseEditableSliderValue('not-a-number', 0, 100, true), null);
  assert.equal(parseEditableSliderValue('Infinity', 0, 100, true), null);
});

void test('editable slider values honor optional range constraints', () => {
  assert.equal(parseEditableSliderValue('125', 0, 100, true), 100);
  assert.equal(parseEditableSliderValue('-25', 0, 100, true), 0);
  assert.equal(parseEditableSliderValue('125', 0, 100, false), 125);
});
