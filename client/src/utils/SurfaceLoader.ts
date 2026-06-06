export interface SurfaceMeshData {
    vertices: Float32Array;
    normals: Float32Array;
    indices: Uint32Array;
    vertexCount: number;
    faceCount: number;
}

export interface SurfaceAnnotationData {
    labels: Int32Array;
    colorTable: Uint8Array;
    names: string[];
}

const TRIANGLE_FILE_MAGIC = 16_777_214;
const NEW_CURV_FILE_MAGIC = 16_777_215;

function readInt3(view: DataView, offset: number): number {
    return (view.getUint8(offset) << 16) | (view.getUint8(offset + 1) << 8) | view.getUint8(offset + 2);
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
    for (let i = 0; i < vertexCount; i += 1) {
        offsetRef.value += 4; // vertex id; labels are stored in file order for surface vertices
        rawLabels[i] = readInt32(view, offsetRef);
    }

    const colorTableExists = readInt32(view, offsetRef);
    if (!colorTableExists) {
        throw new Error('FreeSurfer annotation color table is missing.');
    }

    const entryCountOrVersion = readInt32(view, offsetRef);
    const colors: number[][] = [];
    const names: string[] = [];
    if (entryCountOrVersion > 0) {
        const entryCount = entryCountOrVersion;
        readFsString(view, offsetRef); // original table path
        for (let index = 0; index < entryCount; index += 1) {
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
        readFsString(view, offsetRef); // original table path
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
    for (let i = 0; i < rawLabels.length; i += 1) {
        labels[i] = rawLabels[i] === 0 ? -1 : packedToIndex.get(rawLabels[i]) ?? -1;
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

export function parseFreeSurferCurvature(buffer: ArrayBuffer, expectedVertexCount?: number): Float32Array {
    const view = new DataView(buffer);
    const magic = readInt3(view, 0);
    let offset = 3;
    let vertexCount: number;
    let values: Float32Array;

    if (magic === NEW_CURV_FILE_MAGIC) {
        if (buffer.byteLength < 15) {
            throw new Error('FreeSurfer curvature header is truncated.');
        }
        vertexCount = view.getInt32(offset, false);
        offset += 4;
        offset += 4; // face count, not needed for vertex coloring
        const valuesPerVertex = view.getInt32(offset, false);
        offset += 4;
        if (valuesPerVertex !== 1) {
            throw new Error(`Unsupported curvature value count ${valuesPerVertex}.`);
        }
        if (offset + vertexCount * 4 > buffer.byteLength) {
            throw new Error('FreeSurfer curvature payload is truncated.');
        }
        values = new Float32Array(vertexCount);
        for (let i = 0; i < vertexCount; i += 1) {
            values[i] = view.getFloat32(offset, false);
            offset += 4;
        }
    } else {
        vertexCount = magic;
        const faceCount = readInt3(view, offset);
        offset += 3;
        if (vertexCount <= 0 || faceCount < 0 || offset + vertexCount * 2 > buffer.byteLength) {
            throw new Error('Unsupported or truncated old-format FreeSurfer curvature file.');
        }
        values = new Float32Array(vertexCount);
        for (let i = 0; i < vertexCount; i += 1) {
            values[i] = view.getInt16(offset, false) / 100;
            offset += 2;
        }
    }

    if (expectedVertexCount !== undefined && vertexCount !== expectedVertexCount) {
        throw new Error(`Curvature vertex count ${vertexCount} does not match surface vertex count ${expectedVertexCount}.`);
    }

    return values;
}

function skipLine(bytes: Uint8Array, offset: number): number {
    let cursor = offset;
    while (cursor < bytes.length && bytes[cursor] !== 10) cursor += 1;
    return Math.min(cursor + 1, bytes.length);
}

function computeVertexNormals(vertices: Float32Array, indices: Uint32Array): Float32Array {
    const normals = new Float32Array(vertices.length);

    for (let i = 0; i < indices.length; i += 3) {
        const i0 = indices[i] * 3;
        const i1 = indices[i + 1] * 3;
        const i2 = indices[i + 2] * 3;

        const ax = vertices[i1] - vertices[i0];
        const ay = vertices[i1 + 1] - vertices[i0 + 1];
        const az = vertices[i1 + 2] - vertices[i0 + 2];
        const bx = vertices[i2] - vertices[i0];
        const by = vertices[i2 + 1] - vertices[i0 + 1];
        const bz = vertices[i2 + 2] - vertices[i0 + 2];

        const nx = ay * bz - az * by;
        const ny = az * bx - ax * bz;
        const nz = ax * by - ay * bx;

        normals[i0] += nx;
        normals[i0 + 1] += ny;
        normals[i0 + 2] += nz;
        normals[i1] += nx;
        normals[i1 + 1] += ny;
        normals[i1 + 2] += nz;
        normals[i2] += nx;
        normals[i2 + 1] += ny;
        normals[i2 + 2] += nz;
    }

    for (let i = 0; i < normals.length; i += 3) {
        const x = normals[i];
        const y = normals[i + 1];
        const z = normals[i + 2];
        const length = Math.hypot(x, y, z) || 1;
        normals[i] = x / length;
        normals[i + 1] = y / length;
        normals[i + 2] = z / length;
    }

    return normals;
}

export function parseFreeSurferSurface(buffer: ArrayBuffer): SurfaceMeshData {
    const view = new DataView(buffer);
    const bytes = new Uint8Array(buffer);
    const magic = readInt3(view, 0);

    if (magic !== TRIANGLE_FILE_MAGIC) {
        throw new Error(`Unsupported FreeSurfer surface magic ${magic}. Only binary triangle surfaces are supported.`);
    }

    let offset = 3;
    offset = skipLine(bytes, offset);
    offset = skipLine(bytes, offset);

    if (offset + 8 > buffer.byteLength) {
        throw new Error('FreeSurfer surface header is truncated.');
    }

    const vertexCount = view.getInt32(offset, false);
    offset += 4;
    const faceCount = view.getInt32(offset, false);
    offset += 4;

    if (vertexCount <= 0 || faceCount <= 0) {
        throw new Error('FreeSurfer surface has no vertices or faces.');
    }

    const vertexValues = vertexCount * 3;
    const faceValues = faceCount * 3;
    const expectedBytes = offset + vertexValues * 4 + faceValues * 4;
    if (expectedBytes > buffer.byteLength) {
        throw new Error('FreeSurfer surface payload is truncated.');
    }

    const vertices = new Float32Array(vertexValues);
    for (let i = 0; i < vertexValues; i += 1) {
        vertices[i] = view.getFloat32(offset, false);
        offset += 4;
    }

    const indices = new Uint32Array(faceValues);
    for (let i = 0; i < faceValues; i += 1) {
        indices[i] = view.getUint32(offset, false);
        offset += 4;
    }

    return {
        vertices,
        normals: computeVertexNormals(vertices, indices),
        indices,
        vertexCount,
        faceCount,
    };
}
