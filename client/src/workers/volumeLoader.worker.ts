import pako from 'pako';
import { detectLut, parseVolume } from '../utils/VolumeLoader';

interface VolumeParseRequest {
    id: number;
    buffer: ArrayBuffer;
    detectLut: boolean;
}

type VolumeParseResponse =
    | {
        id: number;
        ok: true;
        volumeData: ReturnType<typeof parseVolume>;
        detectedLut: 'binary' | 'freesurfer' | undefined;
    }
    | {
        id: number;
        ok: false;
        error: string;
    };

const workerSelf = self as unknown as {
    addEventListener: (type: 'message', listener: (event: MessageEvent<VolumeParseRequest>) => void) => void;
    postMessage: (message: VolumeParseResponse, transfer?: Transferable[]) => void;
};

function inflateIfNeeded(buffer: ArrayBuffer): ArrayBuffer {
    const signature = new Uint8Array(buffer.slice(0, 2));
    if (signature[0] !== 0x1F || signature[1] !== 0x8B) {
        return buffer;
    }

    const decompressed = pako.inflate(new Uint8Array(buffer));
    return decompressed.buffer.slice(
        decompressed.byteOffset,
        decompressed.byteOffset + decompressed.byteLength,
    );
}

workerSelf.addEventListener('message', (event: MessageEvent<VolumeParseRequest>) => {
    const { id, buffer, detectLut: shouldDetectLut } = event.data;

    try {
        const volumeData = parseVolume(inflateIfNeeded(buffer));
        const detectedLut = volumeData && shouldDetectLut
            ? detectLut(volumeData.data)
            : undefined;

        const response: VolumeParseResponse = {
            id,
            ok: true,
            volumeData,
            detectedLut,
        };
        const transfers: Transferable[] = [];
        if (volumeData?.data.buffer instanceof ArrayBuffer) {
            transfers.push(volumeData.data.buffer);
        }
        workerSelf.postMessage(response, transfers);
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        workerSelf.postMessage({ id, ok: false, error: message } satisfies VolumeParseResponse);
    }
});
