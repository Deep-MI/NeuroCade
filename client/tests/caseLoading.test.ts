import assert from 'node:assert/strict';
import test from 'node:test';

import { isCaseTransitionPending } from '../src/utils/caseLoading.js';

void test('a routed case is pending until it becomes active', () => {
  assert.equal(isCaseTransitionPending('workspace__sample-case', null, null), true);
  assert.equal(isCaseTransitionPending('workspace__sample-case', 'workspace__other-case', null), true);
  assert.equal(isCaseTransitionPending('workspace__sample-case', 'workspace__sample-case', null), false);
});

void test('an explicit load remains pending for the active route', () => {
  assert.equal(
    isCaseTransitionPending('workspace__sample-case', 'workspace__sample-case', 'workspace__sample-case'),
    true,
  );
});

void test('the case list without an active load is not pending', () => {
  assert.equal(isCaseTransitionPending(null, null, null), false);
});
