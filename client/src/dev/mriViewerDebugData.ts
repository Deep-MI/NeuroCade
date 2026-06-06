import type { Volume } from '../types';

type Mat4 = [
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number],
    [number, number, number, number],
];

type TypedVolumeData = Float32Array | Uint8Array;

const DEMO_DIMS: [number, number, number] = [40, 40, 24];
const TARGET_COORDINATE: [number, number, number] = [20, 20, 12];
const SEGMENTATION_HEADER_OFFSET_MM: [number, number, number] = [4, 0, 0];

export interface HeaderMismatchDebugFixture {
    volumes: Volume[];
    targetCoordinate: [number, number, number];
    segmentationHeaderOffsetMm: [number, number, number];
    cleanup: () => void;
}

function voxelIndex(
    dims: readonly [number, number, number],
    x: number,
    y: number,
    z: number,
): number {
    return x + y * dims[0] + z * dims[0] * dims[1];
}

function writeCube(
    data: TypedVolumeData,
    dims: readonly [number, number, number],
    start: readonly [number, number, number],
    end: readonly [number, number, number],
    value: number,
) {
    for (let z = start[2]; z < end[2]; z++) {
        for (let y = start[1]; y < end[1]; y++) {
            for (let x = start[0]; x < end[0]; x++) {
                data[voxelIndex(dims, x, y, z)] = value;
            }
        }
    }
}

function createIntensityVolumeData(): Float32Array {
    const data = new Float32Array(DEMO_DIMS[0] * DEMO_DIMS[1] * DEMO_DIMS[2]);
    for (let z = 0; z < DEMO_DIMS[2]; z++) {
        for (let y = 0; y < DEMO_DIMS[1]; y++) {
            for (let x = 0; x < DEMO_DIMS[0]; x++) {
                data[voxelIndex(DEMO_DIMS, x, y, z)] = x * 3 + y * 2 + z;
            }
        }
    }

    writeCube(data, DEMO_DIMS, [18, 18, 10], [23, 23, 15], 400);
    return data;
}

function createSegmentationVolumeData(): Uint8Array {
    const data = new Uint8Array(DEMO_DIMS[0] * DEMO_DIMS[1] * DEMO_DIMS[2]);
    // The structure matches the intensity cube in world space, but the
    // segmentation header applies a +4 mm translation in X. That means the
    // labelled voxels live at lower segmentation voxel indices.
    writeCube(data, DEMO_DIMS, [14, 18, 10], [19, 23, 15], 1);
    return data;
}

function buildNiftiBuffer(
    dims: readonly [number, number, number],
    data: TypedVolumeData,
    affine: Mat4,
): ArrayBuffer {
    const datatypeCode = data instanceof Float32Array ? 16 : 2;
    const bitpix = data.BYTES_PER_ELEMENT * 8;
    const voxelBytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    const buffer = new ArrayBuffer(352 + voxelBytes.byteLength);
    const view = new DataView(buffer);
    const headerBytes = new Uint8Array(buffer);

    view.setInt32(0, 348, true);

    const dimOffset = 40;
    view.setInt16(dimOffset, 3, true);
    view.setInt16(dimOffset + 2, dims[0], true);
    view.setInt16(dimOffset + 4, dims[1], true);
    view.setInt16(dimOffset + 6, dims[2], true);
    view.setInt16(dimOffset + 8, 1, true);

    view.setInt16(70, datatypeCode, true);
    view.setInt16(72, bitpix, true);

    view.setFloat32(76, 1, true);
    view.setFloat32(80, 1, true);
    view.setFloat32(84, 1, true);
    view.setFloat32(88, 1, true);

    view.setFloat32(108, 352, true);
    view.setInt16(254, 1, true);

    let offset = 280;
    for (let row = 0; row < 3; row++) {
        for (let col = 0; col < 4; col++) {
            view.setFloat32(offset, affine[row][col], true);
            offset += 4;
        }
    }

    headerBytes[344] = 'n'.charCodeAt(0);
    headerBytes[345] = '+'.charCodeAt(0);
    headerBytes[346] = '1'.charCodeAt(0);
    headerBytes[347] = 0;
    headerBytes.set(voxelBytes, 352);

    return buffer;
}

function objectUrlForBuffer(buffer: ArrayBuffer): string {
    return URL.createObjectURL(new Blob([buffer], { type: 'application/octet-stream' }));
}

export function createHeaderMismatchDebugFixture(): HeaderMismatchDebugFixture {
    const baseAffine: Mat4 = [
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
    ];
    const segmentationAffine: Mat4 = [
        [1, 0, 0, SEGMENTATION_HEADER_OFFSET_MM[0]],
        [0, 1, 0, SEGMENTATION_HEADER_OFFSET_MM[1]],
        [0, 0, 1, SEGMENTATION_HEADER_OFFSET_MM[2]],
        [0, 0, 0, 1],
    ];

    const intensityUrl = objectUrlForBuffer(
        buildNiftiBuffer(DEMO_DIMS, createIntensityVolumeData(), baseAffine),
    );
    const segmentationUrl = objectUrlForBuffer(
        buildNiftiBuffer(DEMO_DIMS, createSegmentationVolumeData(), segmentationAffine),
    );

    return {
        targetCoordinate: [...TARGET_COORDINATE],
        segmentationHeaderOffsetMm: [...SEGMENTATION_HEADER_OFFSET_MM],
        volumes: [
            {
                id: 'debug-intensity',
                name: 'Synthetic Intensity',
                filename: 'synthetic-intensity.nii',
                url: intensityUrl,
                opacity: 1,
                colormap: 'gray',
                visible: true,
                type: 'intensity',
                brightness: 0,
                contrast: 1,
            },
            {
                id: 'debug-segmentation',
                name: 'Header-Shifted Segmentation',
                filename: 'synthetic-segmentation-shifted-header.nii',
                url: segmentationUrl,
                opacity: 0.85,
                colormap: 'binary',
                visible: true,
                type: 'segmentation',
                lut: 'binary',
            },
        ],
        cleanup: () => {
            URL.revokeObjectURL(intensityUrl);
            URL.revokeObjectURL(segmentationUrl);
        },
    };
}
