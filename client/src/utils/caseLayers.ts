import type { OutputVolume, Volume } from '../types';
import { createViewerLayer, defaultOutputVolumeVisible, outputVolumeLayerType } from './layerBuilders.js';

export function dedupeOutputVolumes(volumes: OutputVolume[]): OutputVolume[] {
  return volumes.filter((volume, index, volumesList) => (
    volumesList.findIndex((candidate) => candidate.filename === volume.filename) === index
  ));
}

function selectInitialIntensityOutputVolume(volumes: OutputVolume[]): OutputVolume | undefined {
  return volumes.find((volume) => (
    volume.kind === 'volume' && volume.type === 'intensity'
  ));
}

export function visibleOutputVolumes(volumes: OutputVolume[], closedFilenames: Set<string>): OutputVolume[] {
  const restoredVolumes = volumes.filter((volume) => !closedFilenames.has(volume.filename));
  return restoredVolumes.length > 0 ? restoredVolumes : volumes;
}

function outputVolumeToLayer(
  volume: OutputVolume,
  options: {
    initialIntensityVolume?: OutputVolume;
  },
): Volume {
  const layerType = outputVolumeLayerType(volume);
  return createViewerLayer({
    name: volume.name,
    filename: volume.filename,
    artifactId: volume.id,
    url: volume.downloadUrl,
    type: layerType,
    lut: volume.lut,
    customLutUrl: volume.customLutDownloadUrl,
    curvatureUrl: volume.curvatureDownloadUrl,
    annotationUrl: volume.annotationDownloadUrl,
    visible: volume.visible,
  }, {
    defaultVisible: defaultOutputVolumeVisible(volume, options),
  });
}

/**
 * The outputs endpoint order is the NiiVue load order. The layer panel uses
 * painter order, so its background/reference volume appears at the bottom.
 */
export function outputVolumesToViewerLayers(volumes: OutputVolume[]): Volume[] {
  const initialIntensityVolume = selectInitialIntensityOutputVolume(volumes);
  return [...volumes].reverse().map((volume) => (
    outputVolumeToLayer(volume, { initialIntensityVolume })
  ));
}

/** Preserve interactive layer state while adding newly materialized workflow outputs. */
export function mergeOutputVolumesIntoViewerLayers(current: Volume[], outputs: OutputVolume[]): Volume[] {
  const incoming = outputVolumesToViewerLayers(outputs);
  const matchedIds = new Set<string>();
  const merged = incoming.map((layer) => {
    const existing = current.find((candidate) => (
      candidate.artifactId === layer.artifactId || candidate.filename === layer.filename
    ));
    if (existing?.type !== layer.type) return layer;
    matchedIds.add(existing.id);
    return {
      ...layer,
      ...existing,
      artifactId: layer.artifactId,
      url: layer.url,
      ...(layer.type === 'surface' && existing.type === 'surface' ? {
        curvatureUrl: layer.curvatureUrl ?? existing.curvatureUrl,
        annotationUrl: layer.annotationUrl ?? existing.annotationUrl,
      } : {}),
    };
  });
  return [...merged, ...current.filter((layer) => !matchedIds.has(layer.id))];
}
