import { createUuid } from './randomUuid';

export function createGuiSessionId(): string {
  const storageKey = 'neurocade.gui-session-id';
  if (typeof window !== 'undefined') {
    const existing = window.sessionStorage.getItem(storageKey);
    if (existing) return existing;
    const created = `gui-${createUuid()}`;
    window.sessionStorage.setItem(storageKey, created);
    return created;
  }
  return `gui-${createUuid()}`;
}

export function defaultPaneWidth(compactWidth: number, largeWidth: number): number {
  return typeof window !== 'undefined' && window.innerWidth >= 1440 ? largeWidth : compactWidth;
}
