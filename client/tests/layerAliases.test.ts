import assert from 'node:assert/strict';
import test from 'node:test';

import { layerDisplayName, surfaceFileStem } from '../src/utils/layerAliases.js';

void test('layerDisplayName aliases known volume filenames', () => {
  assert.equal(layerDisplayName({ filename: 'orig.mgz' }), 'Conformed input image');
  assert.equal(layerDisplayName({ filename: 'mri/aparc.DKTatlas+aseg.deep.mgz' }), 'Whole brain segm. (cortical+subcort.)');
});

void test('layerDisplayName derives hemisphere surface aliases', () => {
  assert.equal(layerDisplayName({ filename: 'lh.pial.surf' }), 'Left pial surface');
  assert.equal(layerDisplayName({ filename: 'surf/rh.white' }), 'Right white matter surface');
});

void test('layerDisplayName keeps explicit custom names', () => {
  assert.equal(layerDisplayName({ filename: 'lh.pial.surf', name: 'Custom surface' }), 'Custom surface');
});

void test('layerDisplayName ignores raw filename names when aliasing', () => {
  assert.equal(layerDisplayName({ filename: 'surf/lh.pial.surf', name: 'surf/lh.pial.surf' }), 'Left pial surface');
  assert.equal(layerDisplayName({ filename: 'surf/lh.pial.surf', name: 'lh.pial.surf' }), 'Left pial surface');
});

void test('surfaceFileStem normalizes surface filenames', () => {
  assert.equal(surfaceFileStem('lh.pial'), 'lh.pial');
  assert.equal(surfaceFileStem('surf/rh.pial.surf'), 'rh.pial');
  assert.equal(surfaceFileStem('lh.pial.T1'), 'lh.pial.T1');
});
