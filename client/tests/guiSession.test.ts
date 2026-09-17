import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createGuiSessionId } from '../src/utils/guiSession.js';

void test('duplicated tabs have different agent identities even with copied sessionStorage', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window');
  const storage = { getItem: () => 'copied-session' };
  try {
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { sessionStorage: storage } });
    const original = createGuiSessionId();
    assert.equal(createGuiSessionId(), original, 'route changes retain the document identity');
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { sessionStorage: storage } });
    assert.notEqual(createGuiSessionId(), original, 'the copied tab must be independently selectable');
  } finally {
    if (previous) Object.defineProperty(globalThis, 'window', previous);
    else Reflect.deleteProperty(globalThis, 'window');
  }
});
