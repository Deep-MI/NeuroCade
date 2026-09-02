import assert from 'node:assert/strict';
import test from 'node:test';

import { terminalRunTransitionKey, workflowStatusNotificationId } from '../src/utils/runNotifications.js';

void test('identifies an active-to-terminal transition for the same run', () => {
  assert.equal(
    terminalRunTransitionKey(
      { runId: 'run-1', status: 'running' },
      { runId: 'run-1', status: 'canceled', workflowId: 'synthseg' },
    ),
    'run-1:canceled',
  );
});

void test('does not treat hydration or a different run as a terminal transition', () => {
  assert.equal(
    terminalRunTransitionKey(
      { status: 'idle' },
      { runId: 'run-1', status: 'canceled' },
    ),
    null,
  );
  assert.equal(
    terminalRunTransitionKey(
      { runId: 'run-1', status: 'running' },
      { runId: 'run-2', status: 'canceled' },
    ),
    null,
  );
  assert.equal(
    terminalRunTransitionKey(
      { runId: 'run-1', status: 'canceled' },
      { runId: 'run-1', status: 'canceled' },
    ),
    null,
  );
});

void test('creates stable workflow notification IDs', () => {
  assert.equal(workflowStatusNotificationId('run-1', 'canceled'), 'workflow:run-1:canceled');
  assert.notEqual(
    workflowStatusNotificationId('run-1', 'canceled'),
    workflowStatusNotificationId('run-2', 'canceled'),
  );
});
