import type { WorkspaceSummary } from '../../types';

import { appJson, jsonRequest } from './core';

export async function createWorkspace(name: string, description?: string | null): Promise<WorkspaceSummary> {
  return appJson<WorkspaceSummary>(
    '/workspaces',
    'Failed to create workspace',
    jsonRequest({ name, description }, { method: 'POST' }),
  );
}

export async function updateWorkspace(
  workspaceId: string,
  updates: { name?: string; description?: string | null },
): Promise<WorkspaceSummary> {
  return appJson<WorkspaceSummary>(
    `/workspaces/${encodeURIComponent(workspaceId)}`,
    'Failed to update workspace',
    jsonRequest(updates, { method: 'PATCH' }),
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
