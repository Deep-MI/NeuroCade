import assert from 'node:assert/strict';
import { test } from 'node:test';
import { startCasePolling, type CasePollingOptions } from '../src/utils/casePolling.js';
import type { StatusResponse } from '../src/types';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function options(overrides: Partial<CasePollingOptions> = {}): CasePollingOptions {
  return { activeCaseId: 'A', runId: 'run-A', runStatus: 'running',
    isRunActive: status => status === 'running',
    fetchStatus: () => Promise.resolve({ status: 'running', runId: 'run-A' }),
    fetchLogs: () => Promise.resolve(), fetchOutputs: () => Promise.resolve(), onRunChange: () => undefined, ...overrides };
}

void test('a delayed status response cannot update the next case or trigger terminal work', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const pending = deferred<StatusResponse>();
  const updates: string[] = [];
  let requests = 0;
  const stop = startCasePolling(options({ fetchStatus: () => { requests++; return pending.promise; },
    onRunChange: () => updates.push('A'), onTerminalStatus: () => updates.push('terminal-A') }), new Set());
  t.mock.timers.tick(2000);
  t.mock.timers.tick(10000);
  assert.equal(requests, 1, 'slow requests do not overlap');
  stop();
  const stopB = startCasePolling(options({ activeCaseId: 'B', runId: 'run-B',
    fetchStatus: () => Promise.resolve({ status: 'running', runId: 'run-B' }), onRunChange: () => updates.push('B') }), new Set());
  pending.resolve({ status: 'failed', runId: 'run-A', errorMessage: 'old error' });
  await flush();
  t.mock.timers.tick(2000);
  await flush();
  assert.deepEqual(updates, ['B']);
  stopB();
});

for (const stream of ['fetchLogs', 'fetchOutputs'] as const) {
  void test(`cleanup invalidates delayed ${stream} before it can publish data`, async t => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const pending = deferred<void>();
    const updates: string[] = [];
    const stop = startCasePolling(options({ [stream]: async (_caseId: string, isCurrent: () => boolean) => {
      await pending.promise;
      if (isCurrent()) updates.push('stale-data');
    } }), new Set());
    t.mock.timers.tick(stream === 'fetchLogs' ? 3000 : 10000);
    stop();
    pending.resolve();
    await flush();
    assert.deepEqual(updates, []);
  });
}

void test('terminal transition refreshes data once and unchanged status still receives error details', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const events: string[] = [];
  const stop = startCasePolling(options({
    fetchStatus: () => Promise.resolve({ status: 'failed', runId: 'run-A', errorCode: 'pacs_sequence_incompatible' }),
    fetchLogs: () => { events.push('logs'); return Promise.resolve(); }, fetchOutputs: () => { events.push('outputs'); return Promise.resolve(); },
    onRunChange: (_status, _id, _message, code) => { events.push(code!); },
    onTerminalStatus: () => { events.push('terminal'); },
  }), new Set());
  t.mock.timers.tick(2000); await flush();
  assert.deepEqual(events, ['logs', 'outputs', 'terminal', 'pacs_sequence_incompatible']);
  stop();
  const same = startCasePolling(options({ runStatus: 'failed',
    fetchStatus: () => Promise.resolve({ status: 'failed', runId: 'run-A', errorMessage: 'late detail' }),
    onRunChange: (_status, _id, message) => { events.push(message!); },
  }), new Set());
  t.mock.timers.tick(3000); await flush();
  assert.equal(events.at(-1), 'late detail');
  same();
});
