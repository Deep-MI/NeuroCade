export interface SurfaceAnnotationData {
  labels: Int32Array;
  colorTable: Uint8Array;
  names: string[];
}

const MZ3_MAGIC = 23_117;
const MZ3_SCALAR_ATTRIBUTE = 8;
const MZ3_LABEL_COLORMAP_ATTRIBUTE = 64;
const FREESURFER_CURV_NEW_MAGIC = 0xFF_FF_FF;

function readUint24(view: DataView, offset: number): number {
  if (offset + 3 > view.byteLength) {
    throw new Error('FreeSurfer curvature file is truncated.');
  }
  return (view.getUint8(offset) << 16)
    | (view.getUint8(offset + 1) << 8)
    | view.getUint8(offset + 2);
}

export function parseFreeSurferCurvature(
  buffer: ArrayBuffer,
  expectedVertexCount?: number,
): Float32Array {
  const view = new DataView(buffer);
  const magicOrVertexCount = readUint24(view, 0);
  let vertexCount: number;
  let values: Float32Array;

  if (magicOrVertexCount === FREESURFER_CURV_NEW_MAGIC) {
    if (view.byteLength < 15) {
      throw new Error('FreeSurfer curvature header is truncated.');
    }
    vertexCount = view.getUint32(3, false);
    const valuesPerVertex = view.getUint32(11, false);
    if (valuesPerVertex !== 1) {
      throw new Error(`Unsupported FreeSurfer curvature values-per-vertex count ${valuesPerVertex}.`);
    }
    if (view.byteLength < 15 + vertexCount * 4) {
      throw new Error('FreeSurfer curvature values are truncated.');
    }
    values = new Float32Array(vertexCount);
    for (let index = 0; index < vertexCount; index += 1) {
      values[index] = view.getFloat32(15 + index * 4, false);
    }
  } else {
    // Legacy files store the vertex and face counts as 24-bit integers,
    // followed by signed hundredths in big-endian int16 values.
    vertexCount = magicOrVertexCount;
    if (view.byteLength < 6 + vertexCount * 2) {
      throw new Error('FreeSurfer curvature values are truncated.');
    }
    values = new Float32Array(vertexCount);
    for (let index = 0; index < vertexCount; index += 1) {
      values[index] = view.getInt16(6 + index * 2, false) / 100;
    }
  }

  if (vertexCount <= 0) {
    throw new Error('FreeSurfer curvature file has no vertices.');
  }
  if (expectedVertexCount !== undefined && vertexCount !== expectedVertexCount) {
    throw new Error(`Curvature vertex count ${vertexCount} does not match surface vertex count ${expectedVertexCount}.`);
  }
  return values;
}

/**
 * NiiVue normalizes and inverts native `.curv` values while loading them.
 * Wrapping the raw signed scalars in MZ3 preserves the FreeSurfer scale so
 * negative and positive curvature can use independent display endpoints.
 */
export function freeSurferCurvatureToMz3(buffer: ArrayBuffer, expectedVertexCount?: number): ArrayBuffer {
  const values = parseFreeSurferCurvature(buffer, expectedVertexCount);
  const headerLength = 16;
  const output = new ArrayBuffer(headerLength + values.length * 4);
  const view = new DataView(output);
  view.setUint16(0, MZ3_MAGIC, true);
  view.setUint16(2, MZ3_SCALAR_ATTRIBUTE, true);
  view.setUint32(4, 0, true);
  view.setUint32(8, values.length, true);
  view.setUint32(12, 0, true);
  for (let index = 0; index < values.length; index += 1) {
    view.setFloat32(headerLength + index * 4, values[index], true);
  }
  return output;
}

function readInt32(view: DataView, offsetRef: { value: number }): number {
  if (offsetRef.value + 4 > view.byteLength) {
    throw new Error('FreeSurfer annotation file is truncated.');
  }
  const value = view.getInt32(offsetRef.value, false);
  offsetRef.value += 4;
  return value;
}

function readFsString(view: DataView, offsetRef: { value: number }): string {
  const length = readInt32(view, offsetRef);
  if (length < 0 || offsetRef.value + length > view.byteLength) {
    throw new Error('FreeSurfer annotation string is truncated.');
  }
  const bytes = new Uint8Array(view.buffer, view.byteOffset + offsetRef.value, length);
  offsetRef.value += length;
  const end = bytes.at(-1) === 0 ? bytes.length - 1 : bytes.length;
  return new TextDecoder().decode(bytes.slice(0, end));
}

function packRgb(red: number, green: number, blue: number): number {
  return red + (green << 8) + (blue << 16);
}

function appendAnnotationEntry(
  colors: number[][],
  names: string[],
  index: number,
  name: string,
  red: number,
  green: number,
  blue: number,
  transparency: number,
): void {
  colors[index] = [red, green, blue, transparency, packRgb(red, green, blue)];
  names[index] = name;
}

