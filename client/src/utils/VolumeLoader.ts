/**
 * VolumeLoader – parse NIfTI / MGH buffers and canonicalize to RAS orientation.
 *
 * After parsing, dims[0]=R/L, dims[1]=A/P, dims[2]=I/S and the flat data
 * array is laid out as data[x + y*dimX + z*dimX*dimY].
 */
import * as nifti from 'nifti-reader-js';

// ── Public types ────────────────────────────────────────────────────────────

export type VoxelArray =
    | Uint8Array | Int16Array | Uint16Array
    | Int32Array | Uint32Array
    | Float32Array | Float64Array;

export type Matrix4x4 = [
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number],
];

export interface VolumeData {
    dims: [number, number, number];
    data: VoxelArray;
    voxelToWorld: Matrix4x4;
    worldToVoxel: Matrix4x4;
}

// ── Internal types ──────────────────────────────────────────────────────────

type Matrix3x3 = [[number, number, number], [number, number, number], [number, number, number]];

interface Orientation {
    axisOrder: [0 | 1 | 2, 0 | 1 | 2, 0 | 1 | 2];
    flips: [boolean, boolean, boolean];
}

interface NiftiHeader {
    dims: number[];
    datatypeCode: number;
    littleEndian: boolean;
    numBitsPerVoxel: number;
    [key: string]: unknown;
}

interface RawVolumeData {
    dims: [number, number, number];
    data: VoxelArray;
}

// ── Orientation helpers ─────────────────────────────────────────────────────

function asRow4(value: unknown): [number, number, number, number] | null {
    if (!Array.isArray(value) || value.length < 4) return null;
    const nums = value.slice(0, 4).map(Number);
    if (nums.some(Number.isNaN)) return null;
    return nums as [number, number, number, number];
}

function identity4(): Matrix4x4 {
    return [
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
    ];
}

export function multiplyAffine(a: Matrix4x4, b: Matrix4x4): Matrix4x4 {
    const out = identity4();
    for (let row = 0; row < 4; row++) {
        for (let col = 0; col < 4; col++) {
            out[row][col] = 0;
            for (let k = 0; k < 4; k++) {
                out[row][col] += a[row][k] * b[k][col];
            }
        }
    }
    return out;
}

function invert3x3(m: Matrix3x3): Matrix3x3 | null {
    const [
        [a, b, c],
        [d, e, f],
        [g, h, i],
    ] = m;
    const A = e * i - f * h;
    const B = -(d * i - f * g);
    const C = d * h - e * g;
    const D = -(b * i - c * h);
    const E = a * i - c * g;
    const F = -(a * h - b * g);
    const G = b * f - c * e;
    const H = -(a * f - c * d);
    const I = a * e - b * d;
    const det = a * A + b * B + c * C;
    if (Math.abs(det) < 1e-8) return null;
    const invDet = 1 / det;
    return [
        [A * invDet, D * invDet, G * invDet],
        [B * invDet, E * invDet, H * invDet],
        [C * invDet, F * invDet, I * invDet],
    ];
}

function invertAffine(affine: Matrix4x4): Matrix4x4 | null {
    const linear: Matrix3x3 = [
        [affine[0][0], affine[0][1], affine[0][2]],
        [affine[1][0], affine[1][1], affine[1][2]],
        [affine[2][0], affine[2][1], affine[2][2]],
    ];
    const invLinear = invert3x3(linear);
    if (!invLinear) return null;

    const translation = [affine[0][3], affine[1][3], affine[2][3]];
    const invTranslation: [number, number, number] = [
        -(invLinear[0][0] * translation[0] + invLinear[0][1] * translation[1] + invLinear[0][2] * translation[2]),
        -(invLinear[1][0] * translation[0] + invLinear[1][1] * translation[1] + invLinear[1][2] * translation[2]),
        -(invLinear[2][0] * translation[0] + invLinear[2][1] * translation[1] + invLinear[2][2] * translation[2]),
    ];

    return [
        [invLinear[0][0], invLinear[0][1], invLinear[0][2], invTranslation[0]],
        [invLinear[1][0], invLinear[1][1], invLinear[1][2], invTranslation[1]],
        [invLinear[2][0], invLinear[2][1], invLinear[2][2], invTranslation[2]],
        [0, 0, 0, 1],
    ];
}

function orientationFromAffine(affine: Matrix4x4): Orientation | null {
    return orientationFromMatrix([
        [affine[0][0], affine[0][1], affine[0][2]],
        [affine[1][0], affine[1][1], affine[1][2]],
        [affine[2][0], affine[2][1], affine[2][2]],
    ]);
}

