import { createUuid } from './randomUuid.js';

// One identity per live document. sessionStorage is copied when a tab is
// duplicated, so it cannot identify the target of an agent navigation command.
const sessions = new WeakMap<Window, string>();

export function createGuiSessionId(): string {
  if (typeof window === 'undefined') return `gui-${createUuid()}`;
  let session = sessions.get(window);
  if (!session) {
    session = `gui-${createUuid()}`;
    sessions.set(window, session);
  }
  return session;
}

export function defaultPaneWidth(compactWidth: number, largeWidth: number): number {
  return typeof window !== 'undefined' && window.innerWidth >= 1440 ? largeWidth : compactWidth;
}
