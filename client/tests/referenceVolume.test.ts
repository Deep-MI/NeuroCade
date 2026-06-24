import assert from 'node:assert/strict';
import test from 'node:test';

import type { Volume } from '../src/types.js';
import type { NiivueVolumeInterop } from '../src/utils/niivueInterop.js';
import {
  selectLoadedReferenceVolume,
  selectReferenceVolumeSource,
} from '../src/neurocadeViewer/referenceVolume.js';

function volume(id: string, visible: boolean, type: 'intensity' | 'segmentation' = 'intensity'): Volume {
  return {
    id,
    name: id,
    filename: `${id}.mgz`,
    url: `/${id}.mgz`,
    opacity: 1,
    colormap: 'gray',
    visible,
    type,
  };
}

void test('selectReferenceVolumeSource prefers the visible intensity layer', () => {
  const input = volume('input', false);
  const conform = volume('conform', true);
  const segmentation = volume('aseg', true, 'segmentation');

  assert.equal(selectReferenceVolumeSource([segmentation, input, conform]), conform);
});

void test('selectReferenceVolumeSource falls back to a visible non-surface layer', () => {
  const hiddenIntensity = volume('input', false);
  const segmentation = volume('aseg', true, 'segmentation');

  assert.equal(selectReferenceVolumeSource([hiddenIntensity, segmentation]), segmentation);
});

void test('selectLoadedReferenceVolume waits for the selected source instead of using a hidden loaded volume', () => {
  const input = volume('input', false);
  const conform = volume('conform', true);
  const loaded: NiivueVolumeInterop[] = [{ id: 'input', name: 'input.mgz', url: '/input.mgz' }];

  assert.equal(selectLoadedReferenceVolume(loaded, [input, conform]), null);
});

void test('selectLoadedReferenceVolume returns the newly visible loaded reference when available', () => {
  const input = volume('input', false);
  const conform = volume('conform', true);
  const loadedInput: NiivueVolumeInterop = { id: 'input', name: 'input.mgz', url: '/input.mgz' };
  const loadedConform: NiivueVolumeInterop = { id: 'conform', name: 'conform.mgz', url: '/conform.mgz' };

  assert.equal(selectLoadedReferenceVolume([loadedInput, loadedConform], [input, conform]), loadedConform);
});
