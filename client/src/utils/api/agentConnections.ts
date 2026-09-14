import { appJson } from './core';

export interface AgentConnection {
  id: string;
  name: string;
  workspace_id: string;
  access: string;
  require_approval: boolean;
  revoked: boolean;
  last_seen: number | null;
}

export interface ConnectionPage { items: AgentConnection[]; next_cursor: string | null }

export function listAgentConnections(after = ''): Promise<ConnectionPage> {
  return appJson(`/mcp/clients?after=${encodeURIComponent(after)}`, 'Could not load external agents', {
    headers: { 'X-NeuroCade-UI': '1' },
  });
}

export async function listActiveAgentConnections(): Promise<AgentConnection[]> {
  const connections = new Map<string, AgentConnection>();
  const cursors = new Set<string>();
  let after = '';
  do {
    cursors.add(after);
    const page = await listAgentConnections(after);
    for (const connection of page.items) {
      if (!connection.revoked) connections.set(connection.id, connection);
    }
    after = page.next_cursor ?? '';
  } while (after && !cursors.has(after));
  return [...connections.values()];
}
