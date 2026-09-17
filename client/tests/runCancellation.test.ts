import assert from 'node:assert/strict';
import { test } from 'node:test';
import { describeCancellation, type RunCancellation } from '../src/utils/runCancellation.js';

void test('accepting a cancellation is not proof of stopping', () => {
  const response: RunCancellation = { run_id: 'run', status: 'running', cancellation: 'requested' };
  assert.equal(describeCancellation(response).stopped, false);
  assert.match(describeCancellation(response).message, /Waiting/);
  const stopped = describeCancellation({ ...response, status: 'canceled', cancellation: 'stopped' });
  assert.equal(stopped.stopped, true);
  assert.match(stopped.message, /canceled by user/);
  const finished = describeCancellation({ ...response, status: 'completed', cancellation: null });
  assert.equal(finished.stopped, false);
  assert.match(finished.message, /already finished/);
});
