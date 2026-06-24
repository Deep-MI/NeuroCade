export interface LabelLookupResult {
  index: number;
  name: string;
  color?: [number, number, number];
}

interface LabelLookupLut {
  min?: number;
  lut: ArrayLike<number>;
  labels?: string[];
}

export function labelInfoFromLut(labelIndex: number, labelLut?: LabelLookupLut | null): LabelLookupResult {
  const labelOffset = labelIndex - (labelLut?.min ?? 0);
  const colorOffset = labelOffset * 4;
  const color = labelLut?.lut && colorOffset >= 0 && colorOffset + 2 < labelLut.lut.length
    ? [
        labelLut.lut[colorOffset],
        labelLut.lut[colorOffset + 1],
        labelLut.lut[colorOffset + 2],
      ] as [number, number, number]
    : undefined;
  const name = labelOffset >= 0 ? labelLut?.labels?.[labelOffset] : undefined;
  return {
    index: labelIndex,
    name: name && name !== '?' ? name : `Label ${labelIndex}`,
    color,
  };
}
