import assert from 'node:assert/strict';
import test from 'node:test';

import {
  referenceWorldToVoxel,
  referenceVoxelToWorld,
  externalCoordinateToWorld,
  coordinateSourceFor,
  enforceVolumeRenderOrder,
  reorderLoadedVolumes,
  setLoadedVolumeOpacity,
  syncLoadedVolumeOpacities,
} from '../src/neurocadeViewer/loadedVolumeDisplay.js';
import { clampMeshOutlineWidth, MESH_OUTLINE_WIDTH } from '../src/neurocadeViewer/layerDisplay.js';
import { applyLayerDisplay } from '../src/neurocadeViewer/viewerPaneAdapter.js';
import type { Volume } from '../src/types.js';
import type { NiivueVolumeInterop } from '../src/utils/niivueInterop.js';

function source(id: string, type: Volume['type'], visible: boolean): Volume {
  return {
    id,
    filename: id,
    name: id,
    url: `/${id}`,
    type,
    visible,
    opacity: 1,
    colormap: 'gray',
  };
}

function loadedVolume(id: string, obliqueAngle: number): NiivueVolumeInterop {
  return {
    id,
    name: id,
    url: `/${id}`,
    oblique_angle: obliqueAngle,
    dimsRAS: [3, 20, 20, 20],
    pixDimsRAS: [1, 1, 1, 1],
    extentsMin: [-10, -10, -10],
    extentsMax: [10, 10, 10],
    matRAS: [
      1, 0, 0, -10,
      0, 1, 0, -10,
      0, 0, 1, -10,
      0, 0, 0, 1,
    ],
  } as NiivueVolumeInterop;
}

function mockNiivue(volumes: NiivueVolumeInterop[]) {
  return {
    volumes,
    model: {
      addVolume: () => {
        volumes.push({} as NiivueVolumeInterop);
        return Promise.resolve();
      },
      moveVolume: (from: number, to: number) => {
        const [volume] = volumes.splice(from, 1);
        volumes.splice(to, 0, volume);
        return true;
      },
      removeVolume: (index: number) => {
        volumes.splice(index, 1);
      },
    },
    updateGLVolume: () => Promise.resolve(),
  };
}

void test('setLoadedVolumeOpacity stages a display-only opacity change', () => {
  const loaded = { id: 'orig', opacity: 1 } as NiivueVolumeInterop;
  const nv = {
    volumes: [loaded],
  };

  const result = setLoadedVolumeOpacity(nv as never, loaded, 0.35);

  assert.equal(result, 'updated');
  assert.equal(loaded.opacity, 0.35);
  assert.equal(loaded.isDirty, undefined);
});

void test('a visibility change requests a GL refresh after staging opacity', () => {
  const sourceVolume = source('orig', 'intensity', true);
  const loaded = { id: sourceVolume.id, opacity: 1 } as NiivueVolumeInterop;
  const nv = { volumes: [loaded], meshes: [] };

  const action = applyLayerDisplay(
    nv as never,
    sourceVolume.id,
    { ...sourceVolume, visible: false },
    { visible: false },
  );

  assert.deepEqual(action, { kind: 'refresh' });
  assert.equal(loaded.opacity, 0);
});

void test('hiding a real volume changes opacity without changing its position', () => {
  const loadedReference = { id: 'orig', opacity: 1 } as NiivueVolumeInterop;
  const loadedOverlay = { id: 'input', opacity: 1 } as NiivueVolumeInterop;
  const nv = {
    volumes: [loadedReference, loadedOverlay],
  };

  const result = setLoadedVolumeOpacity(nv as never, loadedReference, 0);

  assert.equal(result, 'updated');
  assert.equal(loadedReference.opacity, 0);
  assert.equal(nv.volumes[0], loadedReference);
  assert.equal(nv.volumes.length, 2);
});

