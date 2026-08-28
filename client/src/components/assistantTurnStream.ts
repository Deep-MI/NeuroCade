import type { AssistantActivity, AssistantApprovalRequest, ReasoningEntry, ToolCallEntry } from '../types';

interface ApiResponse {
  message: { content: string };
  turn_id?: string;
  tool_calls_log?: ToolCallEntry[];
  approval_request?: AssistantApprovalRequest;
}

interface AssistantMessageEvent {
  content: string;
  round?: number;
}

interface SseEvent {
  eventType: string;
  data: string;
}

export interface AssistantTurnStreamCallbacks {
  onText: (content: string, startsNewMessage: boolean) => void;
  onActivity: (activity: AssistantActivity) => void;
  onAssistantMessage: (content: string) => void;
  onToolUpdates: (toolCalls: ToolCallEntry[], reasoningEntries: ReasoningEntry[]) => void;
  onDone: (response: ApiResponse, streamedText: string) => void;
}

export interface AssistantTurnStreamResult {
  receivedFinalEvent: boolean;
  streamedText: string;
  eventCounts: Record<string, number>;
}

interface StreamState extends AssistantTurnStreamResult {
  streamedRound?: number;
  toolCalls: ToolCallEntry[];
  reasoningEntries: ReasoningEntry[];
}

function parseSsePart(part: string): SseEvent | null {
  if (!part.trim()) return null;
  let eventType = '';
  let data = '';
  for (const line of part.split('\n')) {
    if (line.startsWith('event: ')) eventType = line.slice(7);
    else if (line.startsWith('data: ')) data = line.slice(6);
  }
  return eventType && data ? { eventType, data } : null;
}

function processSsePart(
  part: string,
  state: StreamState,
  callbacks: AssistantTurnStreamCallbacks,
): void {
  const event = parseSsePart(part);
  if (!event) return;
  const { eventType, data } = event;
  state.eventCounts[eventType] = (state.eventCounts[eventType] ?? 0) + 1;

  if (eventType === 'text_delta') {
    const delta = JSON.parse(data) as AssistantMessageEvent;
    const startsNewMessage = state.streamedRound !== delta.round;
    state.streamedText = startsNewMessage ? delta.content : state.streamedText + delta.content;
    state.streamedRound = delta.round;
    callbacks.onText(state.streamedText, startsNewMessage);
  } else if (eventType === 'activity') {
    callbacks.onActivity(JSON.parse(data) as AssistantActivity);
  } else if (eventType === 'assistant_message') {
    const message = JSON.parse(data) as AssistantMessageEvent;
    if (message.content.trim()) callbacks.onAssistantMessage(message.content);
  } else if (eventType === 'reasoning') {
    state.reasoningEntries.push(JSON.parse(data) as ReasoningEntry);
    callbacks.onToolUpdates([...state.toolCalls], [...state.reasoningEntries]);
  } else if (eventType === 'tool_call') {
    state.toolCalls.push(JSON.parse(data) as ToolCallEntry);
    callbacks.onToolUpdates([...state.toolCalls], [...state.reasoningEntries]);
  } else if (eventType === 'done') {
    state.receivedFinalEvent = true;
    callbacks.onDone(JSON.parse(data) as ApiResponse, state.streamedText);
  } else if (eventType === 'error') {
    state.receivedFinalEvent = true;
    const payload = JSON.parse(data) as { error?: { message?: string } };
    throw new Error(payload.error?.message ?? 'API request failed');
  }
}

export async function consumeAssistantTurnStream(
  body: ReadableStream<Uint8Array>,
  callbacks: AssistantTurnStreamCallbacks,
): Promise<AssistantTurnStreamResult> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const state: StreamState = {
    receivedFinalEvent: false,
    streamedText: '',
    eventCounts: {},
    toolCalls: [],
    reasoningEntries: [],
  };
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n');
    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';
    for (const part of parts) processSsePart(part, state, callbacks);
  }

  buffer = (buffer + decoder.decode()).replace(/\r\n/g, '\n');
  if (buffer.trim()) processSsePart(buffer, state, callbacks);
  return {
    receivedFinalEvent: state.receivedFinalEvent,
    streamedText: state.streamedText,
    eventCounts: state.eventCounts,
  };
}
