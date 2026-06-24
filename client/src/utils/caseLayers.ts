import {
  DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
  DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
} from '../constants';
import type { OutputVolume, Volume } from '../types';
import { isLayerFile, layerDisplayName, surfaceFileStem } from './layerAliases';
import { defaultSurfaceColorModeForLayer } from './surfaceColors';

function isDefaultVisibleSurface(filename: string): boolean {
  return ['lh.pial', 'rh.pial'].includes(surfaceFileStem(filename));
}

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
  const normalized = volume.filename.toLowerCase();
  const isSurface = volume.type === 'surface';
  const isSegmentation = normalized.includes('aparc')
    || normalized.includes('aseg')
    || normalized.includes('seg')
    || normalized.includes('mask')
    || normalized.includes('cereb')
    || normalized.includes('wmparc')
    || normalized.includes('hypothalamus');
  const isBinaryMaskHint = normalized.includes('mask') || normalized.includes('brainmask') || normalized.includes('_bin');
  const isInputVolume = isLayerFile(volume.filename, '001.mgz');
  const isOrigVolume = isLayerFile(volume.filename, 'orig.mgz');
  const isDefaultSegmentation = isLayerFile(volume.filename, 'aparc.DKTatlas+aseg.deep.mgz');
  const defaultVisible = isSurface
    ? isDefaultVisibleSurface(volume.filename)
    : (volume.filename === options.initialIntensityVolume?.filename || isDefaultSegmentation || (!options.initialIntensityVolume && (isOrigVolume || (!options.hasOrigVolume && isInputVolume))));

  const baseLayer = {
    id: volume.filename,
    name: layerDisplayName(volume),
    filename: volume.filename,
    artifactId: volume.id,
    url: volume.downloadUrl,
    opacity: isSurface ? 1.0 : isSegmentation ? 0.7 : 1.0,
    colormap: isSurface ? 'surface' : (isSegmentation ? '' : 'gray'),
    visible: volume.visible ?? defaultVisible,
    renderIn3D: isSurface,
    renderInSlices: isSurface,
  };

  if (isSurface) {
    const surfaceLayer = {
      ...baseLayer,
      type: 'surface' as const,
      surfaceReferenceAffine: volume.surfaceReferenceAffine,
      curvatureUrl: volume.curvatureDownloadUrl,
      annotationUrl: volume.annotationDownloadUrl,
      curvatureNegativeThreshold: DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
      curvaturePositiveThreshold: DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
    };
    return {
      ...surfaceLayer,
      surfaceColorMode: defaultSurfaceColorModeForLayer(surfaceLayer),
    };
  }

  if (isSegmentation) {
    return {
      ...baseLayer,
      type: 'segmentation',
      lut: (volume.lut === 'binary' || volume.lut === 'freesurfer')
        ? volume.lut
        : (isBinaryMaskHint ? 'binary' : 'freesurfer'),
      customLutUrl: volume.customLutDownloadUrl,
      brightness: 0,
      contrast: 1.0,
    };
  }

  return {
    ...baseLayer,
    type: 'intensity',
    brightness: 0,
    contrast: 1.0,
  };
}
