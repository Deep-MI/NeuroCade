import type { ChatMessage } from '../types';

export function appendUniqueChatMessages(
  existing: ChatMessage[],
  incoming: ChatMessage[],
): ChatMessage[] {
  const existingNotificationIds = new Set(
    existing.flatMap((message) => message.notificationId ? [message.notificationId] : []),
  );
  const additions = incoming.filter((message) => {
    if (!message.notificationId) return !existing.includes(message);
    if (existingNotificationIds.has(message.notificationId)) return false;
    existingNotificationIds.add(message.notificationId);
    return true;
  });
  return additions.length === 0 ? existing : [...existing, ...additions];
}
