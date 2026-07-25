import assert from 'node:assert/strict';
import test from 'node:test';

import {
  referenceVoxelToWorld,
  reorderLoadedVolumes,
  setLoadedVolumeOpacity,
} from '../src/neurocadeViewer/loadedVolumeDisplay.js';
import type { NiivueVolumeInterop } from '../src/utils/niivueInterop.js';

void test('setLoadedVolumeOpacity stages an opacity change for a batched GPU refresh', () => {
  const loaded = { id: 'orig', opacity: 1 } as NiivueVolumeInterop;
  const nv = {
    volumes: [loaded],
  };

  const result = setLoadedVolumeOpacity(nv as never, loaded, 0.35);

  assert.equal(result, 'updated');
  assert.equal(loaded.opacity, 0.35);
  assert.equal(loaded.isDirty, true);
});

void test('reorderLoadedVolumes uses the NiiVue model ordering API', () => {
  const loadedOrig = { id: 'orig' } as NiivueVolumeInterop;
  const loadedSegTop = { id: 'seg-top' } as NiivueVolumeInterop;
  const loadedSegBottom = { id: 'seg-bottom' } as NiivueVolumeInterop;
  const volumes = [loadedSegTop, loadedOrig, loadedSegBottom];
  const nv = {
    volumes,
    model: {
      moveVolume: (from: number, to: number) => {
        const [volume] = volumes.splice(from, 1);
        volumes.splice(to, 0, volume);
        return true;
      },
    },
    updateGLVolume: () => Promise.resolve(),
  };

  const changed = reorderLoadedVolumes(nv as never, [loadedOrig, loadedSegBottom, loadedSegTop]);

  assert.equal(changed, true);
  assert.deepEqual(nv.volumes.map((volume) => volume.id), ['orig', 'seg-bottom', 'seg-top']);
});

void test('reorderLoadedVolumes preserves the crosshair in world coordinates', () => {
  const loadedInput = { id: 'input' } as NiivueVolumeInterop;
  const loadedOrig = { id: 'orig' } as NiivueVolumeInterop;
  const volumes = [loadedInput, loadedOrig];
  const restored: number[][] = [];
  const nv = {
    volumes,
    getCrosshairPos: () => [12, -34, 56],
    setCrosshairPos: (position: number[]) => restored.push(position),
    model: {
      moveVolume: (from: number, to: number) => {
        const [volume] = volumes.splice(from, 1);
        volumes.splice(to, 0, volume);
        return true;
      },
    },
  };

  reorderLoadedVolumes(nv as never, [loadedOrig, loadedInput]);

  assert.deepEqual(restored, [[12, -34, 56]]);
});

void test('referenceVoxelToWorld applies the base volume RAS affine', () => {
  const nv = {
    volumes: [{
      matRAS: [
        0, 2, 0, 10,
        -3, 0, 0, 20,
        0, 0, 4, 30,
        0, 0, 0, 1,
      ],
    }],
  };

  assert.deepEqual(referenceVoxelToWorld(nv as never, [1, 2, 3]), [14, 17, 42]);
});
