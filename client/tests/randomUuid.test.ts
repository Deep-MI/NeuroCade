import assert from 'node:assert/strict';
import test from 'node:test';

import { createUuid } from '../src/utils/randomUuid.js';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

void test('uses crypto.randomUUID when it is available', () => {
  const expected = '11111111-1111-4111-8111-111111111111';
  assert.equal(createUuid({ randomUUID: () => expected }), expected);
});

void test('creates a version 4 UUID when randomUUID is unavailable', () => {
  const uuid = createUuid({
    getRandomValues(array) {
      array.fill(0xab);
      return array;
    },
  });

  assert.match(uuid, UUID_V4);
  assert.equal(uuid, 'abababab-abab-4bab-abab-abababababab');
});

void test('creates a UUID when the Web Crypto API is unavailable', () => {
  assert.match(createUuid(null), UUID_V4);
});
