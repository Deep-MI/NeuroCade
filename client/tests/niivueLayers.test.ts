import assert from 'node:assert/strict';
import test from 'node:test';

import {
  referenceWorldToVoxel,
  referenceVoxelToWorld,
  reorderLoadedVolumes,
  setLoadedVolumeOpacity,
  syncLoadedVolumeOpacities,
} from '../src/neurocadeViewer/loadedVolumeDisplay.js';
import { FIXED_REFERENCE_ID } from '../src/neurocadeViewer/fixedReference.js';
import {
  ensureFixedNiivueReference,
  removeFixedNiivueReference,
} from '../src/neurocadeViewer/fixedReferenceRuntime.js';
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
  } as Volume;
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

void test('coordinate conversion bypasses the fixed reference grid', () => {
  const fixedReference = {
    id: FIXED_REFERENCE_ID,
    __neurocadeFixedReference: true,
    __neurocadeCoordinateSourceId: 'input',
    matRAS: [
      1, 0, 0, 0,
      0, 1, 0, 0,
      0, 0, 1, 0,
      0, 0, 0, 1,
    ],
  };
  const input = {
    id: 'input',
    matRAS: [
      0, 2, 0, 10,
      -3, 0, 0, 20,
      0, 0, 4, 30,
      0, 0, 0, 1,
    ],
  };
  const nv = { volumes: [fixedReference, input] };

  assert.deepEqual(referenceVoxelToWorld(nv as never, [1, 2, 3]), [14, 17, 42]);
  assert.deepEqual(referenceWorldToVoxel(nv as never, [14, 17, 42])?.map(Math.round), [1, 2, 3]);
});

void test('a fixed transparent grid is always inserted at volume zero', async () => {
  const hiddenOrig = source('orig.mgz', 'intensity', false);
  const bottomOblique = source('input.mgz', 'intensity', true);
  const loadedBottom = loadedVolume(bottomOblique.id, 3.5);
  const loadedOrig = loadedVolume(hiddenOrig.id, 0);
  const volumes = [loadedOrig, loadedBottom];
  const nv = mockNiivue(volumes);

  const coordinateSourceId = await ensureFixedNiivueReference(
    nv as never,
    [hiddenOrig, bottomOblique],
  );

  assert.deepEqual(
    volumes.map((volume) => volume.id),
    [FIXED_REFERENCE_ID, 'input.mgz', 'orig.mgz'],
  );
  assert.equal(volumes[0].opacity, 0);
  assert.equal(volumes[0].__neurocadeFixedReference, true);
  assert.equal(coordinateSourceId, 'input.mgz');
});

void test('visibility changes neither rebuild nor reorder the fixed reference', async () => {
  const topOrig = source('orig.mgz', 'intensity', false);
  const bottomOblique = source('input.mgz', 'intensity', true);
  const loadedBottom = loadedVolume(bottomOblique.id, 3.5);
  const loadedOrig = loadedVolume(topOrig.id, 0);
  const volumes = [loadedOrig, loadedBottom];
  const nv = mockNiivue(volumes);

  await ensureFixedNiivueReference(nv as never, [topOrig, bottomOblique]);
  const fixedReference = volumes[0];
  const firstOrder = volumes.map((volume) => volume.id);

  await ensureFixedNiivueReference(
    nv as never,
    [{ ...topOrig, visible: true }, bottomOblique],
  );

  assert.equal(volumes[0], fixedReference);
  assert.deepEqual(volumes.map((volume) => volume.id), firstOrder);
  assert.equal(volumes.filter((volume) => volume.__neurocadeFixedReference).length, 1);
});

void test('concurrent reconciliation installs only one fixed reference', async () => {
  const input = source('input.mgz', 'intensity', true);
  const volumes = [loadedVolume(input.id, 0)];
  let releaseCreation: (() => void) | undefined;
  let addVolumeCalls = 0;
  const creationGate = new Promise<void>((resolve) => {
    releaseCreation = resolve;
  });
  const nv = {
    ...mockNiivue(volumes),
    model: {
      ...mockNiivue(volumes).model,
      addVolume: async () => {
        addVolumeCalls += 1;
        await creationGate;
        volumes.push({
          name: 'NeuroCade reference grid',
        } as NiivueVolumeInterop);
      },
    },
  };

  const first = ensureFixedNiivueReference(nv as never, [input]);
  const second = ensureFixedNiivueReference(nv as never, [input]);
  releaseCreation?.();
  await Promise.all([first, second]);

  assert.equal(addVolumeCalls, 1);
  assert.equal(volumes.filter((volume) => volume.__neurocadeFixedReference).length, 1);
});

void test('removal invalidates an in-flight fixed reference creation', async () => {
  const input = source('input.mgz', 'intensity', true);
  const volumes = [loadedVolume(input.id, 0)];
  let releaseCreation: (() => void) | undefined;
  let addVolumeCalls = 0;
  const creationGate = new Promise<void>((resolve) => {
    releaseCreation = resolve;
  });
  const base = mockNiivue(volumes);
  const nv = {
    ...base,
    model: {
      ...base.model,
      addVolume: async () => {
        addVolumeCalls += 1;
        await creationGate;
        volumes.push({
          name: 'NeuroCade reference grid',
        } as NiivueVolumeInterop);
      },
    },
  };

  const staleCreation = ensureFixedNiivueReference(nv as never, [input]);
  removeFixedNiivueReference(nv as never);
  releaseCreation?.();

  assert.equal(await staleCreation, null);
  assert.equal(volumes.filter((volume) => volume.__neurocadeFixedReference).length, 0);
  assert.equal(await ensureFixedNiivueReference(nv as never, [input]), input.id);
  assert.equal(addVolumeCalls, 2);
  assert.equal(volumes.filter((volume) => volume.__neurocadeFixedReference).length, 1);
});

void test('reordering pane layers only reorders real NiiVue volumes', async () => {
  const segmentation = source('aseg.mgz', 'segmentation', true);
  const orig = source('orig.mgz', 'intensity', true);
  const input = source('input.mgz', 'intensity', true);
  const loadedOrig = loadedVolume(orig.id, 0);
  const loadedInput = loadedVolume(input.id, 3.5);
  const loadedSegmentation = loadedVolume(segmentation.id, 0);
  const volumes = [loadedOrig, loadedInput, loadedSegmentation];
  const nv = mockNiivue(volumes);

  await ensureFixedNiivueReference(nv as never, [segmentation, orig, input]);
  const fixedReference = volumes[0];
  const coordinateSourceId = await ensureFixedNiivueReference(
    nv as never,
    [segmentation, input, orig],
  );

  assert.deepEqual(
    volumes.map((volume) => volume.id),
    [FIXED_REFERENCE_ID, 'orig.mgz', 'input.mgz', 'aseg.mgz'],
  );
  assert.equal(volumes[0], fixedReference);
  assert.equal(coordinateSourceId, 'orig.mgz');
});
