import type { OutputVolume, Volume } from '../types';
import { createViewerLayer, defaultOutputVolumeVisible, inferOutputVolumeLayerType } from './layerBuilders.js';

export function dedupeOutputVolumes(volumes: OutputVolume[]): OutputVolume[] {
  return volumes.filter((volume, index, volumesList) => (
    volumesList.findIndex((candidate) => candidate.filename === volume.filename) === index
  ));
}

function selectInitialIntensityOutputVolume(volumes: OutputVolume[]): OutputVolume | undefined {
  return volumes.find((volume) => (
    volume.kind === 'volume' && (volume.type ?? 'intensity') === 'intensity'
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
  const layerType = inferOutputVolumeLayerType(volume);
  return createViewerLayer({
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
