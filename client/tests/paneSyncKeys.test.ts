import assert from 'node:assert/strict';
import test from 'node:test';

import {
  layerReconcileKeyOf,
  surfaceAppearanceKeyOf,
  surfaceVisibilityKeyOf,
  volumeAppearanceKeyOf,
  volumeDisplayKeyOf,
  volumeStackKeyOf,
  windowingKeyOf,
} from '../src/neurocadeViewer/paneSyncKeys.js';
import {
  orderedReferenceCandidate,
  volumesInRenderOrder,
} from '../src/neurocadeViewer/layerDisplay.js';
import type { Volume } from '../src/types.js';

const intensity = {
  id: 'orig.mgz',
  filename: 'orig.mgz',
  name: 'Orig',
  url: '/orig.mgz',
  type: 'intensity',
  visible: true,
  opacity: 1,
  colormap: 'gray',
  brightness: 0,
  contrast: 1,
} satisfies Volume;

const segmentation = {
  id: 'aseg.mgz',
  filename: 'aseg.mgz',
  name: 'Aseg',
  url: '/aseg.mgz',
  type: 'segmentation',
  visible: true,
  opacity: 0.7,
  colormap: '',
  lut: 'freesurfer',
  brightness: 0,
  contrast: 1,
} satisfies Volume;

const surface = {
  id: 'lh.pial',
  filename: 'lh.pial',
  name: 'Left pial',
  url: '/lh.pial',
  type: 'surface',
  visible: true,
  opacity: 1,
  colormap: 'surface',
  surfaceColorMode: 'curvature',
  curvatureUrl: '/lh.curv',
  curvatureNegativeThreshold: 0.2,
  curvaturePositiveThreshold: 0.2,
} satisfies Volume;

void test('pane sync keys isolate source, visibility, appearance, and ordering concerns', () => {
  assert.match(layerReconcileKeyOf([intensity]), /orig\.mgz:\/orig\.mgz/);
  assert.equal(layerReconcileKeyOf([intensity]), layerReconcileKeyOf([{ ...intensity, visible: false }]));
  assert.match(layerReconcileKeyOf([surface]), /lh\.pial:\/lh\.pial/);
  assert.notEqual(layerReconcileKeyOf([surface]), layerReconcileKeyOf([{ ...surface, visible: false }]));
  assert.notEqual(surfaceVisibilityKeyOf([surface]), surfaceVisibilityKeyOf([{ ...surface, opacity: 0.5 }]));
  assert.equal(surfaceVisibilityKeyOf([intensity]), surfaceVisibilityKeyOf([{ ...intensity, opacity: 0.5 }]));
  assert.notEqual(volumeAppearanceKeyOf([intensity]), volumeAppearanceKeyOf([{ ...intensity, colormap: 'hot' }]));
  assert.equal(volumeStackKeyOf([intensity, segmentation]), 'orig.mgz|aseg.mgz');
  assert.equal(
    volumeStackKeyOf([
      { ...segmentation, id: 'seg-top', filename: 'seg-top.mgz' },
      intensity,
      { ...segmentation, id: 'seg-bottom', filename: 'seg-bottom.mgz' },
    ]),
    'orig.mgz|seg-bottom|seg-top',
  );
  assert.equal(volumeStackKeyOf([intensity]), volumeStackKeyOf([{ ...intensity, visible: false }]));
  assert.equal(volumeStackKeyOf([segmentation]), volumeStackKeyOf([{ ...segmentation, visible: false }]));
  assert.equal(volumeStackKeyOf([intensity]), volumeStackKeyOf([{ ...intensity, opacity: 0.5 }]));
  assert.notEqual(volumeDisplayKeyOf([segmentation]), volumeDisplayKeyOf([{ ...segmentation, visible: false }]));
  assert.notEqual(volumeDisplayKeyOf([intensity]), volumeDisplayKeyOf([{ ...intensity, opacity: 0.5 }]));
  assert.notEqual(volumeDisplayKeyOf([intensity]), volumeDisplayKeyOf([{ ...intensity, visible: false }]));
});

void test('the bottom intensity layer is the coordinate source below overlays', () => {
  const input = {
    ...intensity,
    id: 'input.mgz',
    filename: 'input.mgz',
    name: 'Input',
    url: '/input.mgz',
    visible: false,
  };
  const sources = [segmentation, intensity, input];

  assert.deepEqual(
    volumesInRenderOrder(sources).map((volume) => volume.id),
    ['input.mgz', 'orig.mgz', 'aseg.mgz'],
  );
  assert.equal(orderedReferenceCandidate(sources)?.id, 'input.mgz');
});

void test('visibility does not change the reference but reorder and removal do', () => {
  const input = {
    ...intensity,
    id: 'input.mgz',
    filename: 'input.mgz',
    name: 'Input',
    url: '/input.mgz',
  };

  assert.equal(orderedReferenceCandidate([intensity, input])?.id, 'input.mgz');
  assert.equal(orderedReferenceCandidate([intensity, { ...input, visible: false }])?.id, 'input.mgz');
  assert.equal(orderedReferenceCandidate([input, intensity])?.id, 'orig.mgz');
  assert.equal(orderedReferenceCandidate([intensity])?.id, 'orig.mgz');
});

void test('pane sync keys include surface display fields', () => {
  assert.notEqual(
    surfaceAppearanceKeyOf([surface]),
    surfaceAppearanceKeyOf([{ ...surface, curvaturePositiveThreshold: 0.4 }]),
  );
});

void test('windowing key tracks all persisted window bounds', () => {
  const first = windowingKeyOf({ orig: { calMin: 1, calMax: 2, globalMin: 0, globalMax: 3 } });
  const second = windowingKeyOf({ orig: { calMin: 1, calMax: 2, globalMin: 0, globalMax: 4 } });

  assert.notEqual(first, second);
});
