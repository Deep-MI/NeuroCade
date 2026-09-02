import assert from 'node:assert/strict';
import test from 'node:test';

import { appendUniqueChatMessages } from '../src/components/chatMessages.js';
import type { ChatMessage } from '../src/types.js';

void test('deduplicates reconstructed notifications by stable ID', () => {
  const existing: ChatMessage[] = [{
    notificationId: 'workflow:run-1:canceled',
    role: 'info',
    content: 'SynthSeg canceled.',
  }];
  const incoming: ChatMessage[] = [{
    notificationId: 'workflow:run-1:canceled',
    role: 'info',
    content: 'SynthSeg canceled.',
  }];

  assert.strictEqual(appendUniqueChatMessages(existing, incoming), existing);
});

void test('keeps terminal notifications for different runs', () => {
  const existing: ChatMessage[] = [{
    notificationId: 'workflow:run-1:canceled',
    role: 'info',
    content: 'SynthSeg canceled.',
  }];
  const incoming: ChatMessage[] = [{
    notificationId: 'workflow:run-2:canceled',
    role: 'info',
    content: 'SynthSeg canceled.',
  }];

  assert.equal(appendUniqueChatMessages(existing, incoming).length, 2);
});