void test('post-load opacity sync reapplies the latest layer pane visibility', () => {
  const hidden = source('orig.mgz', 'intensity', false);
  const visible = source('input.mgz', 'intensity', true);
  const loadedHidden = { ...loadedVolume(hidden.id, 0), opacity: 1 };
  const loadedVisible = { ...loadedVolume(visible.id, 3.5), opacity: 0 };
  const nv = { volumes: [loadedHidden, loadedVisible] };

  assert.equal(syncLoadedVolumeOpacities(nv as never, new Map([
    [hidden.id, 0],
    [visible.id, 1],
  ])), true);
  assert.equal(loadedHidden.opacity, 0);
  assert.equal(loadedVisible.opacity, 1);
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

void test('reorderLoadedVolumes preserves the crosshair in world coordinates', () => {
  const loadedInput = { id: 'input' } as NiivueVolumeInterop;
  const loadedOrig = { id: 'orig' } as NiivueVolumeInterop;
  const volumes = [loadedInput, loadedOrig];
  const restored: number[][] = [];
  const nv = {
    volumes,
    getCrosshairPos: () => [12, -34, 56],
    setCrosshairPos: (position: number[]) => restored.push(position),
    model: {
      moveVolume: (from: number, to: number) => {
        const [volume] = volumes.splice(from, 1);
        volumes.splice(to, 0, volume);
        return true;
      },
    },
  };

  reorderLoadedVolumes(nv as never, [loadedOrig, loadedInput]);

  assert.deepEqual(restored, [[12, -34, 56]]);
});

void test('referenceVoxelToWorld applies the base volume RAS affine', () => {
  const nv = {
    volumes: [{
      matRAS: [
        0, 2, 0, 10,
        -3, 0, 0, 20,
        0, 0, 4, 30,
        0, 0, 0, 1,
      ],
    }],
  };

  assert.deepEqual(referenceVoxelToWorld(nv as never, [1, 2, 3]), [14, 17, 42]);
  assert.deepEqual(
    referenceWorldToVoxel(nv as never, [14, 17, 42])?.map(Math.round),
    [1, 2, 3],
  );
});

void test('voxel coordinates use the first loaded volume as the frame', () => {
  const input = {
    id: 'input',
    matRAS: [
      0, 2, 0, 10,
      -3, 0, 0, 20,
      0, 0, 4, 30,
      0, 0, 0, 1,
    ],
  };
  const nv = { volumes: [input] };

  assert.deepEqual(referenceVoxelToWorld(nv as never, [1, 2, 3]), [14, 17, 42]);
  assert.deepEqual(referenceWorldToVoxel(nv as never, [14, 17, 42])?.map(Math.round), [1, 2, 3]);
});

void test('enforceVolumeRenderOrder puts the bottom intensity volume first', () => {
  const input = source('input.mgz', 'intensity', true);
  const orig = source('orig.mgz', 'intensity', true);
  const segmentation = source('aseg.mgz', 'segmentation', true);
  const volumes = [loadedVolume(input.id, 0), loadedVolume(orig.id, 0), loadedVolume(segmentation.id, 0)];
  const nv = mockNiivue(volumes);

  assert.equal(enforceVolumeRenderOrder(nv as never, [segmentation, input, orig]), true);
  assert.deepEqual(volumes.map((volume) => volume.id), ['orig.mgz', 'input.mgz', 'aseg.mgz']);
  assert.equal(enforceVolumeRenderOrder(nv as never, [segmentation, input, orig]), false);
});

void test('coordinateSourceFor reports the bottom layer that is actually loaded', () => {
  const input = source('input.mgz', 'intensity', true);
  const orig = source('orig.mgz', 'intensity', true);
  const segmentation = source('aseg.mgz', 'segmentation', true);
  const sources = [segmentation, input, orig];

  // orig.mgz is the bottom layer in render order but has not been loaded yet.
  assert.equal(coordinateSourceFor(mockNiivue([loadedVolume(input.id, 0)]) as never, sources), 'input.mgz');
  assert.equal(coordinateSourceFor(mockNiivue([]) as never, sources), null);
});

void test('mesh outline width is clamped to the slider range with a finite default', () => {
  assert.equal(clampMeshOutlineWidth(0), MESH_OUTLINE_WIDTH.min);
  assert.equal(clampMeshOutlineWidth(99), MESH_OUTLINE_WIDTH.max);
  assert.equal(clampMeshOutlineWidth(2.5), 2.5);
  assert.equal(clampMeshOutlineWidth(Number.NaN), MESH_OUTLINE_WIDTH.default);
  assert.equal(clampMeshOutlineWidth(undefined), MESH_OUTLINE_WIDTH.default);
});

void test('external world cursor bypasses cropped/reference voxel transforms', () => {
  const nv = { volumes: [{ matRAS: [2, 0, 0, 100, 0, 3, 0, -50, 0, 0, 4, 20, 0, 0, 0, 1] }] };
  assert.deepEqual(externalCoordinateToWorld(nv as never, [12.5, -20, 7], 'world'), [12.5, -20, 7]);
  assert.deepEqual(externalCoordinateToWorld(nv as never, [1, 2, 3]), [102, -44, 32]);
  assert.equal(externalCoordinateToWorld(nv as never, [NaN, 0, 0], 'world'), null);
});
