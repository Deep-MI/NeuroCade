import { SignUp, SignedIn, SignedOut } from '@clerk/clerk-react';
import { Navigate, useLocation } from 'react-router';

import { useFrontendConfig } from '../auth/frontendConfigContext';
import { useAppSession } from '../auth/sessionContext';
import { APP_DISPLAY_NAME } from '../constants';
import { workspaceCasesPath } from '../utils/caseRoutes';


function redirectTargetFromState(state: unknown, fallback: string): string {
  if (typeof state !== 'object' || state === null) {
    return fallback;
  }
  const from = (state as Record<string, unknown>).from;
  return typeof from === 'string' ? from : fallback;
}

export function SignUpPage() {
  const { clerk_publishable_key: publishableKey, local_auth_enabled: localAuthEnabled } = useFrontendConfig();
  const { session, loading } = useAppSession();
  const location = useLocation();
  const defaultWorkspacePath = session?.default_workspace_id
    ? workspaceCasesPath(session.default_workspace_id)
    : '/';
  const redirectTarget = redirectTargetFromState(location.state, defaultWorkspacePath);

  if (localAuthEnabled) {
    return loading ? (
      <div className="nc-app-page px-6 py-10 text-sm text-[var(--nc-tx-muted)]">Loading session...</div>
    ) : (
      <Navigate to={redirectTarget} replace />
    );
  }

  if (!publishableKey) {
    return (
      <div className="nc-app-page flex items-center justify-center p-6">
        <div className="nc-card-static w-full max-w-md p-8">
          <h1 className="mb-2 text-2xl font-semibold text-[var(--nc-tx)]">{APP_DISPLAY_NAME} Sign Up</h1>
          <p className="mb-6 text-sm text-[var(--nc-tx-muted)]">
            Clerk is not configured. Ask an administrator to configure institutional authentication.
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      <SignedIn>
        {loading ? <div className="nc-app-page px-6 py-10 text-sm text-[var(--nc-tx-muted)]">Loading session...</div> : <Navigate to={defaultWorkspacePath} replace />}
      </SignedIn>
      <SignedOut>
        <div className="nc-app-page flex items-center justify-center p-6">
          <div className="nc-card-static w-full max-w-md p-8">
            <h1 className="mb-2 text-2xl font-semibold text-[var(--nc-tx)]">{APP_DISPLAY_NAME} Sign Up</h1>
            <p className="mb-6 text-sm text-[var(--nc-tx-muted)]">
              Create a workspace-backed account. Sample content will be seeded into your personal workspace on first login.
            </p>
            <SignUp
              routing="path"
              path="/sign-up"
              signInUrl="/sign-in"
              fallbackRedirectUrl={redirectTarget}
              forceRedirectUrl={redirectTarget}
            />
          </div>
        </div>
      </SignedOut>
    </>
  );
}
