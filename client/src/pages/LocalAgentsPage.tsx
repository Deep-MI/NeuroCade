import { agentConnectionLabel } from '../utils/agentConnectionLabel';
import { ArrowLeft, Bot, Check, Copy, Download, Monitor, ShieldCheck, Terminal, Unplug } from 'lucide-react';
import { AppSettingsMenu } from '../components/AppSettingsMenu';
import { useAppAppearance } from '../hooks/useAppPreferences';
import { useCallback, useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router';
import { ChatApprovalContent } from '../components/ChatApprovalContent';
import type { AssistantApprovalRequest } from '../types';
import { appFetch, appJson, expectOk, jsonRequest } from '../utils/api';
import { useFrontendConfig } from '../auth/frontendConfigContext';

import { listAgentConnections, type AgentConnection } from '../utils/api/agentConnections';
interface Invocation { invocation_id: string; client_id: string; tool: string; case_id: string | null; status: string; arguments: Record<string, unknown>; presentation: AssistantApprovalRequest['presentation']; run_id: string | null }
interface Page<T> { items: T[]; next_cursor: string | null }
interface Workspace { id: string; name: string }
const headers = { 'X-NeuroCade-UI': '1' };

export function LocalAgentsPage() {
  const { mcp_enabled: mcpEnabled } = useFrontendConfig();
  const [isLight] = useAppAppearance();
  if (!mcpEnabled) {
    return <div className={`nc-shell ${isLight ? 'nc-light' : ''}`}>
      <header className="nc-topbar">
        <Link to="/" className="nc-logo"><img src="/logo-192.png" alt="" className="nc-logo-mark" /><span>NeuroCade</span></Link>
        <span className="hidden border-l border-[var(--nc-border)] pl-4 text-xs text-[var(--nc-tx-dim)] sm:block">Settings / Connect external agents</span>
        <div className="flex-1" /><AppSettingsMenu />
      </header>
      <main className="flex-1 overflow-y-auto px-5 py-8 sm:px-8">
        <div className="mx-auto max-w-3xl space-y-6">
          <Link to="/" className="inline-flex items-center gap-2 text-xs text-[var(--nc-tx-muted)]"><ArrowLeft size={13} />Back to workspace</Link>
          <section className="nc-card-static space-y-3 p-6 text-sm">
            <Bot size={24} className="text-[var(--nc-interactive)]" />
            <h1 className="text-xl font-semibold">Local agent access is disabled</h1>
            <p className="text-[var(--nc-tx-muted)]">Restart NeuroCade with <code>./scripts/run.sh start -d --mcp</code> to connect and manage external agents.</p>
          </section>
        </div>
      </main>
    </div>;
  }
  return <EnabledLocalAgentsPage isLight={isLight} />;
}

function EnabledLocalAgentsPage({ isLight }: { isLight: boolean }) {
  const location = useLocation();
  const [clients, setClients] = useState<AgentConnection[]>([]);
  const [activity, setActivity] = useState<Invocation[]>([]);
  const [pending, setPending] = useState<Invocation[]>([]);
  const [focused, setFocused] = useState<Invocation | null>(null);
  const [clientAfter, setClientAfter] = useState('');
  const [pendingAfter, setPendingAfter] = useState('');
  const [activityAfter, setActivityAfter] = useState('');
  const [clientNext, setClientNext] = useState<string | null>(null);
  const [pendingNext, setPendingNext] = useState<string | null>(null);
  const [activityNext, setActivityNext] = useState<string | null>(null);
  const focusedId = new URLSearchParams(location.search).get('invocation');
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState(new URLSearchParams(location.search).get('workspace') ?? '');
  const [name, setName] = useState('Local assistant');
  const [access, setAccess] = useState('standard');
  const [maximumAccess, setMaximumAccess] = useState('standard');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const [executable, setExecutable] = useState('');
  const [agent, setAgent] = useState('codex-plugin');
  const [copied, setCopied] = useState(false);
  const [setupPrompt, setSetupPrompt] = useState('');
  const shellQuote = (value: string) => "'" + value.replaceAll("'", "'\"'\"'") + "'";
  const refresh = useCallback(async () => {
    try {
      const [status, list, requests, events, selected] = await Promise.all([
        appJson<{ access: string; host_executable: string }>('/mcp/status', 'Launch NeuroCade with --mcp to enable local agents.', { headers }),
        listAgentConnections(clientAfter),
        appJson<Page<Invocation>>(`/mcp/invocations?pending=true&after=${encodeURIComponent(pendingAfter)}`, 'Could not load requests', { headers }),
        appJson<Page<Invocation>>(`/mcp/invocations?after=${encodeURIComponent(activityAfter)}`, 'Could not load activity', { headers }),
        focusedId ? appJson<Invocation>(`/mcp/invocations/${encodeURIComponent(focusedId)}`, 'Could not load selected request', { headers }) : Promise.resolve(null),
      ]);
      setExecutable(status.host_executable); setMaximumAccess(status.access); setClients(list.items); setActivity(events.items);
      setPending(requests.items); setFocused(selected);
      setClientNext(list.next_cursor); setPendingNext(requests.next_cursor); setActivityNext(events.next_cursor); setError('');
      if (status.access === 'read') setAccess('read');
    } catch (err) { setError(String(err)); }
  }, [clientAfter, pendingAfter, activityAfter, focusedId]);
  useEffect(() => {
    void refresh();
    void appJson<Workspace[]>('/workspaces', 'Could not load workspaces').then(setWorkspaces).catch(err => setError(String(err)));
    const timer = setInterval(() => { void refresh(); }, 5000);
    return () => clearInterval(timer);
  }, [refresh]);
  async function action(path: string, body?: unknown, method = 'POST') {
    setBusy(true);
    try { await appJson(path, 'Action failed', jsonRequest(body, { method, headers })); await refresh(); }
    catch (err) { setError(String(err)); } finally { setBusy(false); }
  }
  async function createSetupPrompt(target = agent, connectionName = target) {
    setBusy(true);
    setCopied(false);
    try {
      const grant = await appJson<{ url: string; code: string; pairing_id: string; installation_id: string }>('/mcp/pairings', 'Could not prepare pairing',
        jsonRequest({ name: connectionName, workspace_id: workspace, access, require_approval: false }, { method: 'POST', headers }));
      const command = [executable, 'setup', '--client', target, '--url', grant.url.replace(/\/mcp$/, ''),
        '--pairing-id', grant.pairing_id, '--installation-id', grant.installation_id, '--pairing-code', grant.code].map(shellQuote).join(' ');
      const prompt = `Connect yourself to my local NeuroCade workspace. Run this command on this computer within 10 minutes: ${command}
The single-use pairing code saves credentials automatically. Do not print tokens or repeat the pairing code. If pairing has expired, ask me to generate a new prompt in NeuroCade. Reload your MCP tools or start a new local task, then call neurocade_get_context and verify upload and download tools. Show the case, input scan and analysis and confirm with me before starting an analysis. If you cannot execute commands on this Mac, explain that limitation.`;
      setSetupPrompt(prompt);
      try { await navigator.clipboard.writeText(prompt); setCopied(true); }
      catch { setError('Prompt ready below. Copy it from the text box.'); }
    } catch (err) { setError(String(err)); } finally { setBusy(false); }
  }
  async function downloadExtension() {
    setBusy(true);
    try {
      const response = await appFetch('/mcp/desktop-extension', jsonRequest({ name: 'Claude Desktop', workspace_id: workspace, access, require_approval: false }, { method: 'POST', headers }));
      await expectOk(response, 'Could not download the extension');
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'neurocade.mcpb'; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setCreated(true);
    } catch (err) { setError(String(err)); } finally { setBusy(false); }
  }
  function pagination(after: string, next: string | null, update: (value: string) => void) {
    return <div className="flex gap-2">
      {after && <button className="nc-btn" onClick={() => update('')}>Newest</button>}
      {next && <button className="nc-btn" onClick={() => update(next)}>Older</button>}
    </div>;
  }
  function requestCard(item: Invocation) {
    return <article className="nc-card-static space-y-3 p-4 text-sm" key={item.invocation_id}>
      <p><strong>{clients.find(c => c.id === item.client_id)?.name ?? 'External agent'}</strong> · {item.tool} · {item.status}</p>
      <Link to={`/local-agents?invocation=${encodeURIComponent(item.invocation_id)}`}>Request details</Link>
      {item.case_id && <p>Case: {item.case_id}</p>}
      {item.presentation && Object.keys(item.presentation).length > 0 && <ChatApprovalContent approval={{ name: item.tool, arguments: item.arguments, digest: '', description: item.tool, presentation: item.presentation }} />}
      {item.run_id && <p>Run: <code>{item.run_id}</code></p>}
      {item.status === 'awaiting_approval' && <div className="flex gap-2"><button className="nc-btn nc-btn-active" disabled={busy} onClick={() => void action(`/mcp/invocations/${item.invocation_id}/decision`, { approve: true })}>Approve</button><button className="nc-btn" disabled={busy} onClick={() => void action(`/mcp/invocations/${item.invocation_id}/decision`, { approve: false })}>Deny</button></div>}
    </article>;
  }
  const supported = [
    { id: 'codex-plugin', label: 'Codex', detail: 'Local desktop or CLI', icon: Terminal },
    { id: 'chatgpt', label: 'ChatGPT Work', detail: 'Local tasks on this Mac', icon: Monitor },
    { id: 'claude-desktop', label: 'Claude Desktop', detail: 'macOS extension', icon: Monitor },
    { id: 'claude-code', label: 'Claude Code', detail: 'Local CLI', icon: Terminal },
  ];
  return <div className={`nc-shell ${isLight ? 'nc-light' : ''}`}>
    <header className="nc-topbar">
      <Link to="/" className="nc-logo"><img src="/logo-192.png" alt="" className="nc-logo-mark" /><span>NeuroCade</span></Link>
      <span className="hidden border-l border-[var(--nc-border)] pl-4 text-xs text-[var(--nc-tx-dim)] sm:block">Settings / Connect external agents</span>
      <div className="flex-1" /><AppSettingsMenu />
    </header>
    <main className="flex-1 overflow-y-auto px-5 py-8 sm:px-8">
      <div className="mx-auto max-w-5xl space-y-6">
        <Link to="/" className="inline-flex items-center gap-2 text-xs text-[var(--nc-tx-muted)]"><ArrowLeft size={13} />Back to workspace</Link>
        <div className="flex items-start gap-4">
          <div className="rounded-lg border border-[var(--nc-interactive-border)] bg-[var(--nc-interactive-subtle)] p-3 text-[var(--nc-interactive)]"><Bot size={24} /></div>
          <div><h1 className="text-2xl font-semibold tracking-tight">Connect external agents</h1><p className="mt-1 text-sm text-[var(--nc-tx-muted)]">Let your assistant upload scans, run analyses and open results in one workspace.</p></div>
        </div>
        <div className="nc-card-static grid gap-4 p-4 text-sm sm:grid-cols-2">
          <div><p className="mb-1 flex items-center gap-2 font-medium"><Check size={15} className="text-[var(--nc-success)]" />Connect on this computer</p><p className="text-xs text-[var(--nc-tx-muted)]">ChatGPT Work (local), Codex, Claude Desktop and Claude Code. Keep NeuroCade running.</p></div>
          <div className="sm:border-l sm:border-[var(--nc-border)] sm:pl-5"><p className="mb-1 flex items-center gap-2 font-medium"><Unplug size={15} className="text-[var(--nc-tx-dim)]" />Cloud connections aren't included</p><p className="text-xs text-[var(--nc-tx-muted)]">ChatGPT web and hosted agents need a remote connection. Cowork's sandbox cannot run the Mac installer.</p></div>
        </div>
        {error && <p role="alert" className="rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] p-3 text-sm text-[var(--nc-danger)]">{error}</p>}
        <div className="grid items-start gap-6 lg:grid-cols-[1.35fr_1fr]">
          <section className="nc-card-static space-y-5 p-5">
            <div><h2 className="font-semibold">Connect an assistant</h2><p className="mt-1 text-xs text-[var(--nc-tx-dim)]">Choose a client, then the workspace it can access.</p></div>
            <div className="grid grid-cols-2 gap-2">{supported.map(({ id, label, detail, icon: Icon }) => <button key={id} disabled={busy} onClick={() => { setAgent(id); setCopied(false); setCreated(false); setSetupPrompt(''); }} aria-pressed={agent === id} className={`nc-agent-choice ${agent === id ? 'is-selected' : ''}`}><Icon size={18} /><span className="font-medium">{label}</span><span className="text-[11px] text-[var(--nc-tx-dim)]">{detail}</span></button>)}</div>
            <label className="block space-y-2 text-xs font-medium">Workspace<select className="nc-agent-input nc-select w-full" value={workspace} disabled={busy} onChange={event => { setWorkspace(event.target.value); setCreated(false); setCopied(false); setSetupPrompt(''); }}><option value="">Choose a workspace</option>{workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            {agent === 'claude-desktop' ? <div className="space-y-3 text-sm">
              <p>Download and open the extension in Claude. Your selected workspace connects automatically.</p>
              <button className="nc-btn nc-btn-active" disabled={busy || !workspace || !executable || !!error} onClick={() => void downloadExtension()}><Download size={14} />Download Claude extension</button>
              <p className="text-xs text-[var(--nc-tx-muted)]">Install and launch within 10 minutes, then start a new conversation. No connection file or token entry needed.</p>
              {created && <p role="status" className="text-xs text-[var(--nc-success)]">Extension downloaded. Open it in Claude to pair. If it expires, download a fresh extension.</p>}
            </div> : <div className="space-y-3">
              <p className="text-sm">Copy the setup prompt into a local {agent === 'claude-code' ? 'Claude Code' : agent === 'chatgpt' ? 'ChatGPT Work' : 'Codex'} task.</p>
              <button className="nc-btn nc-btn-active" disabled={busy || !workspace || !executable || !!error} onClick={() => { void createSetupPrompt(); }}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copy a fresh setup prompt' : 'Copy setup prompt'}</button>
              <p className="text-xs text-[var(--nc-tx-muted)]">Run within 10 minutes. Your agent pairs, saves credentials and installs the connection. Start a new local task afterwards.</p>
            </div>}
            {setupPrompt && <details open><summary className="cursor-pointer text-xs text-[var(--nc-tx-dim)]">Setup prompt · single use, valid for 10 minutes</summary><textarea className="nc-agent-input mt-2 w-full text-xs" rows={6} readOnly aria-label="Agent setup prompt" value={setupPrompt} /></details>}
            <div className="flex gap-2 border-t border-[var(--nc-border)] pt-4 text-xs text-[var(--nc-tx-muted)]"><ShieldCheck size={15} className="shrink-0 text-[var(--nc-interactive)]" /><p>Your agent confirms in conversation. Extra NeuroCade approval starts off. After connecting, change it for each agent in Settings → Agent approval.</p></div>
            <details className="border-t border-[var(--nc-border)] pt-3 text-xs"><summary className="cursor-pointer text-[var(--nc-tx-dim)]">Manual connection</summary><div className="mt-3 space-y-3">
              <label className="block">Connection name<input className="nc-agent-input mt-1 w-full" value={name} maxLength={100} onChange={event => setName(event.target.value)} /></label>
              <label className="block">Access<select className="nc-agent-input nc-select mt-1 w-full" value={access} onChange={event => { setAccess(event.target.value); setSetupPrompt(''); setCopied(false); }}><option value="read">Read only</option>{maximumAccess === 'standard' && <option value="standard">Read and run analyses</option>}</select></label>
              <button className="nc-btn" disabled={busy || !workspace || !executable || !name.trim() || !!error} onClick={() => { void createSetupPrompt('manual', name); }}>Create setup prompt</button>
              <p className="text-[var(--nc-tx-muted)]">Run the prompt locally. The connector saves credentials and prints the command to register in your MCP client.</p>
            </div></details>
          </section>
          <div className="space-y-5">
            <section className="nc-card-static p-5"><div className="mb-4 flex items-center justify-between"><h2 className="font-semibold">Connections</h2><span className="nc-chip">{clients.filter(item => !item.revoked).length} active</span></div>
              {clients.length === 0 && <div className="py-6 text-center text-sm text-[var(--nc-tx-dim)]"><Bot size={24} className="mx-auto mb-2" />No connections yet.<p className="mt-1 text-xs">Choose an assistant to get started.</p></div>}
              <div className="divide-y divide-[var(--nc-border)]">{clients.map(client => <div className="py-3 first:pt-0" key={client.id}><div className="flex items-center justify-between gap-3"><strong className="min-w-0 truncate text-sm" title={client.name}>{agentConnectionLabel(client.name, workspaces.find(item => item.id === client.workspace_id)?.name)}</strong>{!client.revoked && <button className="nc-btn text-xs" disabled={busy} onClick={() => void action(`/mcp/clients/${client.id}`, undefined, 'DELETE')}>Revoke</button>}</div><p className="mt-1 text-xs text-[var(--nc-tx-muted)]">{workspaces.find(item => item.id === client.workspace_id)?.name ?? client.workspace_id}</p><p className="mt-1 text-xs text-[var(--nc-tx-dim)]">{client.revoked ? 'Revoked' : client.last_seen ? 'Connected' : 'Waiting for first connection'} · {client.access === 'read' ? 'Read only' : 'Read & run'} · {client.require_approval ? 'App approval on' : 'Confirms in agent'}</p></div>)}</div>
              {pagination(clientAfter, clientNext, setClientAfter)}
            </section>
            <p className="px-1 text-xs leading-relaxed text-[var(--nc-tx-dim)]">Processing stays in NeuroCade. Information your assistant requests may be sent to its model provider. You can revoke access at any time.</p>
          </div>
        </div>
        {focused && <section className="space-y-3"><h2 className="font-semibold">Selected request</h2>{requestCard(focused)}</section>}
        {pending.length > 0 && <section className="space-y-3"><h2 className="font-semibold">Awaiting approval</h2>{pending.filter(item => item.invocation_id !== focused?.invocation_id).map(requestCard)}{pagination(pendingAfter, pendingNext, setPendingAfter)}</section>}
        <details className="nc-card-static p-4"><summary className="cursor-pointer text-sm font-medium">Recent activity <span className="ml-2 text-xs font-normal text-[var(--nc-tx-dim)]">{activity.length} on this page</span></summary><div className="mt-4 space-y-3">{activity.length === 0 ? <p className="text-xs text-[var(--nc-tx-dim)]">No external activity yet.</p> : activity.filter(item => item.invocation_id !== focused?.invocation_id).map(requestCard)}{pagination(activityAfter, activityNext, setActivityAfter)}</div></details>
      </div>
    </main>
  </div>;
}
