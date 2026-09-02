import type { LutMap } from './LutParser';
import type { NiivueColorMap } from './niivueInterop';

interface NiivueLabelLut {
  lut: Uint8ClampedArray;
  min: number;
  max: number;
  labels?: string[];
}

export function lutMapToNiivueColorMap(lut: LutMap): NiivueColorMap {
  const entries = [...lut.entries()].sort(([left], [right]) => left - right);
  return {
    R: entries.map(([, entry]) => entry.rgb[0]),
    G: entries.map(([, entry]) => entry.rgb[1]),
    B: entries.map(([, entry]) => entry.rgb[2]),
    A: entries.map(([index, entry]) => index === 0 ? 0 : entry.alpha),
    I: entries.map(([index]) => index),
    labels: entries.map(([, entry]) => entry.name),
  };
}

export function compileNiivueLabelColorMap(colorMap: NiivueColorMap): NiivueLabelLut {
  const indices = colorMap.I ?? colorMap.R.map((_, index) => index);
  if (
    colorMap.R.length !== colorMap.G.length
    || colorMap.R.length !== colorMap.B.length
    || colorMap.R.length !== indices.length
  ) {
    throw new Error('Label colormap channels must have matching lengths.');
  }
  const min = Math.min(...indices);
  const max = Math.max(...indices);
  const lut = new Uint8ClampedArray((max - min + 1) * 4);
  for (let index = 0; index < indices.length; index += 1) {
    const offset = (indices[index] - min) * 4;
    lut[offset] = colorMap.R[index];
    lut[offset + 1] = colorMap.G[index];
    lut[offset + 2] = colorMap.B[index];
    lut[offset + 3] = colorMap.A?.[index] ?? (indices[index] === 0 ? 0 : 255);
  }
  const labels = colorMap.labels
    ? Array.from({ length: max - min + 1 }, () => '?')
    : undefined;
  if (labels && colorMap.labels) {
    for (let index = 0; index < indices.length; index += 1) {
      labels[indices[index] - min] = colorMap.labels[index] ?? '?';
    }
  }
  return { lut, min, max, labels };
}
