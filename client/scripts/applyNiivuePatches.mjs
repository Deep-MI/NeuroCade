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

// NiiVue aspect-fits each 2D slice and then also uses that smaller rectangle as
// its clipping and hit-test viewport. Keep the fitted anatomy undistorted, but
// pad its world bounds so the complete canvas (or Grid cell) remains usable.
const verticalLayoutEnd = `  return n.map((c, h) => {
    const u = { leftTopWidthHeight: [o + (s - c.w) / 2, l, c.w, c.h] };
    return l += c.h + e, { ...u, ...i[h] ?? {} };
  });
};
`;
const expandedSliceViewportHelper = `${verticalLayoutEnd}function ncExpandSliceViewport(t, e) {
  const n = t.screen;
  if (!n || t.axCorSag === Q.RENDER)
    return { ...t, leftTopWidthHeight: e };
  const i = yo(n), r = i.mxMM[0] - i.mnMM[0], s = i.mxMM[1] - i.mnMM[1], o = e[2] / e[3];
  if (!(r > 0 && s > 0 && Number.isFinite(o) && o > 0))
    return { ...t, screen: i, leftTopWidthHeight: e };
  if (r / s < o) {
    const a = (s * o - r) / 2;
    i.mnMM[0] -= a, i.mxMM[0] += a, i.fovMM[0] = i.mxMM[0] - i.mnMM[0];
  } else {
    const a = (r / o - s) / 2;
    i.mnMM[1] -= a, i.mxMM[1] += a, i.fovMM[1] = i.mxMM[1] - i.mnMM[1];
  }
  return { ...t, screen: i, leftTopWidthHeight: e };
}
`;
applyPinnedPatch(
  'expanded slice viewport helper',
  verticalLayoutEnd,
  expandedSliceViewportHelper,
);

const fittedSingleSlice = `    return [
      {
        ...d[u],
        leftTopWidthHeight: [
          (e[0] - x) / 2,
          (e[1] - p) / 2,
          x,
          p
        ]
      }
    ];`;
const fullViewportSingleSlice = `    const y = [0, 0, e[0], e[1]];
    return [
      ncExpandSliceViewport({ ...d[u], leftTopWidthHeight: y }, y)
    ];`;
applyPinnedPatch(
  'single-slice full viewport',
  fittedSingleSlice,
  fullViewportSingleSlice,
);

const fittedGridReturn = `  return E.hasRender && Y.push({
    ...D,
    leftTopWidthHeight: [
      q + A.w + s,
      j + A.h + s,
      O,
      O
    ]
  }), Y;`;
const fullViewportGridReturn = `  E.hasRender && Y.push({
    ...D,
    leftTopWidthHeight: [
      q + A.w + s,
      j + A.h + s,
      O,
      O
    ]
  });
  const K = (e[0] - s) / 2, J = (e[1] - s) / 2, ne = [
    [0, 0, K, J],
    [0, J + s, K, J],
    [K + s, 0, K, J],
    [K + s, J + s, K, J]
  ];
  return Y.map((xe, ie) => ncExpandSliceViewport(xe, ne[ie]));`;
applyPinnedPatch(
  'Grid cells use full viewport',
  fittedGridReturn,
  fullViewportGridReturn,
);

await writeFile(bundlePath, source);