function canonicalDims(
    dims: [number, number, number],
    ori: Orientation,
): [number, number, number] {
    return [
        dims[ori.axisOrder[0]],
        dims[ori.axisOrder[1]],
        dims[ori.axisOrder[2]],
    ];
}

function canonicalToRawTransform(
    dims: [number, number, number],
    ori: Orientation,
): Matrix4x4 {
    const out = canonicalDims(dims, ori);
    const transform = identity4();

    for (let row = 0; row < 3; row++) {
        transform[row][0] = 0;
        transform[row][1] = 0;
        transform[row][2] = 0;
        transform[row][3] = 0;
    }

    for (let canonicalAxis = 0; canonicalAxis < 3; canonicalAxis++) {
        const rawAxis = ori.axisOrder[canonicalAxis];
        const flipped = ori.flips[canonicalAxis];
        transform[rawAxis][canonicalAxis] = flipped ? -1 : 1;
        transform[rawAxis][3] = flipped ? out[canonicalAxis] - 1 : 0;
    }

    return transform;
}

/** Map columns of a vox→RAS rotation/scale matrix to canonical RAS axes. */
function orientationFromMatrix(m: Matrix3x3): Orientation | null {
    const order: [0 | 1 | 2, 0 | 1 | 2, 0 | 1 | 2] = [0, 1, 2];
    const flips: [boolean, boolean, boolean] = [false, false, false];
    const used = new Set<number>();

    for (let w = 0; w < 3; w++) {
        let best = -1, bestAbs = -1, bestVal = 0;
        for (let v = 0; v < 3; v++) {
            if (used.has(v)) continue;
            const mag = Math.abs(m[w][v]);
            if (mag > bestAbs) { bestAbs = mag; bestVal = m[w][v]; best = v; }
        }
        if (best < 0 || bestAbs <= 0) return null;
        order[w] = best as 0 | 1 | 2;
        flips[w] = bestVal < 0;
        used.add(best);
    }
    return { axisOrder: order, flips };
}

function getNiftiAffine(h: NiftiHeader): Matrix4x4 | null {
    const affine = (h as { affine?: unknown[] }).affine;
    if (Array.isArray(affine) && affine.length >= 3) {
        const rows = [asRow4(affine[0]), asRow4(affine[1]), asRow4(affine[2])];
        if (rows[0] && rows[1] && rows[2]) {
            return [
                rows[0],
                rows[1],
                rows[2],
                [0, 0, 0, 1],
            ];
        }
    }
    const srows = ['srow_x', 'srow_y', 'srow_z'].map(k => asRow4((h as Record<string, unknown>)[k]));
    if (srows[0] && srows[1] && srows[2]) {
        return [
            srows[0],
            srows[1],
            srows[2],
            [0, 0, 0, 1],
        ];
    }
    return null;
}

function defaultNiftiAffine(h: NiftiHeader): Matrix4x4 {
    const pixdim = (h as { pixDims?: number[] }).pixDims ?? [];
    const sx = Number(pixdim[1]) || 1;
    const sy = Number(pixdim[2]) || 1;
    const sz = Number(pixdim[3]) || 1;
    return [
        [sx, 0, 0, 0],
        [0, sy, 0, 0],
        [0, 0, sz, 0],
        [0, 0, 0, 1],
    ];
}

function getMghAffine(
    buf: ArrayBuffer,
    dims: [number, number, number],
): Matrix4x4 | null {
    if (buf.byteLength < 90) return null;
    const v = new DataView(buf);
    if (v.getInt16(28, false) !== 1) return null;

    const delta = [
        v.getFloat32(30, false),
        v.getFloat32(34, false),
        v.getFloat32(38, false),
    ] as [number, number, number];
    const dc: number[] = [];
    for (let off = 42; off <= 74; off += 4) {
        dc.push(v.getFloat32(off, false));
    }
    const center = [
        v.getFloat32(78, false),
        v.getFloat32(82, false),
        v.getFloat32(86, false),
    ] as [number, number, number];

    const mdcTransposed: Matrix3x3 = [
        [dc[0] * delta[0], dc[3] * delta[1], dc[6] * delta[2]],
        [dc[1] * delta[0], dc[4] * delta[1], dc[7] * delta[2]],
        [dc[2] * delta[0], dc[5] * delta[1], dc[8] * delta[2]],
    ];
    const volumeCenter: [number, number, number] = [
        (mdcTransposed[0][0] * dims[0] + mdcTransposed[0][1] * dims[1] + mdcTransposed[0][2] * dims[2]) / 2,
        (mdcTransposed[1][0] * dims[0] + mdcTransposed[1][1] * dims[1] + mdcTransposed[1][2] * dims[2]) / 2,
        (mdcTransposed[2][0] * dims[0] + mdcTransposed[2][1] * dims[1] + mdcTransposed[2][2] * dims[2]) / 2,
    ];

    return [
        [mdcTransposed[0][0], mdcTransposed[0][1], mdcTransposed[0][2], center[0] - volumeCenter[0]],
        [mdcTransposed[1][0], mdcTransposed[1][1], mdcTransposed[1][2], center[1] - volumeCenter[1]],
        [mdcTransposed[2][0], mdcTransposed[2][1], mdcTransposed[2][2], center[2] - volumeCenter[2]],
        [0, 0, 0, 1],
    ];
}

