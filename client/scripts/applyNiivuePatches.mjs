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

// NiiVue 1.0.0-rc.10 adds premultiplied colors for every overlay but retains
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

// In WebGL2 the crosshair is drawn before meshes. Crosscut surface fragments
// then cover it even though the cursor is UI chrome. Draw it after normal meshes.
const crosshairDraw = `      S.space !== "global3d" && n.ui.is3DCrosshairVisible && !q && this.crosshairRenderer.isReady && this.crosshairRenderer.draw(
        e,
        F,
        N,
        S.axCorSag
      );
`;
applyPinnedPatch(
  'crosshair draw order removal',
  `${crosshairDraw}      const j =`,
  `      const j =`,
);
applyPinnedPatch(
  'crosshair draw order insertion',
  `      const xe = n.mesh.xRay;`,
  `${crosshairDraw}      const xe = n.mesh.xRay;`,
);

// NiiVue 1.0.0-rc.10 only emits the left and top orientation glyphs. Add the
// complementary right and bottom glyphs at the opposite edges of each 2D tile.
const topOrientationLabel = `        S.axCorSag === Q.AXIAL ? c.push(
          this.fontRenderer.buildText(
            "A",
            Z + ee / 2,
            W + me,
            re,
            f,
            0.5,
            0
          )
        ) : (S.axCorSag === Q.CORONAL || S.axCorSag === Q.SAGITTAL) && c.push(
          this.fontRenderer.buildText(
            "S",
            Z + ee / 2,
            W + me,
            re,
            f,
            0.5,
            0
          )
        );
`;
const allOrientationLabels = `${topOrientationLabel}        const le = S.axCorSag === Q.AXIAL || S.axCorSag === Q.CORONAL ? ie ? "L" : "R" : ie ? "P" : "A";
        c.push(
          this.fontRenderer.buildText(
            le,
            Z + ee - me,
            W + te / 2,
            re,
            f,
            1,
            0.5
          )
        );
        c.push(
          this.fontRenderer.buildText(
            S.axCorSag === Q.AXIAL ? "P" : "I",
            Z + ee / 2,
            W + te - me,
            re,
            f,
            0.5,
            1
          )
        );
`;
applyPinnedPatch(
  'four-sided orientation labels',
  topOrientationLabel,
  allOrientationLabels,
);

// Pointer-to-world mapping only needs a retained texture transform. NeuroCade
// keeps that small geometry after releasing the final volume and its GPU data.
applyPinnedPatch(
  'surface-only crosshair positioning',
  `  if (e.volumes.length === 0 || !e.tex2mm) return null;`,
  `  if (!e.tex2mm) return null;`,
);

// NiiVue's rectangle contrast mode always samples and updates volume zero.
// NeuroCade reserves volume zero for an invisible fixed reference grid, and
// renders real volumes from bottom to top. Target the topmost visible volume.
const firstVolumeRectangleContrast = `  if (e === ge.contrast && (t.dragStartXY[0] !== t.dragEndXY[0] || t.dragStartXY[1] !== t.dragEndXY[1])) {
    const n = _S(t);
    if (n) {
      const i = t.model.getVolumes()[0];
      i && (i.calMin = n.calMin, i.calMax = n.calMax, t.emit("volumeUpdated", {
        volumeIndex: 0,
        volume: i,
        changes: { calMin: n.calMin, calMax: n.calMax }
      }), t.updateGLVolume());
    }
  }
`;
const topmostVisibleVolumeRectangleContrast = `  if (e === ge.contrast && (t.dragStartXY[0] !== t.dragEndXY[0] || t.dragStartXY[1] !== t.dragEndXY[1])) {
    const n = t.model.getVolumes();
    let i = n.length - 1;
    for (; i >= 0 && !((n[i].opacity ?? 1) > 0); i--)
      ;
    const r = _S(t, i);
    if (r) {
      const s = n[i];
      s && (s.calMin = r.calMin, s.calMax = r.calMax, t.emit("volumeUpdated", {
        volumeIndex: i,
        volume: s,
        changes: { calMin: r.calMin, calMax: r.calMax }
      }), t.updateGLVolume());
    }
  }
`;
applyPinnedPatch(
  'topmost visible volume rectangle contrast',
  firstVolumeRectangleContrast,
  topmostVisibleVolumeRectangleContrast,
);

// NiiVue 1.0.0-rc.10 uses a 2D slice's aspect-fitted image rectangle as both
// its projection viewport and its clipping/hit-test viewport. This means zoomed
// anatomy remains clipped to the smaller initial-fit rectangle instead of using
// the rest of the canvas (or the rest of its Grid cell). Expand the viewport
// while padding the projected world bounds to the viewport aspect ratio. The
// image therefore has the same initial size and proportions, but zoom/pan can
// use the complete pane.
const verticalLayoutEnd = `  return n.map((c, f) => {
    const u = { leftTopWidthHeight: [o + (s - c.w) / 2, l, c.w, c.h] };
    return l += c.h + e, { ...u, ...i[f] ?? {} };
  });
};
`;
const expandedSliceViewportHelper = `${verticalLayoutEnd}function ncExpandSliceViewport(t, e) {
  const n = t.screen;
  if (!n || t.axCorSag === Q.RENDER)
    return { ...t, leftTopWidthHeight: e };
  const i = Fs(n), r = i.mxMM[0] - i.mnMM[0], s = i.mxMM[1] - i.mnMM[1], o = e[2] / e[3];
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
          (e[0] - p) / 2,
          (e[1] - x) / 2,
          p,
          x
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
    ...P,
    leftTopWidthHeight: [
      q + S.w + s,
      j + S.h + s,
      F,
      F
    ]
  }), Y;`;
const fullViewportGridReturn = `  E.hasRender && Y.push({
    ...P,
    leftTopWidthHeight: [
      q + S.w + s,
      j + S.h + s,
      F,
      F
    ]
  });
  const K = (e[0] - s) / 2, ne = (e[1] - s) / 2, xe = [
    [0, 0, K, ne],
    [0, ne + s, K, ne],
    [K + s, 0, K, ne],
    [K + s, ne + s, K, ne]
  ];
  return Y.map((ie, Z) => ncExpandSliceViewport(ie, xe[Z]));`;
applyPinnedPatch(
  'Grid cells use full viewport',
  fittedGridReturn,
  fullViewportGridReturn,
);

await writeFile(bundlePath, source);
