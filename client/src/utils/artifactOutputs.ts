import type { LayerType } from '../types.js';

type ViewerLayerType = Exclude<LayerType, 'drawing'>;

/** Map a configured workflow output type to a viewer layer, or null for non-viewer outputs. */
export function configuredOutputLayerType(value: unknown): ViewerLayerType | null | undefined {
  if (value === undefined) return undefined;
  if (value === 'intensity_volume') return 'intensity';
  if (value === 'segmentation_volume') return 'segmentation';
  if (value === 'surface') return 'surface';
  return null;
}
