import {
  isMghFilename,
  prepareNiivueVolumeInline,
  type PreparedNiivueVolume,
} from './niivueMghCore';

interface WorkerResponse {
  id: number;
  buffer?: ArrayBuffer;
  filename?: string;
  error?: string;
}

interface PendingWorkerRequest {
  resolve: (prepared: PreparedNiivueVolume) => void;
  reject: (error: Error) => void;
}

let nextRequestId = 1;
let preparationWorker: Worker | null = null;
const pendingRequests = new Map<number, PendingWorkerRequest>();

function getPreparationWorker(): Worker | null {
  if (typeof Worker === 'undefined') return null;
  if (preparationWorker) return preparationWorker;

  preparationWorker = new Worker(
    new URL('../workers/niivueMgh.worker.ts', import.meta.url),
    { type: 'module' },
  );
  preparationWorker.onmessage = (event: MessageEvent<WorkerResponse>) => {
    const pending = pendingRequests.get(event.data.id);
    if (!pending) return;
    pendingRequests.delete(event.data.id);
    if (event.data.error) {
      pending.reject(new Error(event.data.error));
      return;
    }
    if (!event.data.buffer || !event.data.filename) {
      pending.reject(new Error('MGZ preparation worker returned an incomplete result.'));
      return;
    }
    pending.resolve({ buffer: event.data.buffer, filename: event.data.filename });
  };
  preparationWorker.onerror = (event) => {
    const error = new Error(event.message || 'MGZ preparation worker failed.');
    for (const pending of pendingRequests.values()) pending.reject(error);
    pendingRequests.clear();
    preparationWorker?.terminate();
    preparationWorker = null;
  };
  return preparationWorker;
}

export async function prepareNiivueVolume(
  buffer: ArrayBuffer,
  filename: string,
): Promise<PreparedNiivueVolume> {
  if (!isMghFilename(filename)) return { buffer, filename };
  const worker = getPreparationWorker();
  if (!worker) return prepareNiivueVolumeInline(buffer, filename);

  const id = nextRequestId;
  nextRequestId += 1;
  // The fetched-byte cache owns `buffer`; transfer a copy so repeated shows can
  // reuse the cached source without finding it detached.
  const workerBuffer = buffer.slice(0);
  return new Promise<PreparedNiivueVolume>((resolve, reject) => {
    pendingRequests.set(id, { resolve, reject });
    worker.postMessage({ id, buffer: workerBuffer, filename }, [workerBuffer]);
  });
}
