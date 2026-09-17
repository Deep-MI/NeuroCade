import assert from 'node:assert/strict';
import { test } from 'node:test';
import { analysisFailureMessage, decodeApiError, pacsErrorMessage } from '../src/utils/errorMessages.js';

void test('analysis guidance is selected by code, not English wording', () => {
  assert.match(analysisFailureMessage('Different wording', 'pacs_sequence_incompatible'), /Choose a native, noncontrast/);
  assert.doesNotMatch(analysisFailureMessage('PACS input sequence mentioned in another error'), /Choose a native/);
  assert.match(analysisFailureMessage('Changed file', 'pacs_provenance_invalid'), /Re-import/);
  assert.match(analysisFailureMessage(), /No further details/);
});

void test('PACS errors have actionable messages and a safe fallback', () => {
  assert.match(pacsErrorMessage('connection_failed'), /Check the connection/);
  assert.match(pacsErrorMessage('cleanup_failed'), /before retrying/);
  assert.doesNotMatch(pacsErrorMessage('arbitrary_internal_secret'), /arbitrary_internal_secret/);
});

void test('structured API errors preserve codes; legacy and validation responses remain readable', () => {
  assert.equal(decodeApiError({ detail: { code: 'pacs_sequence_incompatible', message: 'Unsupported input' } }, 'Fallback').code, 'pacs_sequence_incompatible');
  assert.equal(decodeApiError({ detail: 'Legacy message' }, 'Fallback').message, 'Legacy message');
  assert.equal(decodeApiError({ detail: [{ msg: 'Invalid input' }] }, 'Fallback').message, 'Fallback');
  assert.equal(decodeApiError(null, 'Fallback').message, 'Fallback');
});
