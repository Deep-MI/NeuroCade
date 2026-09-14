import assert from 'node:assert/strict';
import { test } from 'node:test';
import { describeCancellation, type RunCancellation } from '../src/utils/runCancellation.js';

void test('accepting a cancellation is not proof of stopping', () => {
  const response: RunCancellation = { run_id: 'run', status: 'running', cancellation: 'requested', output_ownership: 'held' };
  assert.equal(describeCancellation(response).stopped, false);
  assert.match(describeCancellation(response).message, /Waiting/);
  const unresolved = describeCancellation({ ...response, cancellation: 'unresolved' });
  assert.equal(unresolved.stopped, false);
  assert.match(unresolved.message, /could not be confirmed/);
  const stopped = describeCancellation({ ...response, status: 'canceled', cancellation: 'stopped', output_ownership: 'released' });
  assert.equal(stopped.stopped, true);
  assert.match(stopped.message, /canceled by user/);
  const finished = describeCancellation({ ...response, status: 'completed', cancellation: null, output_ownership: 'released' });
  assert.equal(finished.stopped, false);
  assert.match(finished.message, /already finished/);
});
