import assert from 'node:assert/strict';
import test from 'node:test';
import { DRAG_MODE } from '@niivue/niivue';

import {
  inPlaneCrosshairDelta,
  niivueDragMode,
  planeAtCanvasPosition,
  throughPlaneCrosshairDelta,
} from '../src/neurocadeViewer/viewerControls.js';

void test('window/level uses NiiVue region contrast', () => {
  assert.equal(niivueDragMode('contrast'), DRAG_MODE.contrast);
  assert.notEqual(niivueDragMode('contrast'), DRAG_MODE.windowing);
  assert.equal(niivueDragMode('pan'), DRAG_MODE.pan);
  assert.equal(niivueDragMode('measurement'), DRAG_MODE.measurement);
});

void test('arrow navigation follows each anatomical plane', () => {
  assert.deepEqual(inPlaneCrosshairDelta(0, 'ArrowUp'), [0, 1, 0]);
  assert.deepEqual(inPlaneCrosshairDelta(0, 'ArrowRight'), [1, 0, 0]);

  assert.deepEqual(inPlaneCrosshairDelta(1, 'ArrowUp'), [0, 0, 1]);
  assert.deepEqual(inPlaneCrosshairDelta(1, 'ArrowRight'), [1, 0, 0]);

  assert.deepEqual(inPlaneCrosshairDelta(2, 'ArrowUp'), [0, 0, 1]);
  assert.deepEqual(inPlaneCrosshairDelta(2, 'ArrowRight'), [0, 1, 0]);
  for (const plane of [0, 1, 2] as const) {
    const up = inPlaneCrosshairDelta(plane, 'ArrowUp');
    const down = inPlaneCrosshairDelta(plane, 'ArrowDown');
    const right = inPlaneCrosshairDelta(plane, 'ArrowRight');
    const left = inPlaneCrosshairDelta(plane, 'ArrowLeft');
    assert.deepEqual(down, up.map((value) => value === 0 ? 0 : -value));
    assert.deepEqual(left, right.map((value) => value === 0 ? 0 : -value));
  }
  assert.deepEqual(throughPlaneCrosshairDelta(0, 'ArrowUp'), [0, 0, 1]);
  assert.deepEqual(throughPlaneCrosshairDelta(0, 'ArrowDown'), [0, 0, -1]);
  assert.deepEqual(throughPlaneCrosshairDelta(1, 'ArrowUp'), [0, 1, 0]);
  assert.deepEqual(throughPlaneCrosshairDelta(1, 'ArrowDown'), [0, -1, 0]);
  assert.deepEqual(throughPlaneCrosshairDelta(2, 'ArrowUp'), [1, 0, 0]);
  assert.deepEqual(throughPlaneCrosshairDelta(2, 'ArrowDown'), [-1, 0, 0]);
});

void test('canvas hit testing selects only 2D grid tiles', () => {
  const slices = [
    { axCorSag: 0, leftTopWidthHeight: [0, 0, 100, 100] },
    { axCorSag: 1, leftTopWidthHeight: [100, 0, 100, 100] },
    { axCorSag: 2, leftTopWidthHeight: [0, 100, 100, 100] },
    { axCorSag: 4, leftTopWidthHeight: [100, 100, 100, 100] },
  ];

  assert.equal(planeAtCanvasPosition(slices, 50, 50), 0);
  assert.equal(planeAtCanvasPosition(slices, 150, 50), 1);
  assert.equal(planeAtCanvasPosition(slices, 50, 150), 2);
  assert.equal(planeAtCanvasPosition(slices, 150, 150), null);
  assert.equal(planeAtCanvasPosition(slices, 250, 250), null);
});
