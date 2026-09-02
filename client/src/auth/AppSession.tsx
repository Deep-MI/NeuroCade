import { ClerkProvider, SignInButton, SignedIn, SignedOut, UserButton, useAuth } from '@clerk/clerk-react';
import { useEffect, useLayoutEffect, useState, type PropsWithChildren } from 'react';
import { Navigate, useLocation } from 'react-router';

import { SessionContext } from './sessionContext';
import type { SessionState } from './sessionContext';
import { useFrontendConfig } from './frontendConfigContext';
import { fetchSession, setAccessTokenProvider } from '../utils/api';

function SessionProviderInner({ children, refreshKey = 'dev', enabled = true }: PropsWithChildren<{ refreshKey?: string; enabled?: boolean }>) {
  const [session, setSession] = useState<SessionState['session']>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      setSession(await fetchSession());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!enabled) return;
    void refresh();
  }, [enabled, refreshKey]);

  return (
    <SessionContext.Provider value={{ session, loading, error, refresh }}>
      {children}
    </SessionContext.Provider>
  );
}

export function AppSessionProvider({ children }: PropsWithChildren) {
  const { clerk_publishable_key: publishableKey, local_auth_enabled: localAuthEnabled } = useFrontendConfig();
  if (publishableKey && !localAuthEnabled) {
    return (
      <ClerkProvider
        publishableKey={publishableKey}
        signInUrl="/sign-in"
        signUpUrl="/sign-up"
        signInFallbackRedirectUrl="/"
        signUpFallbackRedirectUrl="/"
        afterSignOutUrl="/sign-in"
      >
        <ClerkSessionProvider>{children}</ClerkSessionProvider>
      </ClerkProvider>
    );
  }
  return <DevSessionProvider>{children}</DevSessionProvider>;
}

function ClerkSessionProvider({ children }: PropsWithChildren) {
  const { getToken, isLoaded, userId } = useAuth();
  const { clerk_jwt_template: configuredJwtTemplate } = useFrontendConfig();
  const jwtTemplate = configuredJwtTemplate?.trim();

  useLayoutEffect(() => {
    setAccessTokenProvider(() => {
      if (!isLoaded) return Promise.resolve(null);
      return getToken(jwtTemplate ? { template: jwtTemplate } : undefined);
    });
    return () => setAccessTokenProvider(() => Promise.resolve(null));
  }, [getToken, isLoaded, userId, jwtTemplate]);

  return (
    <SessionProviderInner refreshKey={`${isLoaded}:${userId ?? ''}`} enabled={isLoaded && !!userId}>
      {children}
    </SessionProviderInner>
  );
}

function DevSessionProvider({ children }: PropsWithChildren) {
  useEffect(() => {
    setAccessTokenProvider(() => Promise.resolve(null));
    return () => setAccessTokenProvider(() => Promise.resolve(null));
  }, []);

  return <SessionProviderInner>{children}</SessionProviderInner>;
}

export function SessionActions() {
  const { clerk_publishable_key: publishableKey, local_auth_enabled: localAuthEnabled } = useFrontendConfig();
  if (!publishableKey || localAuthEnabled) {
    return null;
  }
  return (
    <>
      <SignedOut>
        <SignInButton mode="modal">
          <button className="nc-btn nc-btn-active">Sign In</button>
        </SignInButton>
      </SignedOut>
      <SignedIn>
        <UserButton />
      </SignedIn>
    </>
  );
}

export function RequireAuth({ children }: PropsWithChildren) {
  const { clerk_publishable_key: publishableKey, local_auth_enabled: localAuthEnabled } = useFrontendConfig();

  if (!publishableKey || localAuthEnabled) {
    return <>{children}</>;
  }

  return <ClerkRequireAuth>{children}</ClerkRequireAuth>;
}

function ClerkRequireAuth({ children }: PropsWithChildren) {
  const location = useLocation();
  const { isLoaded, isSignedIn } = useAuth();

  if (!isLoaded) {
    return <div className="nc-app-page px-6 py-10 text-sm text-[var(--nc-tx-muted)]">Loading authentication...</div>;
  }

  if (!isSignedIn) {
    return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
