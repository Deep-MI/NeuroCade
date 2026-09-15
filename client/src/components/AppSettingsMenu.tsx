import { agentConnectionLabel } from '../utils/agentConnectionLabel';
import { useEffect, useRef, useState } from 'react';
import { Bot, ChevronRight, Moon, Settings, ShieldCheck, Sun } from 'lucide-react';
import { Link, useParams } from 'react-router';
import { useAppAppearance, useAssistantApproval, usePreferenceStatus } from '../hooks/useAppPreferences';
import { appJson, jsonRequest } from '../utils/api';
import { useFrontendConfig } from '../auth/frontendConfigContext';

import { listActiveAgentConnections, type AgentConnection } from '../utils/api/agentConnections';

export function AppSettingsMenu() {
  const { workspaceId } = useParams();
  const [open, setOpen] = useState(false);
  const [approvals, setApprovals] = useState(false);
  const [isLight, setIsLight] = useAppAppearance();
  const { mcp_enabled: mcpEnabled } = useFrontendConfig();
  const [assistantApproval, setAssistantApproval] = useAssistantApproval();
  const preferenceStatus = usePreferenceStatus();
  const [connections, setConnections] = useState<AgentConnection[]>([]);
  const [workspaces, setWorkspaces] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    window.addEventListener('pointerdown', outside); window.addEventListener('keydown', escape);
    return () => { window.removeEventListener('pointerdown', outside); window.removeEventListener('keydown', escape); };
  }, [open]);
  useEffect(() => {
    if (!open || !approvals || !mcpEnabled) return;
    let cancelled = false;
    void Promise.all([
      listActiveAgentConnections(),
      appJson<{ id: string; name: string }[]>('/workspaces', 'Could not load workspaces'),
    ]).then(([result, availableWorkspaces]) => { if (!cancelled) { setConnections(result); setWorkspaces(availableWorkspaces); setError(''); } })
      .catch(err => { if (!cancelled) setError(String(err)); });
    return () => { cancelled = true; };
  }, [open, approvals, mcpEnabled]);
  async function update(connection: AgentConnection, enabled: boolean) {
    setBusy(true);
    try {
      await appJson(`/mcp/clients/${connection.id}`, 'Could not update approval', jsonRequest({ require_approval: enabled }, { method: 'PATCH', headers: { 'X-NeuroCade-UI': '1' } }));
      setConnections(items => items.map(item => item.id === connection.id ? { ...item, require_approval: enabled } : item));
      setError('');
    } catch (err) { setError(String(err)); } finally { setBusy(false); }
  }
  return <div className="relative" ref={root}>
    <button ref={trigger} type="button" className={`nc-btn nc-icon-btn ${open ? 'nc-btn-active' : ''}`} aria-label="Settings" aria-expanded={open} aria-controls="app-settings-panel" onClick={() => setOpen(value => !value)}><Settings size={15} /></button>
    {open && <div id="app-settings-panel" role="dialog" aria-label="Settings" className="nc-settings-panel">
      <p className="nc-eyebrow px-3 pb-2 pt-1">Settings</p>
      <Link className="nc-settings-row" to={workspaceId ? `/local-agents?workspace=${encodeURIComponent(workspaceId)}` : '/local-agents'} onClick={() => setOpen(false)}><Bot size={16} />Connect external agents<ChevronRight size={14} className="ml-auto" /></Link>
      <button className="nc-settings-row" disabled={preferenceStatus.busy} onClick={() => setIsLight(!isLight)}>{isLight ? <Moon size={16} /> : <Sun size={16} />}Appearance<span className="ml-auto text-xs text-[var(--nc-tx-muted)]">{isLight ? 'Light → Dark' : 'Dark → Light'}</span></button>
      <button className="nc-settings-row" aria-expanded={approvals} onClick={() => setApprovals(value => !value)}><ShieldCheck size={16} />Agent approval<ChevronRight size={14} className={`ml-auto ${approvals ? 'rotate-90' : ''}`} /></button>
      {preferenceStatus.error && <p role="alert" className="px-3 text-xs text-[var(--nc-warning)]">{preferenceStatus.error}</p>}
      {approvals && <div className="space-y-3 border-t border-[var(--nc-border)] px-3 py-3 text-xs">
        <ApprovalToggle label="Built-in assistant" checked={assistantApproval} disabled={preferenceStatus.busy} onChange={setAssistantApproval} />
        <p className="text-[var(--nc-tx-muted)]">Require approval in NeuroCade before the assistant takes action.</p>
        <div className="border-t border-[var(--nc-border)] pt-3">
          <p className="mb-2 font-semibold">Connected external agents</p>
          {!mcpEnabled ? <p className="text-[var(--nc-tx-dim)]">Local agent access is disabled. Restart NeuroCade with local agents enabled to manage connections.</p> : <>
            <p className="mb-3 text-[var(--nc-tx-muted)]">Turn on to require extra approval in NeuroCade for that agent. Agents still confirm in conversation.</p>
            <div className="max-h-48 space-y-3 overflow-y-auto">{connections.map(connection => <ApprovalToggle key={connection.id} label={agentConnectionLabel(connection.name, workspaces.find(item => item.id === connection.workspace_id)?.name)} description={workspaces.find(item => item.id === connection.workspace_id)?.name ?? connection.workspace_id} checked={connection.require_approval} disabled={busy} onChange={enabled => { void update(connection, enabled); }} />)}</div>
            {connections.length === 0 && !error && <p className="text-[var(--nc-tx-dim)]">No external agents connected yet.</p>}
          </>}
        </div>
        {error && <p role="alert" className="text-[var(--nc-warning)]">{error}</p>}
      </div>}
    </div>}
  </div>;
}

function ApprovalToggle({ label, description, checked, disabled, onChange }: { label: string; description?: string; checked: boolean; disabled?: boolean; onChange: (enabled: boolean) => void }) {
  return <label className="flex items-center justify-between gap-3">
    <span className="min-w-0"><span className="block truncate" title={label}>{label}</span>{description && <span className="mt-1 block truncate text-[var(--nc-tx-muted)]" title={description}>{description}</span>}</span>
    <span className="flex shrink-0 items-center gap-2">
      <span className="text-[var(--nc-tx-muted)]" aria-hidden="true">{checked ? 'On' : 'Off'}</span>
      <input className="nc-approval-toggle" role="switch" aria-label={`Require approval for ${label}${description ? ` in ${description}` : ''}`} type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />
    </span>
  </label>;
}
