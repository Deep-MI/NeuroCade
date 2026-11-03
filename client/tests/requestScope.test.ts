import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequestScope } from '../src/utils/requestScope.js';

void test('delayed A responses stay invalid after A → B → A', async () => {
  const scope = createRequestScope('A');
  const publishA = scope.capture();
  scope.change('B');
  const publishB = scope.capture();
  scope.change('A');
  const published: string[] = [];
  await Promise.resolve();
  if (publishA()) published.push('old A');
  if (publishB()) published.push('B');
  if (scope.capture()()) published.push('new A');
  assert.deepEqual(published, ['new A']);
});

void test('unmount invalidates pending work even after effect reactivation', () => {
  const scope = createRequestScope('A');
  const old = scope.capture();
  scope.dispose();
  scope.activate();
  assert.equal(old(), false);
  assert.equal(scope.capture()(), true);
});
