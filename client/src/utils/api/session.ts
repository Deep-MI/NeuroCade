import type { SessionBootstrap } from '../../types';

import { appJson } from './core';

export async function fetchSession(): Promise<SessionBootstrap> {
  return appJson<SessionBootstrap>('/session', 'Failed to bootstrap session');
}
