export function createGuiSessionId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `gui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function defaultPaneWidth(compactWidth: number, largeWidth: number): number {
  return typeof window !== 'undefined' && window.innerWidth >= 1440 ? largeWidth : compactWidth;
}
