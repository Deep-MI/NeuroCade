import type Niivue from '@niivue/niivue';

import {
  FIXED_REFERENCE_ID,
  createFixedReferenceGrid,
} from './fixedReference.js';
import { isSurfaceLayer, type Volume } from '../types.js';
import {
  asNiivueInterop,
  type NiivueVolumeInterop,
} from '../utils/niivueInterop.js';
import { orderedReferenceCandidate, volumesInRenderOrder } from './layerDisplay.js';
import { reorderLoadedVolumes } from './loadedVolumeDisplay.js';

interface FixedReferenceState {
  generation: number;
  creation?: Promise<NiivueVolumeInterop | null>;
}

const fixedReferenceStates = new WeakMap<Niivue, FixedReferenceState>();

function stateFor(nv: Niivue): FixedReferenceState {
  const current = fixedReferenceStates.get(nv);
  if (current) return current;
  const created = { generation: 0 };
  fixedReferenceStates.set(nv, created);
  return created;
}

function isFixedReference(volume: NiivueVolumeInterop): boolean {
  return volume.__neurocadeFixedReference === true || volume.id === FIXED_REFERENCE_ID;
}

function fixedReferenceVolume(nv: Niivue): NiivueVolumeInterop | undefined {
  return asNiivueInterop(nv).volumes.find(isFixedReference);
}

function loadedVolume(nv: Niivue, id: string): NiivueVolumeInterop | undefined {
  return asNiivueInterop(nv).volumes.find((volume) => volume.id === id);
}

export function removeFixedNiivueReference(nv: Niivue): boolean {
  stateFor(nv).generation += 1;
  const volumes = asNiivueInterop(nv).volumes;
  const fixedReferences = volumes.filter(isFixedReference);
  for (const fixedReference of fixedReferences.reverse()) {
    const index = volumes.indexOf(fixedReference);
    if (index >= 0) nv.model.removeVolume(index);
  }
  return fixedReferences.length > 0;
}

export function enforceVolumeRenderOrder(
  nv: Niivue,
  sources: Volume[],
): boolean {
  const fixedReference = fixedReferenceVolume(nv);
  const orderedSources = volumesInRenderOrder(sources)
    .map((source) => loadedVolume(nv, source.id))
    .filter((volume): volume is NiivueVolumeInterop => Boolean(volume));
  const desired = [
    ...(fixedReference ? [fixedReference] : []),
    ...orderedSources,
  ];
  const desiredSet = new Set(desired);
  desired.push(...asNiivueInterop(nv).volumes.filter((volume) => !desiredSet.has(volume)));
  return reorderLoadedVolumes(nv, desired);
}

async function createFixedReference(
  nv: Niivue,
  loadedSources: NiivueVolumeInterop[],
  resolutionSource: NiivueVolumeInterop,
  generation: number,
): Promise<NiivueVolumeInterop | null> {
  const grid = createFixedReferenceGrid(loadedSources, resolutionSource);
  const volumes = asNiivueInterop(nv).volumes;
  const volumesBefore = new Set(volumes);
  await nv.model.addVolume({
    url: new File([grid.buffer], 'neurocade-reference.nii'),
    name: 'NeuroCade reference grid',
    opacity: 0,
  });
  const fixedReference = volumes.find((volume) => (
    !volumesBefore.has(volume) && volume.name === 'NeuroCade reference grid'
  )) ?? volumes.find((volume) => !volumesBefore.has(volume));
  if (!fixedReference) {
    throw new Error('NiiVue did not install the fixed reference volume.');
  }

  if (stateFor(nv).generation !== generation) {
    const index = volumes.indexOf(fixedReference);
    if (index >= 0) nv.model.removeVolume(index);
    return null;
  }

  fixedReference.id = FIXED_REFERENCE_ID;
  fixedReference.name = 'NeuroCade reference grid';
  fixedReference.url = FIXED_REFERENCE_ID;
  fixedReference.opacity = 0;
  fixedReference.__neurocadeFixedReference = true;
  return fixedReference;
}

async function ensureReferenceCreated(
  nv: Niivue,
  loadedSources: NiivueVolumeInterop[],
  resolutionSource: NiivueVolumeInterop,
): Promise<NiivueVolumeInterop | null> {
  const state = stateFor(nv);
  const generation = state.generation;

  while (state.generation === generation) {
    const existing = fixedReferenceVolume(nv);
    if (existing) return existing;
    if (state.creation) {
      await state.creation;
      continue;
    }

    const creation = createFixedReference(
      nv,
      loadedSources,
      resolutionSource,
      generation,
    );
    state.creation = creation;
    try {
      return await creation;
    } finally {
      if (state.creation === creation) state.creation = undefined;
    }
  }

  return null;
}

export async function ensureFixedNiivueReference(
  nv: Niivue,
  sources: Volume[],
): Promise<string | null> {
  const sourceVolumes = sources.filter((source) => !isSurfaceLayer(source));
  const loadedSources = sourceVolumes
    .map((source) => loadedVolume(nv, source.id))
    .filter((volume): volume is NiivueVolumeInterop => Boolean(volume));

  if (sourceVolumes.length === 0) {
    const changed = removeFixedNiivueReference(nv);
    if (changed) await nv.updateGLVolume();
    return null;
  }

  if (loadedSources.length === 0) {
    return null;
  }

  const coordinateSource = orderedReferenceCandidate(sourceVolumes);
  const resolutionSource = coordinateSource
    ? loadedVolume(nv, coordinateSource.id)
    : loadedSources[0];
  const hadFixedReference = Boolean(fixedReferenceVolume(nv));
  const fixedReference = await ensureReferenceCreated(
    nv,
    loadedSources,
    resolutionSource ?? loadedSources[0],
  );
  if (!fixedReference) return null;

  fixedReference.__neurocadeCoordinateSourceId = coordinateSource?.id ?? undefined;
  fixedReference.opacity = 0;

  const changed = enforceVolumeRenderOrder(nv, sources) || !hadFixedReference;
  if (changed) {
    await nv.updateGLVolume();
  }

  return coordinateSource?.id ?? null;
}