// ── Reorientation ───────────────────────────────────────────────────────────

function allocLike(src: VoxelArray, len: number): VoxelArray {
    if (src instanceof Uint8Array) return new Uint8Array(len);
    if (src instanceof Int16Array) return new Int16Array(len);
    if (src instanceof Uint16Array) return new Uint16Array(len);
    if (src instanceof Int32Array) return new Int32Array(len);
    if (src instanceof Uint32Array) return new Uint32Array(len);
    if (src instanceof Float32Array) return new Float32Array(len);
    return new Float64Array(len);
}

function reorient(
    data: VoxelArray,
    dims: [number, number, number],
    ori: Orientation,
): { data: VoxelArray; dims: [number, number, number] } {
    const [aX, aY, aZ] = ori.axisOrder;
    const [fX, fY, fZ] = ori.flips;
    const out: [number, number, number] = [dims[aX], dims[aY], dims[aZ]];
    const result = allocLike(data, out[0] * out[1] * out[2]);

    for (let z = 0; z < out[2]; z++) {
        for (let y = 0; y < out[1]; y++) {
            for (let x = 0; x < out[0]; x++) {
                const src = [0, 0, 0];
                src[aX] = fX ? out[0] - 1 - x : x;
                src[aY] = fY ? out[1] - 1 - y : y;
                src[aZ] = fZ ? out[2] - 1 - z : z;
                result[x + y * out[0] + z * out[0] * out[1]] =
                    data[src[0] + src[1] * dims[0] + src[2] * dims[0] * dims[1]];
            }
        }
    }
    return { data: result, dims: out };
}

export function transformVoxel(
    affine: Matrix4x4,
    voxel: [number, number, number],
): [number, number, number] {
    return [
        affine[0][0] * voxel[0] + affine[0][1] * voxel[1] + affine[0][2] * voxel[2] + affine[0][3],
        affine[1][0] * voxel[0] + affine[1][1] * voxel[1] + affine[1][2] * voxel[2] + affine[1][3],
        affine[2][0] * voxel[0] + affine[2][1] * voxel[1] + affine[2][2] * voxel[2] + affine[2][3],
    ];
}

export function sampleNearest(
    volume: VolumeData,
    voxel: [number, number, number],
): number {
    const x = Math.round(voxel[0]);
    const y = Math.round(voxel[1]);
    const z = Math.round(voxel[2]);
    if (
        x < 0 || y < 0 || z < 0 ||
        x >= volume.dims[0] || y >= volume.dims[1] || z >= volume.dims[2]
    ) {
        return 0;
    }
    return volume.data[x + y * volume.dims[0] + z * volume.dims[0] * volume.dims[1]];
}

// ── Format readers ──────────────────────────────────────────────────────────

function convertNiftiVoxels(h: NiftiHeader, image: ArrayBuffer): VoxelArray {
    const le = h.littleEndian;
    const dv = new DataView(image);
    const n = image.byteLength / (h.numBitsPerVoxel / 8);

    switch (h.datatypeCode) {
        case 2:   return new Uint8Array(image);
        case 4:   { const a = new Int16Array(n);   for (let i = 0; i < n; i++) a[i] = dv.getInt16(i * 2, le);   return a; }
        case 8:   { const a = new Int32Array(n);   for (let i = 0; i < n; i++) a[i] = dv.getInt32(i * 4, le);   return a; }
        case 16:  { const a = new Float32Array(n); for (let i = 0; i < n; i++) a[i] = dv.getFloat32(i * 4, le); return a; }
        case 64:  { const a = new Float64Array(n); for (let i = 0; i < n; i++) a[i] = dv.getFloat64(i * 8, le); return a; }
        case 512: { const a = new Uint16Array(n);  for (let i = 0; i < n; i++) a[i] = dv.getUint16(i * 2, le);  return a; }
        case 768: { const a = new Uint32Array(n);  for (let i = 0; i < n; i++) a[i] = dv.getUint32(i * 4, le);  return a; }
        default:  return new Int16Array(image);
    }
}

