import { isSurfaceLayer, type Volume } from '../types.js';
import type { NiivueVolumeInterop } from '../utils/niivueInterop.js';

function loadedMatchesSource(loaded: NiivueVolumeInterop, source: Volume): boolean {
  return loaded.id === source.id
    || loaded.url === source.url
    || loaded.name === source.filename
    || loaded.name === source.name;
}

export function selectReferenceVolumeSource(sources: Volume[]): Volume | null {
  const volumes = sources.filter((volume) => !isSurfaceLayer(volume));
  const intensities = volumes.filter((volume) => volume.type !== 'segmentation');
  const segmentations = volumes.filter((volume) => volume.type === 'segmentation');
  return intensities.at(-1) ?? segmentations.at(-1) ?? null;
}

export function selectLoadedReferenceVolume(loadedVolumes: NiivueVolumeInterop[], sources: Volume[]): NiivueVolumeInterop | null {
  const volumes = sources.filter((volume) => !isSurfaceLayer(volume));
  const intensities = volumes.filter((volume) => volume.type !== 'segmentation').reverse();
  const segmentations = volumes.filter((volume) => volume.type === 'segmentation').reverse();
  for (const source of [...intensities, ...segmentations]) {
    const loaded = loadedVolumes.find((candidate) => loadedMatchesSource(candidate, source));
    if (loaded) return loaded;
  }
  return loadedVolumes[0] ?? null;
}
