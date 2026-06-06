import type { AssistantHistoryResponse, AssistantScope } from '../../types';

import { appJson, appOk } from './core';

export async function fetchAssistantHistory(
  workspaceId: string,
  scope: AssistantScope,
  caseId?: string | null,
): Promise<AssistantHistoryResponse> {
  const params = new URLSearchParams({ workspace_id: workspaceId, scope });
  if (scope === 'case' && caseId) {
    params.set('case_id', caseId);
  }
  return appJson<AssistantHistoryResponse>(
    `/assistant/history?${params.toString()}`,
    'Failed to fetch assistant history',
  );
}

export async function clearAssistantHistory(
  workspaceId: string,
  scope: AssistantScope,
  caseId?: string | null,
): Promise<void> {
  const params = new URLSearchParams({ workspace_id: workspaceId, scope });
  if (scope === 'case' && caseId) {
    params.set('case_id', caseId);
  }
  await appOk(`/assistant/history?${params.toString()}`, 'Failed to clear assistant history', {
    method: 'DELETE',
  });
}