function parseMGH(buf: ArrayBuffer): RawVolumeData | null {
    const dv = new DataView(buf);
    if (dv.getInt32(0, false) !== 1) return null;           // version check

    const w = dv.getInt32(4, false);
    const h = dv.getInt32(8, false);
    const d = dv.getInt32(12, false);
    const type = dv.getInt32(20, false);
    const off = 284;
    const n = w * h * d;
    let data: VoxelArray;

    switch (type) {
        case 0: data = new Uint8Array(buf, off, n); break;
        case 1: {                                               // MRI_INT
            data = new Int32Array(n);
            for (let i = 0; i < n; i++) data[i] = dv.getInt32(off + i * 4, false);
            break;
        }
        case 3: {                                               // MRI_FLOAT
            data = new Float32Array(n);
            for (let i = 0; i < n; i++) data[i] = dv.getFloat32(off + i * 4, false);
            break;
        }
        case 4: {                                               // MRI_SHORT
            data = new Int16Array(n);
            for (let i = 0; i < n; i++) data[i] = dv.getInt16(off + i * 2, false);
            break;
        }
        default: return null;
    }
    return { dims: [w, h, d], data };
}

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Parse a (possibly gzip-compressed) NIfTI or MGH buffer and return
 * voxel data canonicalized to RAS orientation.
 */
export function parseVolume(buffer: ArrayBuffer): VolumeData | null {
    if (nifti.isNIFTI(buffer)) {
        const header = nifti.readHeader(buffer);
        const image = nifti.readImage(header, buffer);
        const h = header as unknown as NiftiHeader;
        const data = convertNiftiVoxels(h, image);
        const dims: [number, number, number] = [h.dims[1], h.dims[2], h.dims[3]];
        const rawAffine = getNiftiAffine(h) ?? defaultNiftiAffine(h);
        const ori = orientationFromAffine(rawAffine);
        const canonicalized = ori ? reorient(data, dims, ori) : { dims, data };
        const canonicalAffine = ori
            ? multiplyAffine(rawAffine, canonicalToRawTransform(dims, ori))
            : rawAffine;
        return {
            ...canonicalized,
            voxelToWorld: canonicalAffine,
            worldToVoxel: invertAffine(canonicalAffine) ?? identity4(),
        };
    }

    const mgh = parseMGH(buffer);
    if (mgh) {
        const rawAffine = getMghAffine(buffer, mgh.dims) ?? identity4();
        const ori = orientationFromAffine(rawAffine);
        const canonicalized = ori ? reorient(mgh.data, mgh.dims, ori) : mgh;
        const canonicalAffine = ori
            ? multiplyAffine(rawAffine, canonicalToRawTransform(mgh.dims, ori))
            : rawAffine;
        return {
            ...canonicalized,
            voxelToWorld: canonicalAffine,
            worldToVoxel: invertAffine(canonicalAffine) ?? identity4(),
        };
    }

    return null;
}

/**
 * Inspect voxel data to decide the appropriate LUT for a segmentation volume.
 *
 * - `'binary'`     – the volume contains only two values (typically 0 and 1),
 *                     e.g. a brain mask.
 * - `'freesurfer'` – the volume contains multiple integer labels that map to
 *                     the FreeSurfer colour look-up table.
 * - `undefined`    – the volume does not look like a segmentation (e.g. it
 *                     contains floating-point intensities).
 *
 * The check samples up to ~100 000 voxels for speed on large volumes. A volume
 * is classified as binary when it has at most 2 unique values and every sampled
 * voxel is exactly 0 or 1.
 */
export function detectLut(data: VoxelArray): 'binary' | 'freesurfer' | undefined {
    const len = data.length;
    if (len === 0) return undefined;

    // Sample stride – cap at ~100k samples for performance
    const stride = Math.max(1, Math.floor(len / 100_000));

    const uniqueValues = new Set<number>();
    let allInteger = true;
    let maxVal = -Infinity;

    for (let i = 0; i < len; i += stride) {
        const v = data[i];
        if (allInteger && v !== Math.floor(v)) {
            allInteger = false;
        }
        if (v > maxVal) maxVal = v;
        uniqueValues.add(v);
        // Early exit: once we see >2 unique values it can't be binary,
        // and once we have enough labels we know it's multi-label.
        if (uniqueValues.size > 2 && !allInteger) break;
    }

    if (!allInteger) return undefined;

    // Binary mask: exactly {0} or {0,1} or {1}
    if (uniqueValues.size <= 2) {
        const vals = [...uniqueValues];
        const isBinary = vals.every(v => v === 0 || v === 1);
        if (isBinary) return 'binary';
    }

    // Multi-label integer segmentation
    return 'freesurfer';
}
