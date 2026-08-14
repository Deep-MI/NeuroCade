import assert from 'node:assert/strict';
import test from 'node:test';

import { isCaseTransitionPending } from '../src/utils/caseLoading.js';

void test('a routed case is pending until it becomes active', () => {
  assert.equal(isCaseTransitionPending('sample-case-id', null, null), true);
  assert.equal(isCaseTransitionPending('sample-case-id', 'other-case-id', null), true);
  assert.equal(isCaseTransitionPending('sample-case-id', 'sample-case-id', null), false);
});

void test('an explicit load remains pending for the active route', () => {
  assert.equal(
    isCaseTransitionPending('sample-case-id', 'sample-case-id', 'sample-case-id'),
    true,
  );
});

void test('the case list without an active load is not pending', () => {
  assert.equal(isCaseTransitionPending(null, null, null), false);
});
