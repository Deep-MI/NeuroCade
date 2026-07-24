import assert from 'node:assert/strict';
import test from 'node:test';

import { inPlaneCrosshairDelta } from '../src/neurocadeViewer/viewerControls.js';

void test('arrow navigation stays within the active anatomical plane', () => {
  assert.deepEqual(inPlaneCrosshairDelta(0, 'ArrowUp'), [0, 1, 0]);
  assert.deepEqual(inPlaneCrosshairDelta(0, 'ArrowRight'), [1, 0, 0]);

  assert.deepEqual(inPlaneCrosshairDelta(1, 'ArrowUp'), [0, 0, 1]);
  assert.deepEqual(inPlaneCrosshairDelta(1, 'ArrowRight'), [1, 0, 0]);

  assert.deepEqual(inPlaneCrosshairDelta(2, 'ArrowUp'), [0, 0, 1]);
  assert.deepEqual(inPlaneCrosshairDelta(2, 'ArrowRight'), [0, 1, 0]);
});

void test('opposite arrow keys produce opposite deltas', () => {
  for (const plane of [0, 1, 2] as const) {
    const up = inPlaneCrosshairDelta(plane, 'ArrowUp');
    const down = inPlaneCrosshairDelta(plane, 'ArrowDown');
    const right = inPlaneCrosshairDelta(plane, 'ArrowRight');
    const left = inPlaneCrosshairDelta(plane, 'ArrowLeft');
    assert.deepEqual(down, up.map((value) => value === 0 ? 0 : -value));
    assert.deepEqual(left, right.map((value) => value === 0 ? 0 : -value));
  }
});
