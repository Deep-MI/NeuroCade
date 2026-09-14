/** Separate installer-generated workspace suffixes from the agent's display name. */
export function agentConnectionLabel(name: string, workspaceName?: string): string {
  const suffix = workspaceName ? ` · ${workspaceName}` : '';
  const agentName = suffix && name.endsWith(suffix) ? name.slice(0, -suffix.length) : name;
  const clientNames: Record<string, string> = {
    'claude-desktop': 'Claude Desktop',
    'claude-code': 'Claude Code',
    'codex-plugin': 'Codex',
    codex: 'Codex',
    chatgpt: 'ChatGPT',
  };
  return clientNames[agentName] ?? agentName;
}
