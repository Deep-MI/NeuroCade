import assert from 'node:assert/strict';
import test from 'node:test';

import { reorderLoadedVolumes, setLoadedVolumeOpacity } from '../src/neurocadeViewer/loadedVolumeDisplay.js';
import type { NiivueVolumeInterop } from '../src/utils/niivueInterop.js';

void test('setLoadedVolumeOpacity uses the public NiiVue volume update API', () => {
  const loaded = { id: 'orig', opacity: 1 } as NiivueVolumeInterop;
  const updates: { index: number; opacity?: number }[] = [];
  const nv = {
    volumes: [loaded],
    setVolume: (index: number, update: { opacity?: number }) => {
      updates.push({ index, ...update });
      return Promise.resolve();
    },
  };

  const result = setLoadedVolumeOpacity(nv as never, loaded, 0.35);

  assert.equal(result, 'updated');
  assert.equal(loaded.opacity, 0.35);
  assert.deepEqual(updates, [{ index: 0, opacity: 0.35 }]);
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
