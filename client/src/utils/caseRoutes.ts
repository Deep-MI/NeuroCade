interface CaseRouteTarget {
  workspace_id: string;
  id: string;
}

export function workspaceCasesPath(workspaceId: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}/cases`;
}

export function caseViewerPath(workspaceId: string, caseId: string): string {
  return `${workspaceCasesPath(workspaceId)}/${encodeURIComponent(caseId)}`;
}

export function caseSummaryPath(caseItem: CaseRouteTarget): string {
  return caseViewerPath(caseItem.workspace_id, caseItem.id);
}
