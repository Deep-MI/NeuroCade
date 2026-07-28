import type { ChatMessage } from '../types';

export function messagesVisibleToAssistant(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter(
    (message) => message.role !== 'system'
      && message.role !== 'info'
      && message.role !== 'tool-calls',
  );
}
