import { prepareNiivueVolumeInline } from '../utils/niivueMghCore';

interface PrepareVolumeRequest {
  id: number;
  buffer: ArrayBuffer;
  filename: string;
}

interface PrepareVolumeResponse {
  id: number;
  buffer?: ArrayBuffer;
  filename?: string;
  error?: string;
}

interface WorkerMessageScope {
  onmessage: ((event: MessageEvent<PrepareVolumeRequest>) => void) | null;
  postMessage(message: PrepareVolumeResponse, transfer: Transferable[]): void;
}

const workerScope = globalThis as unknown as WorkerMessageScope;

workerScope.onmessage = (event) => {
  const { id, buffer, filename } = event.data;
  void prepareNiivueVolumeInline(buffer, filename)
    .then((prepared) => {
      workerScope.postMessage(
        { id, buffer: prepared.buffer, filename: prepared.filename },
        [prepared.buffer],
      );
    })
    .catch((error: unknown) => {
      workerScope.postMessage({
        id,
        error: error instanceof Error ? error.message : String(error),
      }, []);
    });
};
