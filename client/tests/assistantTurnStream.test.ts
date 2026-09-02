import assert from 'node:assert/strict';
import test from 'node:test';

import { consumeAssistantTurnStream } from '../src/components/assistantTurnStream.js';
import type { AssistantActivity, ReasoningEntry, ToolCallEntry } from '../src/types.js';

function streamChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

void test('decodes split SSE events and accumulates one streamed round', async () => {
  const textUpdates: [string, boolean][] = [];
  let doneContent = '';
  const result = await consumeAssistantTurnStream(
    streamChunks([
      'event: text_delta\ndata: {"content":"Hel',
      'lo","round":1}\n\nevent: text_delta\ndata: {"content":" world","round":1}\n\n',
      'event: done\ndata: {"message":{"content":"Hello world"}}\n\n',
    ]),
    {
      onText: (content, startsNewMessage) => textUpdates.push([content, startsNewMessage]),
      onActivity: () => undefined,
      onAssistantMessage: () => undefined,
      onToolUpdates: () => undefined,
      onDone: (response) => { doneContent = response.message.content; },
    },
  );

  assert.deepEqual(textUpdates, [['Hello', true], ['Hello world', false]]);
  assert.equal(doneContent, 'Hello world');
  assert.equal(result.receivedFinalEvent, true);
  assert.equal(result.streamedText, 'Hello world');
  assert.deepEqual(result.eventCounts, { text_delta: 2, done: 1 });
});

void test('handles CRLF, new rounds, activity, reasoning, and tool calls', async () => {
  const textUpdates: [string, boolean][] = [];
  const activities: AssistantActivity[] = [];
  const toolUpdates: [ToolCallEntry[], ReasoningEntry[]][] = [];
  const assistantMessages: string[] = [];
  const result = await consumeAssistantTurnStream(
    streamChunks([
      'event: text_delta\r\ndata: {"content":"first","round":1}\r\n\r\n',
      'event: text_delta\r\ndata: {"content":"second","round":2}\r\n\r\n',
      'event: activity\ndata: {"kind":"tool","label":"case_file_tree","blocking":true}\n\n',
      'event: reasoning\ndata: {"summary":"Inspect files","round":2}\n\n',
      'event: tool_call\ndata: {"name":"case_file_tree","arguments":{},"result":"ok"}\n\n',
      'event: assistant_message\ndata: {"content":"Interim result","round":2}\n\n',
    ]),
    {
      onText: (content, startsNewMessage) => textUpdates.push([content, startsNewMessage]),
      onActivity: (activity) => activities.push(activity),
      onAssistantMessage: (content) => assistantMessages.push(content),
      onToolUpdates: (tools, reasoning) => toolUpdates.push([tools, reasoning]),
      onDone: () => undefined,
    },
  );

  assert.deepEqual(textUpdates, [['first', true], ['second', true]]);
  assert.equal(activities[0]?.label, 'case_file_tree');
  assert.deepEqual(toolUpdates.map(([tools, reasoning]) => [tools.length, reasoning.length]), [[0, 1], [1, 1]]);
  assert.deepEqual(assistantMessages, ['Interim result']);
  assert.equal(result.receivedFinalEvent, false);
  assert.equal(result.streamedText, 'second');
});

void test('throws the server error message from a terminal error event', async () => {
  await assert.rejects(
    consumeAssistantTurnStream(
      streamChunks(['event: error\ndata: {"error":{"message":"Model failed"}}\n\n']),
      {
        onText: () => undefined,
        onActivity: () => undefined,
        onAssistantMessage: () => undefined,
        onToolUpdates: () => undefined,
        onDone: () => undefined,
      },
    ),
    /Model failed/,
  );
});
