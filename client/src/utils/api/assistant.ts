import type {
  AssistantActiveTurnResponse,
  AssistantHistoryResponse,
  AssistantScope,
  AssistantTurnCancelResponse,
  ProviderSummary,
} from '../../types';

import { appJson, appOk, jsonRequest } from './core';

function assistantTurnParams(workspaceId: string, scope: AssistantScope, caseId?: string | null): URLSearchParams {
  const params = new URLSearchParams({ workspace_id: workspaceId, scope });
  if (scope === 'case' && caseId) {
    params.set('case_id', caseId);
  }
  return params;
}

export async function fetchAssistantHistory(
  workspaceId: string,
  scope: AssistantScope,
  caseId?: string | null,
): Promise<AssistantHistoryResponse> {
  const params = assistantTurnParams(workspaceId, scope, caseId);
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
  const params = assistantTurnParams(workspaceId, scope, caseId);
  await appOk(`/assistant/history?${params.toString()}`, 'Failed to clear assistant history', {
    method: 'DELETE',
  });
}

export async function fetchActiveAssistantTurn(
  workspaceId: string,
  scope: AssistantScope,
  caseId?: string | null,
): Promise<AssistantActiveTurnResponse> {
  const params = assistantTurnParams(workspaceId, scope, caseId);
  return appJson<AssistantActiveTurnResponse>(
    `/assistant/turns/active?${params.toString()}`,
    'Failed to check assistant turn status',
  );
}

export async function cancelAssistantTurn(
  turnId: string,
  workspaceId: string,
  scope: AssistantScope,
  caseId?: string | null,
): Promise<AssistantTurnCancelResponse> {
  const params = assistantTurnParams(workspaceId, scope, caseId);
  return appJson<AssistantTurnCancelResponse>(
    `/assistant/turns/${encodeURIComponent(turnId)}/cancel?${params.toString()}`,
    'Failed to cancel assistant turn',
    jsonRequest({}, { method: 'POST' }),
  );
}

export async function fetchProviders(): Promise<ProviderSummary[]> {
  return appJson<ProviderSummary[]>('/providers', 'Failed to fetch provider configuration');
}
