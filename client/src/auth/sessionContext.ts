import { createContext, useContext } from 'react';

import type { SessionBootstrap } from '../types';

export interface SessionState {
  session: SessionBootstrap | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export const SessionContext = createContext<SessionState | null>(null);

export function useAppSession(): SessionState {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error('useAppSession must be used inside AppSessionProvider');
  }
  return context;
}
