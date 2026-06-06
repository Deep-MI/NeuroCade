export interface LutEntry {
    name: string;
    rgb: [number, number, number];
    alpha: number;
}

export type LutMap = Map<number, LutEntry>;

/**
 * Binary lookup table for mask/brainmask volumes.
 * Index 0 = background (transparent / skipped), index 1 = brain structure.
 */
export function createBinaryLut(): LutMap {
    const lut: LutMap = new Map();
    lut.set(0, { name: 'Background', rgb: [0, 0, 0], alpha: 0 });
    lut.set(1, { name: 'Structure (binary)',  rgb: [255, 120, 0], alpha: 255 });
    return lut;
}

export function parseLUT(text: string): LutMap {
    const lut: LutMap = new Map();
    const lines = text.split('\n');

    for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;

        // Format is usually: Index Name R G B A
        const parts = trimmed.split(/\s+/);
        if (parts.length < 5) continue;

        const index = parseInt(parts[0], 10);
        const name = parts[1];
        const r = parseInt(parts[2], 10);
        const g = parseInt(parts[3], 10);
        const b = parseInt(parts[4], 10);
        const transparency = parts.length >= 6 ? parseInt(parts[5], 10) : 0;

        if (!isNaN(index)) {
            lut.set(index, {
                name,
                rgb: [r, g, b],
                // FreeSurfer LUT stores transparency in the A column:
                // 0 = fully opaque, 255 = fully transparent.
                alpha: Number.isNaN(transparency) ? 255 : Math.max(0, Math.min(255, 255 - transparency)),
            });
        }
    }

    return lut;
}

/**
 * Look up a label in the given LUT. If the label is missing, return a
 * deterministic, visually distinct fallback entry so unknown segmentation
 * IDs are still rendered and identifiable.
 */
export function lookupLut(lut: LutMap | null, labelIndex: number): LutEntry {
    if (lut?.has(labelIndex)) {
        return lut.get(labelIndex)!;
    }
    // Generate a distinct colour from the label index using the golden-angle
    // hue spread (avoids the washed-out look of simple modular arithmetic).
    const hue = (labelIndex * 137.508) % 360;          // golden angle ≈ 137.5°
    const [r, g, b] = hslToRgb(hue / 360, 0.75, 0.55); // saturated & mid-bright
    return { name: `Unknown label ${labelIndex}`, rgb: [r, g, b], alpha: 255 };
}

/** Convert HSL (h,s,l in 0-1) to RGB (each 0-255). */
function hslToRgb(h: number, s: number, l: number): [number, number, number] {
    let r: number, g: number, b: number;
    if (s === 0) {
        r = g = b = l;
    } else {
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        r = hue2rgb(p, q, h + 1 / 3);
        g = hue2rgb(p, q, h);
        b = hue2rgb(p, q, h - 1 / 3);
    }
    return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}

function hue2rgb(p: number, q: number, t: number): number {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
}
