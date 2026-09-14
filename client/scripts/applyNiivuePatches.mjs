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
