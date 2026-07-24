import type { OutputVolume, Volume } from '../types';
import { isLayerFile } from './layerAliases';
import { createViewerLayer, defaultOutputVolumeVisible, inferOutputVolumeLayerType } from './layerBuilders';

export function dedupeOutputVolumes(volumes: OutputVolume[]): OutputVolume[] {
  return volumes.filter((volume, index, volumesList) => (
    volumesList.findIndex((candidate) => candidate.filename === volume.filename) === index
  ));
}

export function selectInitialIntensityOutputVolume(volumes: OutputVolume[]): OutputVolume | undefined {
  const intensityVolumes = volumes.filter((volume) => volume.kind === 'volume' && (volume.type ?? 'intensity') === 'intensity');
  return intensityVolumes.find((volume) => volume.visible === true)
    ?? intensityVolumes.find((volume) => isLayerFile(volume.filename, 'orig.mgz'))
    ?? intensityVolumes.find((volume) => isLayerFile(volume.filename, '001.mgz'))
    ?? intensityVolumes[0];
}

export function visibleOutputVolumes(volumes: OutputVolume[], closedFilenames: Set<string>): OutputVolume[] {
  const restoredVolumes = volumes.filter((volume) => !closedFilenames.has(volume.filename));
  return restoredVolumes.length > 0 ? restoredVolumes : volumes;
}

export function outputVolumeToLayer(
  volume: OutputVolume,
  options: {
    hasOrigVolume: boolean;
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
