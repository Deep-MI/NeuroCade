import {
  DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
  DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
} from '../constants.js';
import type { LayerType, OutputVolume, Volume } from '../types.js';
import { isLayerFile, layerDisplayName, surfaceFileStem } from './layerAliases.js';
import { defaultSurfaceColorModeForLayer } from './surfaceColors.js';

type LoadableLayerType = Exclude<LayerType, 'drawing'>;

interface ViewerLayerSource {
  id?: string;
  name?: string;
  filename: string;
  url: string;
  artifactId?: string;
  type: LoadableLayerType;
  lut?: string;
  customLutUrl?: string;
  curvatureUrl?: string;
  annotationUrl?: string;
  visible?: boolean;
}

interface ViewerLayerOptions {
  defaultVisible?: boolean;
}

export function isMaskLikeFilename(filename: string): boolean {
  const normalized = filename.toLowerCase();
  return normalized.includes('mask') || normalized.includes('brainmask') || normalized.includes('_bin');
}

export function outputVolumeLayerType(volume: OutputVolume): LayerType {
  return volume.type === 'surface' || volume.type === 'segmentation' || volume.type === 'drawing' || volume.type === 'intensity'
    ? volume.type
    : 'intensity';
}

function defaultVisibleSurface(filename: string): boolean {
  return ['lh.pial', 'rh.pial'].includes(surfaceFileStem(filename));
}

export function defaultOutputVolumeVisible(
  volume: OutputVolume,
  options: {
    initialIntensityVolume?: OutputVolume;
  },
): boolean {
  const isSurface = volume.type === 'surface';
  const isDefaultSegmentation = isLayerFile(volume.filename, 'aparc.DKTatlas+aseg.deep.mgz');
  return isSurface
    ? defaultVisibleSurface(volume.filename)
    : volume.filename === options.initialIntensityVolume?.filename || isDefaultSegmentation;
}

export function inferOutputVolumeLayerType(volume: OutputVolume): LoadableLayerType {
  if (volume.type === 'surface') return 'surface';
  if (volume.type === 'segmentation') return 'segmentation';
  const normalized = volume.filename.toLowerCase();
  return normalized.includes('aparc')
    || normalized.includes('aseg')
    || normalized.includes('seg')
    || normalized.includes('mask')
    || normalized.includes('cereb')
    || normalized.includes('wmparc')
    || normalized.includes('hypothalamus')
    ? 'segmentation'
    : 'intensity';
}

export function createViewerLayer(source: ViewerLayerSource, options: ViewerLayerOptions = {}): Volume {
  const baseLayer = {
    id: source.id ?? source.filename,
    name: layerDisplayName(source),
    filename: source.filename,
    artifactId: source.artifactId,
    url: source.url,
    opacity: source.type === 'segmentation' ? 0.7 : 1.0,
    colormap: source.type === 'surface' ? 'surface' : source.type === 'segmentation' ? '' : 'gray',
    visible: source.visible ?? options.defaultVisible ?? true,
  };

  if (source.type === 'surface') {
    const surfaceLayer = {
      ...baseLayer,
      type: 'surface' as const,
      curvatureUrl: source.curvatureUrl,
      annotationUrl: source.annotationUrl,
      curvatureNegativeThreshold: DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
      curvaturePositiveThreshold: DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
    };
    return {
      ...surfaceLayer,
      surfaceColorMode: defaultSurfaceColorModeForLayer(surfaceLayer),
    };
  }

  if (source.type === 'segmentation') {
    return {
      ...baseLayer,
      type: 'segmentation',
      lut: (source.lut === 'binary' || source.lut === 'freesurfer')
        ? source.lut
        : (isMaskLikeFilename(source.filename) ? 'binary' : 'freesurfer'),
      customLutUrl: source.customLutUrl,
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
