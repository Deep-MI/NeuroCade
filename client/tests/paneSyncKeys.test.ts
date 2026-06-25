import assert from 'node:assert/strict';
import test from 'node:test';

import {
  sourceVisibilityKeyOf,
  surfaceAppearanceKeyOf,
  surfaceTransformKeyOf,
  volumeAppearanceKeyOf,
  volumeOrderKeyOf,
  volumeVisibilityKeyOf,
  windowingKeyOf,
} from '../src/neurocadeViewer/paneSyncKeys.js';
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
  renderIn3D: true,
  renderInSlices: false,
  surfaceColorMode: 'curvature',
  surfaceReferenceAffine: [[1, 0, 0, 0]],
  curvatureNegativeThreshold: -0.2,
  curvaturePositiveThreshold: 0.2,
} satisfies Volume;

void test('pane sync keys isolate source, visibility, appearance, and ordering concerns', () => {
  assert.match(sourceVisibilityKeyOf([intensity]), /orig\.mgz:\/orig\.mgz/);
  assert.notEqual(volumeVisibilityKeyOf([intensity]), volumeVisibilityKeyOf([{ ...intensity, opacity: 0.5 }]));
  assert.notEqual(volumeAppearanceKeyOf([intensity]), volumeAppearanceKeyOf([{ ...intensity, colormap: 'hot' }]));
  assert.equal(volumeOrderKeyOf([intensity, segmentation]), 'orig.mgz|aseg.mgz');
  assert.equal(
    volumeOrderKeyOf([
      { ...segmentation, id: 'seg-top', filename: 'seg-top.mgz' },
      intensity,
      { ...segmentation, id: 'seg-bottom', filename: 'seg-bottom.mgz' },
    ]),
    'orig.mgz|seg-bottom|seg-top',
  );
});

void test('pane sync keys include surface display and transform fields', () => {
  assert.notEqual(
    surfaceAppearanceKeyOf([surface]),
    surfaceAppearanceKeyOf([{ ...surface, curvaturePositiveThreshold: 0.4 }]),
  );
  assert.notEqual(
    surfaceTransformKeyOf([surface]),
    surfaceTransformKeyOf([{ ...surface, surfaceReferenceAffine: [[2, 0, 0, 0]] }]),
  );
});

void test('windowing key tracks all persisted window bounds', () => {
  const first = windowingKeyOf({ orig: { calMin: 1, calMax: 2, globalMin: 0, globalMax: 3 } });
  const second = windowingKeyOf({ orig: { calMin: 1, calMax: 2, globalMin: 0, globalMax: 4 } });

  assert.notEqual(first, second);
});
