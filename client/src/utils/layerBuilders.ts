import {
  DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
  DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
} from '../constants.js';
import type { LayerType, OutputVolume, Volume } from '../types.js';
import { layerDisplayName } from './layerAliases.js';
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

export function outputVolumeLayerType(volume: OutputVolume): LoadableLayerType {
  return volume.type;
}

export function defaultOutputVolumeVisible(
  volume: OutputVolume,
  options: {
    initialIntensityVolume?: OutputVolume;
  },
): boolean {
  return volume.type === 'intensity' && volume.filename === options.initialIntensityVolume?.filename;
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
        : 'freesurfer',
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


/** Refresh file contents without discarding an existing layer's display choices. */
export function refreshViewerLayer(
  existing: Volume,
  loaded: Volume,
  overrides: { name?: string; visible?: boolean },
): Volume {
  if (existing.type !== loaded.type) return loaded;
  return {
    ...loaded,
    ...existing,
    url: loaded.url,
    artifactId: loaded.artifactId ?? existing.artifactId,
    name: overrides.name?.trim() ?? existing.name,
    visible: overrides.visible ?? existing.visible,
  };
}
