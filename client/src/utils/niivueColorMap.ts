import type { LutMap } from './LutParser';
import type { NiivueColorMap } from './niivueInterop';

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
