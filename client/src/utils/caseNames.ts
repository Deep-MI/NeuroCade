export const SLUG_NAME_RE = /^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$/;

export function toSlugName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
    .replace(/-+$/g, '');
}

export function inferCaseName(filename: string): string {
  if (filename.toLowerCase().endsWith('.nii.gz')) {
    return toSlugName(filename.slice(0, -7));
  }
  if (filename.toLowerCase().endsWith('.nii') || filename.toLowerCase().endsWith('.mgz')) {
    return toSlugName(filename.replace(/\.(nii|mgz)$/i, ''));
  }
  return toSlugName(filename.replace(/\.[^.]+$/u, ''));
}

export function getSlugNameValidationError(value: string, label: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return `${label} cannot be empty.`;
  }
  if (!SLUG_NAME_RE.test(trimmed)) {
    return 'Use a lowercase slug, 2-64 characters, with only a-z, 0-9, and hyphen.';
  }
  return null;
}

export function getCaseNameValidationError(value: string): string | null {
  return getSlugNameValidationError(value, 'Case name');
}
