import assert from 'node:assert/strict';
import test from 'node:test';

import { layerDisplayName, surfaceFileStem } from '../src/utils/layerAliases.js';

void test('layer aliases cover volumes, surfaces, and explicit names', () => {
  assert.equal(layerDisplayName({ filename: 'orig.mgz' }), 'Conformed input image');
  assert.equal(layerDisplayName({ filename: 'mri/aparc.DKTatlas+aseg.deep.mgz' }), 'Whole brain segm. (cortical+subcort.)');
  assert.equal(layerDisplayName({ filename: 'lh.pial.surf' }), 'Left pial surface');
  assert.equal(layerDisplayName({ filename: 'surf/rh.white' }), 'Right white matter surface');
  assert.equal(layerDisplayName({ filename: 'lh.pial.surf', name: 'Custom surface' }), 'Custom surface');
  assert.equal(layerDisplayName({ filename: 'surf/lh.pial.surf', name: 'surf/lh.pial.surf' }), 'Left pial surface');
  assert.equal(layerDisplayName({ filename: 'surf/lh.pial.surf', name: 'lh.pial.surf' }), 'Left pial surface');
  assert.equal(surfaceFileStem('lh.pial'), 'lh.pial');
  assert.equal(surfaceFileStem('surf/rh.pial.surf'), 'rh.pial');
  assert.equal(surfaceFileStem('lh.pial.T1'), 'lh.pial.T1');
});
