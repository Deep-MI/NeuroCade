import { readdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const clientDir = join(dirname(fileURLToPath(import.meta.url)), '..');
const niivueDistDir = join(clientDir, 'node_modules', '@niivue', 'niivue', 'dist');
const candidates = (await readdir(niivueDistDir))
  .filter((name) => /^NVControlBase-.*\.js$/.test(name));

if (candidates.length !== 1) {
  throw new Error(`Expected one NiiVue control bundle, found ${candidates.length}`);
}

const bundlePath = join(niivueDistDir, candidates[0]);
let source = await readFile(bundlePath, 'utf8');

function applyPinnedPatch(name, original, replacement) {
  if (source.includes(replacement)) return;
  const occurrences = source.split(original).length - 1;
  if (occurrences !== 1) {
    throw new Error(`Could not apply NiiVue ${name} patch: expected one match, found ${occurrences}`);
  }
  source = source.replace(original, replacement);
}

// NiiVue 1.0.0-rc.12 adds premultiplied colors for every overlay but retains
// only max(alpha). Overlapping half-opacity labels are consequently unpremultiplied
// to roughly twice their intended brightness. Composite each layer source-over.
applyPinnedPatch(
  'overlay source-over compositing',
  `      l <= 0 || (i[a] += s[a] / 255 * l, i[a + 1] += s[a + 1] / 255 * l, i[a + 2] += s[a + 2] / 255 * l, i[a + 3] = Math.max(i[a + 3], l));`,
  `      l <= 0 || (i[a] = s[a] / 255 * l + i[a] * (1 - l), i[a + 1] = s[a + 1] / 255 * l + i[a + 1] * (1 - l), i[a + 2] = s[a + 2] / 255 * l + i[a + 2] * (1 - l), i[a + 3] = l + i[a + 3] * (1 - l));`,
);
applyPinnedPatch(
  'WebGPU overlay source-over compositing',
  `    accum[idx] = vec4f(cur.x + rgba.x * a, cur.y + rgba.y * a, cur.z + rgba.z * a, max(cur.w, a));`,
  `    accum[idx] = vec4f(rgba.x * a + cur.x * (1.0 - a), rgba.y * a + cur.y * (1.0 - a), rgba.z * a + cur.z * (1.0 - a), a + cur.w * (1.0 - a));`,
);

// Pointer-to-world mapping only needs a retained texture transform. NeuroCade
// keeps that small geometry after releasing the final volume and its GPU data.
applyPinnedPatch(
  'surface-only crosshair positioning',
  `  if (e.volumes.length === 0 || !e.tex2mm) return null;`,
  `  if (!e.tex2mm) return null;`,
);

await writeFile(bundlePath, source);
