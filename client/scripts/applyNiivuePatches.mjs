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

await writeFile(bundlePath, source);
