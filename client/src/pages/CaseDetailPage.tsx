import { Suspense, lazy } from 'react';
import { useParams } from 'react-router';

const CaseWorkspace = lazy(() => import('../CaseWorkspace'));


export function CaseDetailPage() {
  const { workspaceId, caseId } = useParams<{ workspaceId: string; caseId: string }>();

  if (!workspaceId || !caseId) {
    return <div className="nc-app-page px-6 py-8 text-sm text-[var(--nc-danger)]">Missing workspace or case id.</div>;
  }
  return (
    <div>
      <Suspense fallback={<div className="nc-app-page p-6 text-sm text-[var(--nc-tx-muted)]">Loading case workspace...</div>}>
        <CaseWorkspace
          initialCaseId={caseId}
          initialWorkspaceId={workspaceId}
        />
      </Suspense>
    </div>
  );
}
