import assert from 'node:assert/strict';
import test from 'node:test';

import { labelInfoFromLut } from '../src/neurocadeViewer/labelLookup.js';
import { reorderLoadedVolumes, setLoadedVolumeOpacity } from '../src/neurocadeViewer/loadedVolumeDisplay.js';
import {
  applySegmentationRgbaRendering,
  buildSegmentationRgba,
  fallbackSegmentationColor,
  NIFTI_INTENT_NONE,
  RGBA32_DATATYPE_CODE,
} from '../src/neurocadeViewer/segmentationRgba.js';
import type { SegmentationVolumeLayer } from '../src/types.js';
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

void test('buildSegmentationRgba maps raw labels to voxel-exact RGBA colors', () => {
  const labelLut = {
    min: 1000,
    max: 1002,
    lut: new Uint8ClampedArray([
      10, 20, 30, 255,
      40, 50, 60, 128,
      70, 80, 90, 255,
    ]),
    labels: ['Region 1000', 'Region 1001', 'Region 1002'],
  };
  const fallback = fallbackSegmentationColor(77);

  const rgba = buildSegmentationRgba(new Uint16Array([0, 1001, 1002, 77]), 5, labelLut);

  assert.deepEqual([...rgba], [
    0, 0, 0, 0,
    40, 50, 60, 128,
    70, 80, 90, 255,
    ...fallback,
    0, 0, 0, 0,
  ]);
});

void test('applySegmentationRgbaRendering keeps raw labels for lookup and disables atlas rendering', () => {
  const labelLut = {
    min: 1,
    max: 2,
    lut: new Uint8ClampedArray([
      10, 20, 30, 255,
      40, 50, 60, 255,
    ]),
    labels: ['One', 'Two'],
  };
  const recoloredLabelLut = {
    ...labelLut,
    lut: new Uint8ClampedArray([
      90, 80, 70, 255,
      60, 50, 40, 255,
    ]),
  };
  const rawLabels = new Uint8Array([1, 2]);
  const loaded: NiivueVolumeInterop = {
    dims: [3, 2, 1, 1],
    img: rawLabels,
    colormapLabel: labelLut,
    hdr: {
      dims: [3, 2, 1, 1],
      datatypeCode: 2,
      intent_code: 1002,
      cal_min: 1,
      cal_max: 2,
    },
  };
  const source = { id: 'seg', type: 'segmentation' } as SegmentationVolumeLayer;

  applySegmentationRgbaRendering(loaded, source, labelLut);
  const renderedImage = loaded.img;

  assert.deepEqual([...(loaded.img as Uint8Array)], [
    10, 20, 30, 255,
    40, 50, 60, 255,
  ]);
  assert.equal(loaded.__rawLabelData, rawLabels);
  assert.deepEqual(loaded.__rawLabelDims, [2, 1, 1]);
  assert.equal(loaded.__rawLabelColormap, labelLut);
  assert.equal(loaded.colormapLabel, null);
  assert.equal(loaded.hdr?.datatypeCode, RGBA32_DATATYPE_CODE);
  assert.equal(loaded.hdr?.intent_code, NIFTI_INTENT_NONE);
  assert.equal(loaded.hdr?.cal_min, 0);
  assert.equal(loaded.hdr?.cal_max, 255);

  applySegmentationRgbaRendering(loaded, source, labelLut);
  assert.equal(loaded.img, renderedImage);

  applySegmentationRgbaRendering(loaded, source, recoloredLabelLut);
  assert.deepEqual([...(loaded.img as Uint8Array)], [
    90, 80, 70, 255,
    60, 50, 40, 255,
  ]);
  assert.equal(loaded.__rawLabelColormap, recoloredLabelLut);
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
