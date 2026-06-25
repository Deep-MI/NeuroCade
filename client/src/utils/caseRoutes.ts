export interface CaseRouteTarget {
  workspace_id?: string | null;
  case_id: string;
  subject_name?: string | null;
}

export function workspaceCasesPath(workspaceId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}/cases`;
}

export function caseRouteSlug(workspaceId: string, caseId: string, fallbackTitle?: string | null): string {
  const prefix = `${workspaceId}__`;
  if (caseId.startsWith(prefix)) return caseId.slice(prefix.length);
  if (fallbackTitle) return fallbackTitle;
  throw new Error('Case id must use the canonical workspace-prefixed format.');
}

export function caseViewerPath(workspaceId: string, caseId: string, fallbackTitle?: string | null): string {
  return `${workspaceCasesPath(workspaceId)}/${encodeURIComponent(caseRouteSlug(workspaceId, caseId, fallbackTitle))}`;
}

export function caseSummaryPath(caseItem: CaseRouteTarget, fallbackWorkspaceId?: string | null): string | null {
  const workspaceId = caseItem.workspace_id ?? fallbackWorkspaceId;
  if (!workspaceId) return null;
  return caseViewerPath(workspaceId, caseItem.case_id, caseItem.subject_name);
}
