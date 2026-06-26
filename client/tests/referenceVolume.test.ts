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

void test('selectReferenceVolumeSource uses the lowest intensity underlay', () => {
  const input = volume('input', false);
  const conform = volume('conform', true);
  const segmentation = volume('aseg', true, 'segmentation');

  assert.equal(selectReferenceVolumeSource([segmentation, conform, input]), input);
  assert.equal(selectReferenceVolumeSource([input, segmentation]), input);
});

void test('selectReferenceVolumeSource can use a hidden lowest layer', () => {
  const hiddenIntensity = volume('input', false);
  const segmentation = volume('aseg', true, 'segmentation');

  assert.equal(selectReferenceVolumeSource([segmentation, hiddenIntensity]), hiddenIntensity);
});

void test('selectReferenceVolumeSource falls back to the lowest segmentation when no intensity is present', () => {
  const aseg = volume('aseg', true, 'segmentation');
  const mask = volume('mask', true, 'segmentation');

  assert.equal(selectReferenceVolumeSource([aseg, mask]), mask);
});

void test('selectLoadedReferenceVolume waits for the bottom selected source', () => {
  const input = volume('input', false);
  const conform = volume('conform', true);
  const loaded: NiivueVolumeInterop[] = [{ id: 'conform', name: 'conform.mgz', url: '/conform.mgz' }];

  assert.equal(selectLoadedReferenceVolume(loaded, [conform, input]), null);
});

void test('selectLoadedReferenceVolume returns the loaded bottom reference when available', () => {
  const input = volume('input', false);
  const conform = volume('conform', true);
  const loadedInput: NiivueVolumeInterop = { id: 'input', name: 'input.mgz', url: '/input.mgz' };
  const loadedConform: NiivueVolumeInterop = { id: 'conform', name: 'conform.mgz', url: '/conform.mgz' };

  assert.equal(selectLoadedReferenceVolume([loadedConform, loadedInput], [conform, input]), loadedInput);
});
