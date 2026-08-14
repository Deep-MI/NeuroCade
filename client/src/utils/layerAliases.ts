const VOLUME_ALIASES: Record<string, string> = {
  '001.mgz': 'Input image',
  'inpainting_original_image.mgz': 'Image before inpainting',
  'orig.mgz': 'Conformed input image',
  'mask.mgz': 'Brain mask',
  'orig_nu.mgz': 'Bias field corrected image',
  'FLAIR_n4_maskout_norm.nii.gz': 'FLAIR image',
  'T2_caipi.nii.gz': 'T2-weighted image',
  'aparc.DKTatlas+aseg.mgz': 'Whole-brain parcellation',
  'WMH_FLAIR.nii.gz': 'White matter hyperintensities',
  'ob_seg.mgz': 'Olfactory bulb segmentation',
  'aparc.DKTatlas+aseg.deep.mgz': 'Whole brain segm. (cortical+subcort.)',
  'aseg.auto_noCCseg.mgz': 'Subcortical segm.',
  'cerebellum.CerebNet.nii.gz': 'Cerebellum sub-segm.',
  'hypothalamus.HypVINN.nii.gz': 'Hypothalamus sub-segm.',
  'hypothalamus_mask.HypVINN.nii.gz': 'Hypothalamus mask (HypVINN)',
  'hypothalamus_bin.nii.gz': 'Hypothalamus binary mask',
  'aparc.DKTatlas+aseg.deep.withCC.mgz': 'Whole brain segm. after surface recon.',
  'wmparc.DKTatlas.mapped.mgz': 'White matter parcellation',
};

const SURFACE_LABELS: Record<string, string> = {
  pial: 'pial surface',
  white: 'white matter surface',
  inflated: 'inflated surface',
  sphere: 'spherical surface',
  smoothwm: 'smooth white matter surface',
  orig: 'original surface',
};

const HEMISPHERE_LABELS: Record<string, string> = { lh: 'Left', rh: 'Right' };
const basename = (path: string): string => path.split(/[\\/]/).pop() ?? path;
export const surfaceFileStem = (filename: string): string => basename(filename).replace(/\.surf$/, '');

function surfaceAlias(filename: string): string | undefined {
  const match = /^([lr]h)\.([^.]+)$/.exec(surfaceFileStem(filename));
  if (!match) return undefined;
  const [, hemisphere, surface] = match;
  const hemisphereLabel = HEMISPHERE_LABELS[hemisphere];
  const surfaceLabel = SURFACE_LABELS[surface];
  return hemisphereLabel && surfaceLabel ? `${hemisphereLabel} ${surfaceLabel}` : undefined;
}

export function layerDisplayName(layer: { filename: string; name?: string | null }): string {
  const fallback = layer.name?.trim();
  if (fallback && fallback !== layer.filename && fallback !== basename(layer.filename)) {
    return fallback;
  }

  const file = basename(layer.filename);
  return VOLUME_ALIASES[file] ?? surfaceAlias(file) ?? fallback ?? layer.filename;
}
