import assert from 'node:assert/strict';
import test from 'node:test';

import { restorePersistedCaseLayers } from '../src/utils/caseLayerPersistence.js';
import type { Volume } from '../src/types.js';

const storage = new Map<string, string>();

globalThis.localStorage = {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => { storage.set(key, value); },
  removeItem: (key: string) => { storage.delete(key); },
  clear: () => { storage.clear(); },
  key: (index: number) => [...storage.keys()][index] ?? null,
  get length() { return storage.size; },
};

function intensity(id: string): Volume {
  return {
    id,
    filename: id,
    name: id,
    url: `/${id}`,
    type: 'intensity',
    visible: true,
    opacity: 1,
    colormap: 'gray',
    brightness: 0,
    contrast: 1,
  };
}

void test('restorePersistedCaseLayers restores order and display settings', () => {
  storage.clear();
  storage.set('fastsurfer-case-demo', JSON.stringify({
    volumes: [
      { ...intensity('b.mgz'), visible: false, opacity: 0.4, brightness: 10, contrast: 2 },
      { ...intensity('a.mgz'), visible: true, opacity: 1, brightness: 0, contrast: 1 },
    ],
  }));

  const restored = restorePersistedCaseLayers('demo', [intensity('a.mgz'), intensity('b.mgz')]);

  assert.deepEqual(restored.map((volume) => volume.id), ['b.mgz', 'a.mgz']);
  assert.equal(restored[0].visible, false);
  assert.equal(restored[0].opacity, 0.4);
  assert.equal('brightness' in restored[0] ? restored[0].brightness : undefined, 10);
  assert.equal('contrast' in restored[0] ? restored[0].contrast : undefined, 2);
});
