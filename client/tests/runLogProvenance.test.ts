import assert from 'node:assert/strict';
import test from 'node:test';
import { describeRunLogSource } from '../src/utils/runLogProvenance.js';

void test('stored log captions identify the workflow, terminal status, and time', () => {
  const createdAt = '2026-09-22T10:00:00Z';
  assert.equal(describeRunLogSource({ id: 'run', tool_id: 'fastsurfer', status: 'completed', created_at: createdAt }),
    `fastsurfer · completed · ${new Date(createdAt).toLocaleString()}`);
  assert.equal(describeRunLogSource(null), null);
  assert.equal(describeRunLogSource({ id: 'run', tool_id: null, status: 'running', created_at: 'invalid' }), 'workflow · running');
});
