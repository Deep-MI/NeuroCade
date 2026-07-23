export function createGuiSessionId(): string {
  return `gui-${globalThis.crypto.randomUUID()}`;
}

export function defaultPaneWidth(compactWidth: number, largeWidth: number): number {
  return typeof window !== 'undefined' && window.innerWidth >= 1440 ? largeWidth : compactWidth;
}
