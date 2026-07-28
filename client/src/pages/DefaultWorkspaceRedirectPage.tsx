import { Navigate } from 'react-router';

import { useAppSession } from '../auth/sessionContext';
import { workspaceCasesPath } from '../utils/caseRoutes';


export function DefaultWorkspaceRedirectPage() {
  const { session, loading, error } = useAppSession();
  const targetWorkspaceId = session?.default_workspace_id ?? session?.workspaces[0]?.id ?? null;
  const target = targetWorkspaceId ? workspaceCasesPath(targetWorkspaceId) : '/sign-in';

  if (loading) {
    return <div className="nc-app-page px-6 py-10 text-sm text-[var(--nc-tx-muted)]">Resolving workspace...</div>;
  }
  if (error) {
    return <div className="nc-app-page px-6 py-10 text-sm text-[var(--nc-danger)]">{error}</div>;
  }
  return <Navigate to={target} replace />;
}
