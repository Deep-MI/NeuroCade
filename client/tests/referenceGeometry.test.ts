import assert from 'node:assert/strict';
import test from 'node:test';
import type Niivue from '@niivue/niivue';

import type { Volume } from '../src/types.js';
import type { NiivueVolumeInterop } from '../src/utils/niivueInterop.js';
import {
  applyReferenceGeometry,
  captureReferenceGeometry,
  moveCrosshairInReferenceVox,
  referenceVoxelToWorldFromGeometry,
  referenceWorldToVoxel,
  selectReferenceVolumeSource,
  type ReferenceGeometry,
} from '../src/neurocadeViewer/referenceGeometry.js';

const identity = [
  1, 0, 0, 0,
  0, 1, 0, 0,
  0, 0, 1, 0,
  0, 0, 0, 1,
] as ReferenceGeometry['matRAS'];

function volume(id: string, type: 'intensity' | 'segmentation' = 'intensity'): Volume {
  return {
    id,
    name: id,
    filename: `${id}.mgz`,
    url: `/${id}.mgz`,
    opacity: 1,
    colormap: 'gray',
    visible: true,
    type,
  };
}

function geometry(): ReferenceGeometry {
  return {
    sourceId: 'orig',
    dimsRAS: [3, 10, 20, 30],
    matRAS: [
      0, -2, 0, 10,
      3, 0, 0, -5,
      0, 0, 4, 7,
      0, 0, 0, 1,
    ],
    tex2mm: identity,
    mm2tex: identity,
    extentsMin: [-10, -20, -30],
    extentsMax: [10, 20, 30],
    pivot3D: [0, 0, 0],
    furthestFromPivot: Math.hypot(10, 20, 30),
  };
}

void test('selectReferenceVolumeSource prefers orig.mgz regardless of layer order', () => {
  const input = volume('input');
  const segmentation = volume('aseg', 'segmentation');
  const origNu = volume('orig_nu');
  const orig = volume('orig');
  assert.equal(selectReferenceVolumeSource([segmentation, origNu, orig, input]), orig);
});

void test('selectReferenceVolumeSource falls back to the first intensity', () => {
  const input = volume('input');
  const segmentation = volume('aseg', 'segmentation');
  assert.equal(selectReferenceVolumeSource([segmentation, input]), input);
});

void test('reference geometry converts between voxel and world coordinates', () => {
  const reference = geometry();
  const world = referenceVoxelToWorldFromGeometry(reference, [2, 3, 4]);
  assert.deepEqual(world, [4, 1, 23]);
  const voxel = referenceWorldToVoxel(reference, world);
  assert.ok(voxel);
  assert.deepEqual(voxel.map((value) => Math.round(value)), [2, 3, 4]);
});

void test('capture stores matrices without retaining the image', () => {
  const source = volume('orig');
  const loaded = {
    id: source.id,
    name: source.name,
    url: source.url,
    dimsRAS: [3, 10, 20, 30],
    matRAS: identity,
    frac2mm: identity,
    extentsMin: [-5, -10, -15],
    extentsMax: [5, 10, 15],
    img: new Uint8Array(1024),
  } as unknown as NiivueVolumeInterop;
  const nv = {
    volumes: [loaded],
    model: {},
  } as unknown as Niivue;

  const captured = captureReferenceGeometry(nv, [source], null);
  assert.equal(captured?.sourceId, source.id);
  assert.equal('img' in (captured ?? {}), false);
});

void test('cached preferred geometry is not replaced by a remaining segmentation', () => {
  const preferred = volume('orig');
  const segmentation = volume('aseg', 'segmentation');
  const cached = geometry();
  const loadedSegmentation = {
    id: segmentation.id,
    dimsRAS: [3, 2, 2, 2],
    matRAS: identity,
  } as unknown as NiivueVolumeInterop;
  const nv = {
    volumes: [loadedSegmentation],
    model: { tex2mm: identity, mm2tex: identity },
  } as unknown as Niivue;

  assert.equal(captureReferenceGeometry(nv, [segmentation, preferred], cached), cached);
});

void test('a hidden preferred reference never adopts another loaded volume geometry', () => {
  const preferred = volume('orig');
  preferred.visible = false;
  const input = volume('input');
  const loadedInput = {
    id: input.id,
    dimsRAS: [3, 2, 2, 2],
    matRAS: identity,
    frac2mm: identity,
    extentsMin: [-1, -1, -1],
    extentsMax: [1, 1, 1],
  } as unknown as NiivueVolumeInterop;
  const nv = { volumes: [loadedInput], model: {} } as unknown as Niivue;

  assert.equal(captureReferenceGeometry(nv, [input, preferred], null), null);
});

void test('applyReferenceGeometry restores transforms only with no loaded volume', () => {
  const reference = geometry();
  const model = {
    tex2mm: null,
    mm2tex: null,
    extentsMin: null,
    extentsMax: null,
    pivot3D: null,
    furthestFromPivot: 0,
  };
  const nv = { volumes: [], model } as unknown as Niivue;
  applyReferenceGeometry(nv, reference);
  assert.deepEqual(Array.from(model.tex2mm ?? []), reference.tex2mm);
  assert.deepEqual(Array.from(model.mm2tex ?? []), reference.mm2tex);
  assert.deepEqual(Array.from(model.extentsMin ?? []), reference.extentsMin);
  assert.deepEqual(Array.from(model.extentsMax ?? []), reference.extentsMax);
  assert.deepEqual(Array.from(model.pivot3D ?? []), reference.pivot3D);
  assert.equal(model.furthestFromPivot, reference.furthestFromPivot);
});

void test('applyReferenceGeometry pins bounds without replacing live volume transforms', () => {
  const reference = geometry();
  const liveTex2mm = new Float32Array(identity.map((value) => value * 2));
  const liveMm2tex = new Float32Array(identity.map((value) => value * 0.5));
  const model = {
    tex2mm: liveTex2mm,
    mm2tex: liveMm2tex,
    extentsMin: null,
    extentsMax: null,
    pivot3D: null,
    furthestFromPivot: 0,
  };
  const nv = { volumes: [{}], model } as unknown as Niivue;
  applyReferenceGeometry(nv, reference);
  assert.equal(model.tex2mm, liveTex2mm);
  assert.equal(model.mm2tex, liveMm2tex);
  assert.deepEqual(Array.from(model.extentsMin ?? []), reference.extentsMin);
});

void test('surface-only keyboard movement uses cached voxel geometry', () => {
  const reference = geometry();
  let crosshair: [number, number, number] = referenceVoxelToWorldFromGeometry(reference, [2, 3, 4]);
  const nv = {
    getCrosshairPos: () => crosshair,
    setCrosshairPos: (next: [number, number, number]) => { crosshair = next; },
  } as unknown as Niivue;

  assert.equal(moveCrosshairInReferenceVox(nv, reference, [1, 0, 0]), true);
  assert.deepEqual(referenceWorldToVoxel(reference, crosshair)?.map(Math.round), [3, 3, 4]);
});