export function parseFreeSurferAnnotation(buffer: ArrayBuffer, expectedVertexCount?: number): SurfaceAnnotationData {
  const view = new DataView(buffer);
  const offsetRef = { value: 0 };
  const vertexCount = readInt32(view, offsetRef);
  if (vertexCount <= 0) {
    throw new Error('FreeSurfer annotation has no vertices.');
  }
  if (expectedVertexCount !== undefined && vertexCount !== expectedVertexCount) {
    throw new Error(`Annotation vertex count ${vertexCount} does not match surface vertex count ${expectedVertexCount}.`);
  }
  if (offsetRef.value + vertexCount * 8 > buffer.byteLength) {
    throw new Error('FreeSurfer annotation vertex data is truncated.');
  }

  const rawLabels = new Int32Array(vertexCount);
  for (let index = 0; index < vertexCount; index += 1) {
    offsetRef.value += 4; // Vertex ID; labels are stored in surface vertex order.
    rawLabels[index] = readInt32(view, offsetRef);
  }

  if (!readInt32(view, offsetRef)) {
    throw new Error('FreeSurfer annotation color table is missing.');
  }

  const entryCountOrVersion = readInt32(view, offsetRef);
  const colors: number[][] = [];
  const names: string[] = [];
  if (entryCountOrVersion > 0) {
    readFsString(view, offsetRef); // Original table path.
    for (let index = 0; index < entryCountOrVersion; index += 1) {
      const name = readFsString(view, offsetRef);
      appendAnnotationEntry(
        colors,
        names,
        index,
        name,
        readInt32(view, offsetRef),
        readInt32(view, offsetRef),
        readInt32(view, offsetRef),
        readInt32(view, offsetRef),
      );
    }
  } else {
    const version = -entryCountOrVersion;
    if (version !== 2) {
      throw new Error(`Unsupported FreeSurfer annotation color table version ${version}.`);
    }
    const maxIndex = readInt32(view, offsetRef);
    if (maxIndex <= 0) {
      throw new Error('FreeSurfer annotation color table is empty.');
    }
    readFsString(view, offsetRef); // Original table path.
    const entriesToRead = readInt32(view, offsetRef);
    for (let entry = 0; entry < entriesToRead; entry += 1) {
      const index = readInt32(view, offsetRef);
      if (index < 0 || index >= maxIndex) {
        throw new Error(`FreeSurfer annotation color table index ${index} is out of range.`);
      }
      const name = readFsString(view, offsetRef);
      appendAnnotationEntry(
        colors,
        names,
        index,
        name,
        readInt32(view, offsetRef),
        readInt32(view, offsetRef),
        readInt32(view, offsetRef),
        readInt32(view, offsetRef),
      );
    }
  }

  const packedToIndex = new Map<number, number>();
  for (let index = 0; index < colors.length; index += 1) {
    const color = colors[index];
    if (color) packedToIndex.set(color[4], index);
  }

  const labels = new Int32Array(vertexCount);
  for (let index = 0; index < rawLabels.length; index += 1) {
    labels[index] = rawLabels[index] === 0 ? -1 : packedToIndex.get(rawLabels[index]) ?? -1;
  }

  const colorTable = new Uint8Array(colors.length * 4);
  for (let index = 0; index < colors.length; index += 1) {
    const color = colors[index] ?? [128, 128, 128, 0];
    const offset = index * 4;
    colorTable[offset] = color[0];
    colorTable[offset + 1] = color[1];
    colorTable[offset + 2] = color[2];
    colorTable[offset + 3] = Math.max(0, Math.min(255, 255 - color[3]));
  }

  return { labels, colorTable, names };
}

/**
 * NiiVue 1.0 cannot load `.annot` through addMeshLayer, but it can load an MZ3
 * scalar layer with an embedded label table.
 */
export function freeSurferAnnotationToMz3(buffer: ArrayBuffer, expectedVertexCount?: number): ArrayBuffer {
  const annotation = parseFreeSurferAnnotation(buffer, expectedVertexCount);
  const colorMap = {
    R: [0],
    G: [0],
    B: [0],
    A: [0],
    I: [0],
    labels: ['Unassigned'],
  };

  for (let index = 0; index < annotation.colorTable.length / 4; index += 1) {
    const colorOffset = index * 4;
    colorMap.R.push(annotation.colorTable[colorOffset]);
    colorMap.G.push(annotation.colorTable[colorOffset + 1]);
    colorMap.B.push(annotation.colorTable[colorOffset + 2]);
    colorMap.A.push(annotation.colorTable[colorOffset + 3]);
    colorMap.I.push(index + 1);
    colorMap.labels.push(annotation.names[index] || `Region ${index}`);
  }

  const encodedColorMap = new TextEncoder().encode(JSON.stringify(colorMap));
  const headerLength = 16;
  const output = new ArrayBuffer(headerLength + encodedColorMap.byteLength + annotation.labels.length * 4);
  const view = new DataView(output);
  view.setUint16(0, MZ3_MAGIC, true);
  view.setUint16(2, MZ3_SCALAR_ATTRIBUTE | MZ3_LABEL_COLORMAP_ATTRIBUTE, true);
  view.setUint32(4, 0, true);
  view.setUint32(8, annotation.labels.length, true);
  view.setUint32(12, encodedColorMap.byteLength, true);
  new Uint8Array(output, headerLength, encodedColorMap.byteLength).set(encodedColorMap);

  let scalarOffset = headerLength + encodedColorMap.byteLength;
  for (const label of annotation.labels) {
    view.setFloat32(scalarOffset, label < 0 ? 0 : label + 1, true);
    scalarOffset += 4;
  }
  return output;
}
