import type { VolumeData } from './VolumeLoader';

export type VolumeLoadPriority = 'foreground' | 'segmentation' | 'background';

interface VolumeParseInput {
    buffer: ArrayBuffer;
    detectLut: boolean;
    priority: VolumeLoadPriority;
}

interface VolumeParseResult {
    volumeData: VolumeData | null;
    detectedLut: 'binary' | 'freesurfer' | undefined;
}

interface VolumeParseRequest {
    id: number;
    buffer: ArrayBuffer;
    detectLut: boolean;
}

type VolumeParseResponse =
    | {
        id: number;
        ok: true;
        volumeData: VolumeData | null;
        detectedLut: 'binary' | 'freesurfer' | undefined;
    }
    | {
        id: number;
        ok: false;
        error: string;
    };

interface VolumeParseJob {
    promise: Promise<VolumeParseResult>;
    cancel: () => void;
}

interface QueueTask extends VolumeParseInput {
    id: number;
    priorityRank: number;
    sequence: number;
    running: boolean;
    settled: boolean;
    workerEntry: WorkerEntry | null;
    resolve: (result: VolumeParseResult) => void;
    reject: (error: Error) => void;
}

interface WorkerEntry {
    worker: Worker;
    current: QueueTask | null;
}

function priorityRank(priority: VolumeLoadPriority): number {
    if (priority === 'foreground') return 0;
    if (priority === 'segmentation') return 1;
    return 2;
}

function createAbortError(): Error {
    const error = new Error('Volume parse canceled');
    error.name = 'AbortError';
    return error;
}

function createVolumeWorker(): Worker {
    return new Worker(new URL('../workers/volumeLoader.worker.ts', import.meta.url), { type: 'module' });
}

class VolumeWorkerPool {
    private workers: WorkerEntry[];
    private queue: QueueTask[] = [];
    private nextId = 1;
    private sequence = 1;

    constructor(size: number) {
        this.workers = Array.from({ length: size }, () => this.createWorkerEntry());
    }

    parse(input: VolumeParseInput): VolumeParseJob {
        let task: QueueTask;
        const promise = new Promise<VolumeParseResult>((resolve, reject) => {
            task = {
                ...input,
                id: this.nextId++,
                priorityRank: priorityRank(input.priority),
                sequence: this.sequence++,
                running: false,
                settled: false,
                workerEntry: null,
                resolve,
                reject,
            };
            this.queue.push(task);
            this.dispatch();
        });

        return {
            promise,
            cancel: () => this.cancelTask(task!),
        };
    }

    private createWorkerEntry(): WorkerEntry {
        const entry: WorkerEntry = {
            worker: createVolumeWorker(),
            current: null,
        };
        this.attachWorkerHandlers(entry);
        return entry;
    }

    private attachWorkerHandlers(entry: WorkerEntry): void {
        entry.worker.onmessage = (event: MessageEvent<VolumeParseResponse>) => {
            const task = entry.current;
            if (task?.id !== event.data.id || task.settled) return;

            task.settled = true;
            entry.current = null;
            task.workerEntry = null;

            if (event.data.ok) {
                task.resolve({
                    volumeData: event.data.volumeData,
                    detectedLut: event.data.detectedLut,
                });
            } else {
                task.reject(new Error(event.data.error));
            }
            this.dispatch();
        };

        entry.worker.onerror = (event) => {
            const task = entry.current;
            if (task && !task.settled) {
                task.settled = true;
                task.reject(new Error(event.message || 'Volume worker failed'));
            }
            this.replaceWorker(entry);
            this.dispatch();
        };
    }

    private replaceWorker(entry: WorkerEntry): void {
        entry.worker.terminate();
        entry.worker = createVolumeWorker();
        entry.current = null;
        this.attachWorkerHandlers(entry);
    }

    private cancelTask(task: QueueTask): void {
        if (task.settled) return;
        task.settled = true;

        const queuedIndex = this.queue.indexOf(task);
        if (queuedIndex >= 0) {
            this.queue.splice(queuedIndex, 1);
            task.reject(createAbortError());
            return;
        }

        if (task.running && task.workerEntry) {
            const entry = task.workerEntry;
            this.replaceWorker(entry);
            task.workerEntry = null;
            task.reject(createAbortError());
            this.dispatch();
        }
    }

    private dispatch(): void {
        if (this.queue.length === 0) return;
        this.queue.sort((a, b) => a.priorityRank - b.priorityRank || a.sequence - b.sequence);

        for (const entry of this.workers) {
            if (entry.current) continue;
            const task = this.queue.shift();
            if (!task) return;
            if (task.settled) continue;

            task.running = true;
            task.workerEntry = entry;
            entry.current = task;

            const message: VolumeParseRequest = {
                id: task.id,
                buffer: task.buffer,
                detectLut: task.detectLut,
            };
            entry.worker.postMessage(message, [task.buffer]);
        }
    }
}

const workerCount = Math.max(1, Math.min(2, globalThis.navigator?.hardwareConcurrency ?? 2));
let volumeWorkerPool: VolumeWorkerPool | null = null;

export function getVolumeWorkerPool(): VolumeWorkerPool {
    volumeWorkerPool ??= new VolumeWorkerPool(workerCount);
    return volumeWorkerPool;
}
