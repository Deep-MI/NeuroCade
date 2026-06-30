import assert from 'node:assert/strict';
import test from 'node:test';

import { labelInfoFromLut } from '../src/neurocadeViewer/labelLookup.js';
import { reorderLoadedVolumes, setLoadedVolumeOpacity } from '../src/neurocadeViewer/loadedVolumeDisplay.js';
import type { NiivueVolumeInterop } from '../src/utils/niivueInterop.js';

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

void test('setLoadedVolumeOpacity mutates loaded volume without forcing an immediate GL refresh', () => {
  let refreshes = 0;
  const loaded: NiivueVolumeInterop = { id: 'orig', opacity: 1 };
  const nv = {
    volumes: [loaded],
    getVolumeIndexByID: (id: string) => id === 'orig' ? 0 : -1,
    setOpacity: () => {
      throw new Error('setOpacity should not be used for deferred opacity changes');
    },
    updateGLVolume: () => {
      refreshes += 1;
    },
  };

  const result = setLoadedVolumeOpacity(nv as never, loaded, 0.35);

  assert.equal(result, 'mutated');
  assert.equal(loaded.opacity, 0.35);
  assert.equal(refreshes, 0);
});

void test('reorderLoadedVolumes updates the NiiVue stack without forcing an immediate GL refresh', () => {
  const loadedOrig: NiivueVolumeInterop = { id: 'orig' };
  const loadedSegTop: NiivueVolumeInterop = { id: 'seg-top' };
  const loadedSegBottom: NiivueVolumeInterop = { id: 'seg-bottom' };
  const nv = {
    volumes: [loadedSegTop, loadedOrig, loadedSegBottom],
    back: loadedSegTop,
    overlays: [loadedOrig, loadedSegBottom],
    moveVolumeUp: () => {
      throw new Error('moveVolumeUp should not be used for deferred reorders');
    },
    moveVolumeDown: () => {
      throw new Error('moveVolumeDown should not be used for deferred reorders');
    },
    updateGLVolume: () => {
      throw new Error('updateGLVolume should be scheduled by the caller');
    },
  };

  const changed = reorderLoadedVolumes(nv as never, [loadedOrig, loadedSegBottom, loadedSegTop]);

  assert.equal(changed, true);
  assert.deepEqual(nv.volumes.map((volume) => volume.id), ['orig', 'seg-bottom', 'seg-top']);
  assert.equal(nv.back, loadedOrig);
  assert.deepEqual(nv.overlays, [loadedSegBottom, loadedSegTop]);
});
