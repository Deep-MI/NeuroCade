interface LutEntry {
    name: string;
    rgb: [number, number, number];
    alpha: number;
}

export type LutMap = Map<number, LutEntry>;

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
