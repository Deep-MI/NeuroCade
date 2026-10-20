import assert from 'node:assert/strict';
import test from 'node:test';
import { startGuiStateSync } from '../src/utils/guiStateSync.js';
import type { GuiStateSyncResponse } from '../src/types';

void test('old case replies cannot execute or acknowledge commands in a new scope', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let resolve!: (response: GuiStateSyncResponse) => void;
  let requests = 0;
  const delivered: string[] = [];
  const stopA = startGuiStateSync({ payload: () => ({ case_id: 'A' }),
    sync: () => { requests++; return new Promise<GuiStateSyncResponse>(done => { resolve = done; }); },
    onResponse: () => delivered.push('A') });
  t.mock.timers.tick(10000);
  assert.equal(requests, 1, 'pending requests are serial');
  stopA();
  const stopB = startGuiStateSync({ payload: () => ({ case_id: 'B' }),
    sync: payload => { assert.deepEqual(payload.acknowledged_command_ids, []); return Promise.resolve({ commands: [] }); },
    onResponse: () => delivered.push('B') });
  resolve({ commands: [{ id: 'old', type: 'move_cursor', payload: { position: [1, 2, 3] }, created_at: '', expires_at: '' }] });
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(delivered, ['B']);
  stopB();
});
