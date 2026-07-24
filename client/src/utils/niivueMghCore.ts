export interface PreparedNiivueVolume {
  buffer: ArrayBuffer;
  filename: string;
}

const MGH_HEADER_SIZE = 284;
const NIFTI_HEADER_SIZE = 352;

export function isMghFilename(filename: string): boolean {
  const lowerFilename = filename.toLowerCase();
  return lowerFilename.endsWith('.mgz') || lowerFilename.endsWith('.mgh');
}

function swapPayloadBytes(bytes: Uint8Array, bytesPerVoxel: number): void {
  if (bytesPerVoxel === 1) return;
  for (let offset = 0; offset + bytesPerVoxel <= bytes.length; offset += bytesPerVoxel) {
    for (let left = 0, right = bytesPerVoxel - 1; left < right; left += 1, right -= 1) {
      const value = bytes[offset + left];
      bytes[offset + left] = bytes[offset + right];
      bytes[offset + right] = value;
    }
  }
}

async function decompressGzip(buffer: ArrayBuffer): Promise<ArrayBuffer> {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('This browser cannot decompress MGZ volumes.');
  }
  const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).arrayBuffer();
}

function mghDatatype(type: number): { bitpix: number; bytesPerVoxel: number; niftiDatatype: number } {
  if (type === 0) return { bitpix: 8, bytesPerVoxel: 1, niftiDatatype: 2 };
  if (type === 4) return { bitpix: 16, bytesPerVoxel: 2, niftiDatatype: 4 };
  if (type === 1) return { bitpix: 32, bytesPerVoxel: 4, niftiDatatype: 8 };
  if (type === 3) return { bitpix: 32, bytesPerVoxel: 4, niftiDatatype: 16 };
  throw new Error(`Unsupported MGH datatype ${type}.`);
}

function convertMghToNifti(buffer: ArrayBuffer): ArrayBuffer {
  if (buffer.byteLength < MGH_HEADER_SIZE) {
    throw new Error('MGH volume is smaller than its header.');
  }
  const mgh = new DataView(buffer);
  const width = mgh.getInt32(4, false);
  const height = mgh.getInt32(8, false);
  const depth = mgh.getInt32(12, false);
  const frames = Math.max(1, mgh.getInt32(16, false));
  if (width <= 0 || height <= 0 || depth <= 0) {
    throw new Error(`Invalid MGH dimensions ${width}x${height}x${depth}.`);
  }

  const { bitpix, bytesPerVoxel, niftiDatatype } = mghDatatype(mgh.getInt32(20, false));
  const voxelBytes = width * height * depth * frames * bytesPerVoxel;
  if (MGH_HEADER_SIZE + voxelBytes > buffer.byteLength) {
    throw new Error('MGH voxel payload is truncated.');
  }

  const spacing = [
    Math.abs(mgh.getFloat32(30, false)),
    Math.abs(mgh.getFloat32(34, false)),
    Math.abs(mgh.getFloat32(38, false)),
  ];
  const affine = [
    [
      mgh.getFloat32(42, false) * spacing[0],
      mgh.getFloat32(54, false) * spacing[1],
      mgh.getFloat32(66, false) * spacing[2],
      0,
    ],
    [
      mgh.getFloat32(46, false) * spacing[0],
      mgh.getFloat32(58, false) * spacing[1],
      mgh.getFloat32(70, false) * spacing[2],
      0,
    ],
    [
      mgh.getFloat32(50, false) * spacing[0],
      mgh.getFloat32(62, false) * spacing[1],
      mgh.getFloat32(74, false) * spacing[2],
      0,
    ],
  ];
  const center = [width / 2, height / 2, depth / 2];
  const cras = [
    mgh.getFloat32(78, false),
    mgh.getFloat32(82, false),
    mgh.getFloat32(86, false),
  ];
  for (let row = 0; row < 3; row += 1) {
    affine[row][3] = cras[row]
      - affine[row][0] * center[0]
      - affine[row][1] * center[1]
      - affine[row][2] * center[2];
  }

  const nifti = new ArrayBuffer(NIFTI_HEADER_SIZE + voxelBytes);
  const header = new DataView(nifti);
  header.setInt32(0, 348, true);
  header.setInt16(40, frames > 1 ? 4 : 3, true);
  for (const [index, dimension] of [width, height, depth, frames].entries()) {
    header.setInt16(42 + index * 2, dimension, true);
  }
  header.setInt16(70, niftiDatatype, true);
  header.setInt16(72, bitpix, true);
  header.setFloat32(76, 1, true);
  for (let axis = 0; axis < 3; axis += 1) {
    header.setFloat32(80 + axis * 4, spacing[axis], true);
  }
  header.setFloat32(108, NIFTI_HEADER_SIZE, true);
  header.setFloat32(112, 1, true);
  header.setUint8(123, 2);
  header.setInt16(254, 1, true);
  for (let row = 0; row < 3; row += 1) {
    for (let column = 0; column < 4; column += 1) {
      header.setFloat32(280 + (row * 4 + column) * 4, affine[row][column], true);
    }
  }
  new Uint8Array(nifti, 344, 4).set([110, 43, 49, 0]);

  const payload = new Uint8Array(buffer.slice(MGH_HEADER_SIZE, MGH_HEADER_SIZE + voxelBytes));
  swapPayloadBytes(payload, bytesPerVoxel);
  new Uint8Array(nifti, NIFTI_HEADER_SIZE, voxelBytes).set(payload);
  return nifti;
}

export async function prepareNiivueVolumeInline(
  buffer: ArrayBuffer,
  filename: string,
): Promise<PreparedNiivueVolume> {
  if (!isMghFilename(filename)) {
    return { buffer, filename };
  }
  const mghBuffer = filename.toLowerCase().endsWith('.mgz') ? await decompressGzip(buffer) : buffer;
  return {
    buffer: convertMghToNifti(mghBuffer),
    filename: filename.replace(/\.mg[hz]$/i, '.nii'),
  };
}
