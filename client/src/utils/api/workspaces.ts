import type {
  ArtifactListItem,
  WorkspaceBatchRunDetail,
  WorkspaceBatchRunSummary,
  WorkspaceSummary,
} from '../../types';

import { appJson, jsonRequest } from './core';
import { normalizeArtifactListItem, type ApiArtifactListItem } from './cases';

export async function fetchWorkspaceArtifacts(workspaceId: string): Promise<ArtifactListItem[]> {
  const artifacts = await appJson<ApiArtifactListItem[]>(
    `/workspaces/${encodeURIComponent(workspaceId)}/artifacts`,
    'Failed to fetch workspace artifacts',
  );
  return artifacts.map(normalizeArtifactListItem);
}

export async function fetchWorkspaceBatchRuns(workspaceId: string): Promise<WorkspaceBatchRunSummary[]> {
  return appJson<WorkspaceBatchRunSummary[]>(
    `/workspaces/${encodeURIComponent(workspaceId)}/batch-runs`,
    'Failed to fetch workspace batch runs',
  );
}

export async function cancelWorkspaceBatchRun(workspaceId: string, runId: string): Promise<WorkspaceBatchRunDetail> {
  return appJson<WorkspaceBatchRunDetail>(
    `/workspaces/${encodeURIComponent(workspaceId)}/batch-runs/${encodeURIComponent(runId)}/cancel`,
    'Failed to cancel workspace batch run',
    { method: 'POST' },
  );
}

export async function createWorkspace(name: string, description?: string | null): Promise<WorkspaceSummary> {
  return appJson<WorkspaceSummary>(
    '/workspaces',
    'Failed to create workspace',
    jsonRequest({ name, description }, { method: 'POST' }),
  );
}

export async function renameWorkspace(workspaceId: string, name: string, description?: string | null): Promise<WorkspaceSummary> {
  return appJson<WorkspaceSummary>(
    `/workspaces/${encodeURIComponent(workspaceId)}`,
    'Failed to rename workspace',
    jsonRequest({ name, ...(description === undefined ? {} : { description }) }, { method: 'PATCH' }),
  );
}

export async function deleteWorkspace(workspaceId: string, confirmNonEmpty = false): Promise<{ deleted: string }> {
  return appJson<{ deleted: string }>(
    `/workspaces/${encodeURIComponent(workspaceId)}`,
    'Failed to delete workspace',
    jsonRequest(
      {
        confirm_non_empty_delete: confirmNonEmpty,
      },
      { method: 'DELETE' },
    ),
  );
}
