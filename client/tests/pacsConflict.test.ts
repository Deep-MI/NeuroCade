import assert from 'node:assert/strict';
import { test } from 'node:test';
import { duplicateStudyCases } from '../src/utils/pacsConflict.js';

void test('only duplicate-study conflicts enter the confirmation flow', () => {
  assert.deepEqual(duplicateStudyCases({ detail: { code: 'duplicate_study', case_ids: ['case'] } }), ['case']);
  assert.equal(duplicateStudyCases({ detail: 'Processing may still own this workspace' }), null);
  assert.equal(duplicateStudyCases({ detail: { code: 'output_busy', case_ids: ['case'] } }), null);
  assert.equal(duplicateStudyCases({ detail: { code: 'duplicate_study', case_ids: [42] } }), null);
});
