import { isSurfaceLayer, type Volume } from '../types.js';
import type { NiivueVolumeInterop } from '../utils/niivueInterop.js';

function isIntensityLayer(volume: Volume): boolean {
  return (volume.type ?? 'intensity') === 'intensity';
}

function loadedMatchesSource(loaded: NiivueVolumeInterop, source: Volume): boolean {
  return loaded.id === source.id
    || loaded.url === source.url
    || loaded.name === source.filename
    || loaded.name === source.name;
}

export function selectReferenceVolumeSource(sources: Volume[]): Volume | null {
  const volumes = sources.filter((volume) => !isSurfaceLayer(volume));
  return volumes.find((volume) => volume.visible && isIntensityLayer(volume))
    ?? volumes.find((volume) => volume.visible)
    ?? volumes.find(isIntensityLayer)
    ?? volumes[0]
    ?? null;
}

export function selectLoadedReferenceVolume(loadedVolumes: NiivueVolumeInterop[], sources: Volume[]): NiivueVolumeInterop | null {
  const source = selectReferenceVolumeSource(sources);
  if (!source) return loadedVolumes[0] ?? null;
  return loadedVolumes.find((loaded) => loadedMatchesSource(loaded, source)) ?? null;
}
