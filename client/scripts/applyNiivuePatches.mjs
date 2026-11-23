import { readdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const clientDir = join(dirname(fileURLToPath(import.meta.url)), '..');
const niivueDistDir = join(clientDir, 'node_modules', '@niivue', 'niivue', 'dist');
// NiiVue bundle filenames carry content hashes that change between releases, so
// select the chunk that holds the 2D slice layout code by its contents instead.
const bundleCandidates = [];
for (const name of await readdir(niivueDistDir)) {
  if (!name.endsWith('.js')) continue;
  const text = await readFile(join(niivueDistDir, name), 'utf8');
  if (text.includes('tex2mm') && text.includes('leftTopWidthHeight')) bundleCandidates.push({ name, text });
}

if (bundleCandidates.length !== 1) {
  throw new Error(`Expected one NiiVue control bundle, found ${bundleCandidates.length}`);
}

const bundlePath = join(niivueDistDir, bundleCandidates[0].name);
let source = bundleCandidates[0].text;

// These patches target minified rc.12 code. When a NiiVue upgrade refactors the
// target, the patch is skipped with a warning instead of failing the build, so
// each skipped fix must be re-checked visually against the new NiiVue release.
function applyPinnedPatch(name, original, replacement) {
  if (source.includes(replacement)) return;
  const occurrences = source.split(original).length - 1;
  if (occurrences !== 1) {
    console.warn(`[applyNiivuePatches] SKIPPED "${name}": expected one match, found ${occurrences} (NiiVue bundle changed; verify this behaviour)`);
    return;
  }
  source = source.replace(original, replacement);
}

// Overlay source-over compositing is no longer patched: NiiVue rc.83 exposes it
// as volumeOverlayColorBlend OVER + volumeOverlayAlphaBlend UNION, which
// NiivuePane sets.

// Pointer-to-world mapping only needs a retained texture transform. NeuroCade
// keeps that small geometry after releasing the final volume and its GPU data.
applyPinnedPatch(
  'surface-only crosshair positioning',
  `  if (e.volumes.length === 0 || !e.tex2mm) return null;`,
  `  if (!e.tex2mm) return null;`,
);

// Grid tiles that fill their cells are no longer patched: NiiVue rc.83 supports
// customLayout tiles with `fill`, which NiivuePane sets via gridCustomLayout.

await writeFile(bundlePath, source);
