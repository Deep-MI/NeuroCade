export type Mat3 = [number, number, number, number, number, number, number, number, number];
export type Vec3 = [number, number, number];

export interface SurfaceViewState {
    rotation: Mat3;
    zoom: number;
    panX: number;
    panY: number;
}

function normalizeVec3(value: Vec3): Vec3 {
    const length = Math.hypot(value[0], value[1], value[2]) || 1;
    return [value[0] / length, value[1] / length, value[2] / length];
}

export function dotVec3(a: Vec3, b: Vec3): number {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

export function crossVec3(a: Vec3, b: Vec3): Vec3 {
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ];
}

export function rotationMatrixFromAxisAngle(axis: Vec3, angle: number): Mat3 {
    const [x, y, z] = normalizeVec3(axis);
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    const t = 1 - c;

    // Column-major order for WebGL uniformMatrix3fv.
    return [
        t * x * x + c, t * x * y + s * z, t * x * z - s * y,
        t * x * y - s * z, t * y * y + c, t * y * z + s * x,
        t * x * z + s * y, t * y * z - s * x, t * z * z + c,
    ];
}

export function multiplyMat3(a: Mat3, b: Mat3): Mat3 {
    const out = new Array(9).fill(0) as Mat3;
    for (let column = 0; column < 3; column += 1) {
        for (let row = 0; row < 3; row += 1) {
            out[column * 3 + row] =
                a[row] * b[column * 3]
                + a[3 + row] * b[column * 3 + 1]
                + a[6 + row] * b[column * 3 + 2];
        }
    }
    return out;
}

export function projectToTrackball(clientX: number, clientY: number, rect: DOMRect): Vec3 {
    const size = Math.max(rect.width, rect.height, 1);
    const x = (2 * (clientX - rect.left) - rect.width) / size;
    const y = (rect.height - 2 * (clientY - rect.top)) / size;
    const lengthSquared = x * x + y * y;
    if (lengthSquared <= 1) {
        return [x, y, Math.sqrt(1 - lengthSquared)];
    }
    const length = Math.sqrt(lengthSquared);
    return [x / length, y / length, 0];
}

export const DEFAULT_SURFACE_VIEW: SurfaceViewState = {
    rotation: multiplyMat3(
        rotationMatrixFromAxisAngle([0, 0, 1], -Math.PI / 2),
        rotationMatrixFromAxisAngle([0, 1, 0], -Math.PI / 2),
    ),
    zoom: 1,
    panX: 0,
    panY: 0,
};
