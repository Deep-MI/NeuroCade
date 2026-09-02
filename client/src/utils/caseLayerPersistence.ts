import { isSurfaceLayer, type Volume } from '../types.js';
import { loadCaseState, saveCaseState } from './caseStorage.js';
import { resolveSurfaceLayerColorMode } from './surfaceColors.js';

export function restorePersistedCaseLayers(caseId: string, serverVolumes: Volume[]): Volume[] {
  const saved = loadCaseState(caseId);
  if (!saved || saved.volumes.length === 0) return serverVolumes;

  const savedOrder = new Map(saved.volumes.flatMap((volume, index) => [
    [volume.id, index] as const,
    [volume.filename, index] as const,
  ]));
  const restoredVolumes = serverVolumes.map((serverVolume) => {
    const persistedVolume = saved.volumes.find((volume) => volume.id === serverVolume.id || volume.filename === serverVolume.filename);
    if (!persistedVolume) {
      return serverVolume;
    }
    const restored = {
      ...serverVolume,
      visible: persistedVolume.visible,
      opacity: persistedVolume.opacity,
    };
    if (isSurfaceLayer(serverVolume) && persistedVolume.type === 'surface') {
      return {
        ...restored,
        surfaceColorMode: resolveSurfaceLayerColorMode({ ...serverVolume, surfaceColorMode: persistedVolume.surfaceColorMode ?? serverVolume.surfaceColorMode }),
        curvatureNegativeThreshold: persistedVolume.curvatureNegativeThreshold ?? serverVolume.curvatureNegativeThreshold,
        curvaturePositiveThreshold: persistedVolume.curvaturePositiveThreshold ?? serverVolume.curvaturePositiveThreshold,
      };
    }
    if (!isSurfaceLayer(serverVolume) && persistedVolume.type !== 'surface') {
      return {
        ...restored,
        brightness: persistedVolume.brightness,
        contrast: persistedVolume.contrast,
      };
    }
    return restored;
  });

  return restoredVolumes
    .map((volume, index) => ({ volume, index }))
    .sort((a, b) => {
      const aOrder = savedOrder.get(a.volume.id) ?? savedOrder.get(a.volume.filename) ?? Number.MAX_SAFE_INTEGER;
      const bOrder = savedOrder.get(b.volume.id) ?? savedOrder.get(b.volume.filename) ?? Number.MAX_SAFE_INTEGER;
      return aOrder - bOrder || a.index - b.index;
    })
    .map(({ volume }) => volume);
}

export function savePersistedCaseLayers(caseId: string, volumes: Volume[]): void {
  saveCaseState(caseId, volumes);
}
