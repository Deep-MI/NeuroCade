import { useEffect, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, Database, RefreshCw, Server, Users } from 'lucide-react';
import { Link } from 'react-router-dom';

import { SessionActions } from '../auth/AppSession';
import { useAppSession } from '../auth/sessionContext';
import type { MonitoringAuditEventSummary, MonitoringEventSummary, MonitoringStatusItem, MonitoringSummary } from '../types';
import { fetchMonitoringSummary } from '../utils/api';


function statusClass(status: MonitoringStatusItem['status'] | MonitoringSummary['status']) {
  if (status === 'ok') return 'nc-chip-green';
  if (status === 'degraded' || status === 'unknown') return 'nc-chip-yellow';
  return 'nc-chip-red';
}


function formatTime(value?: string | null) {
  if (!value) return 'Never';
  return new Date(value).toLocaleString();
}


function formatNumber(value: number | undefined) {
  return typeof value === 'number' ? value.toLocaleString() : '0';
}


function detailText(details: Record<string, unknown>) {
  const entries = Object.entries(details).filter(([, value]) => value !== null && value !== undefined);
  if (entries.length === 0) return null;
  const formatValue = (value: unknown) => {
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    return JSON.stringify(value);
  };
  return entries
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${formatValue(value)}`)
    .join(' | ');
}


function ServiceRow({ service }: { service: MonitoringStatusItem }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-[var(--nc-border)] px-4 py-3 last:border-b-0">
      <div className="min-w-0">
        <div className="font-medium text-[var(--nc-tx)]">{service.name}</div>
        {service.message && <div className="mt-1 text-sm text-[var(--nc-tx-muted)]">{service.message}</div>}
        {detailText(service.details) && (
          <div className="nc-mono mt-1 truncate text-xs text-[var(--nc-tx-dim)]">{detailText(service.details)}</div>
        )}
      </div>
      <span className={`nc-chip shrink-0 ${statusClass(service.status)}`}>
        {service.status}
      </span>
    </div>
  );
}


function ErrorRow({ event }: { event: MonitoringEventSummary }) {
  return (
    <li className="border-b border-[var(--nc-border)] px-4 py-3 last:border-b-0">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`nc-chip ${statusClass(event.level === 'error' || event.level === 'critical' ? 'down' : 'unknown')}`}>
              {event.level}
            </span>
            <span className="font-medium text-[var(--nc-tx)]">{event.event_type}</span>
            <span className="text-xs text-[var(--nc-tx-faint)]">{event.source}</span>
          </div>
          <div className="mt-1 text-sm text-[var(--nc-tx-muted)]">{event.message}</div>
          <div className="mt-1 truncate text-xs text-[var(--nc-tx-dim)]">
            {[event.user_email, event.method, event.path, event.status_code ? `HTTP ${event.status_code}` : null].filter(Boolean).join(' | ')}
          </div>
        </div>
        <span className="shrink-0 text-xs text-[var(--nc-tx-faint)]">{formatTime(event.created_at)}</span>
      </div>
    </li>
  );
}


function ActivityRow({ event }: { event: MonitoringAuditEventSummary }) {
  return (
    <li className="border-b border-[var(--nc-border)] px-4 py-3 last:border-b-0">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="font-medium text-[var(--nc-tx)]">{event.action}</div>
          <div className="truncate text-xs text-[var(--nc-tx-dim)]">
            {[event.user_email ?? event.user_id, event.case_id, event.artifact_id].filter(Boolean).join(' | ')}
          </div>
        </div>
        <span className="shrink-0 text-xs text-[var(--nc-tx-faint)]">{formatTime(event.created_at)}</span>
      </div>
    </li>
  );
}


export function MonitoringPage() {
  const { session } = useAppSession();
  const [summary, setSummary] = useState<MonitoringSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (isRefresh = false) => {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      const data = await fetchMonitoringSummary();
      setSummary(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void load();
    const intervalId = window.setInterval(() => void load(true), 15000);
    return () => window.clearInterval(intervalId);
  }, []);

  const totals = summary?.totals ?? {};
  const activeRuns = totals.active_runs ?? 0;
  const activeWorkspaceRuns = totals.active_workspace_runs ?? 0;

  return (
    <div className="nc-app-page">
      <header className="border-b border-[var(--nc-border)] bg-[var(--nc-bg-panel)]">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold">Monitoring</h1>
              {summary && (
                <span className={`nc-chip ${statusClass(summary.status)}`}>
                  {summary.status}
                </span>
              )}
            </div>
            <p className="text-sm text-[var(--nc-tx-muted)]">App health, recent errors, users, jobs, and queue state.</p>
          </div>
          <div className="flex items-center gap-4">
            <Link className="nc-btn" to="/">
              Workspaces
            </Link>
            <button
              type="button"
              onClick={() => void load(true)}
              disabled={refreshing}
              className="nc-btn"
            >
              <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <div className="text-right text-sm">
              <div>{session?.user.full_name}</div>
            </div>
            <SessionActions />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-8">
        {loading && <div className="text-sm text-[var(--nc-tx-muted)]">Loading monitoring dashboard...</div>}
        {error && <div className="rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] p-4 text-sm text-[var(--nc-danger)]">{error}</div>}
        {summary && !error && (
          <div className="space-y-6">
            <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <div className="nc-card-static p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--nc-tx-muted)]">Users</span>
                  <Users className="h-4 w-4 text-[var(--nc-tx-dim)]" />
                </div>
                <div className="mt-2 text-2xl font-semibold">{formatNumber(totals.users)}</div>
                <div className="text-xs text-[var(--nc-tx-dim)]">{formatNumber(totals.recently_active_users)} recently active</div>
              </div>
              <div className="nc-card-static p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--nc-tx-muted)]">Cases</span>
                  <Database className="h-4 w-4 text-[var(--nc-tx-dim)]" />
                </div>
                <div className="mt-2 text-2xl font-semibold">{formatNumber(totals.cases)}</div>
                <div className="text-xs text-[var(--nc-tx-dim)]">{formatNumber(totals.artifacts)} artifacts</div>
              </div>
              <div className="nc-card-static p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--nc-tx-muted)]">Active Work</span>
                  <Activity className="h-4 w-4 text-[var(--nc-tx-dim)]" />
                </div>
                <div className="mt-2 text-2xl font-semibold">{formatNumber(activeRuns)}</div>
                <div className="text-xs text-[var(--nc-tx-dim)]">{formatNumber(activeWorkspaceRuns)} workspace runs</div>
              </div>
              <div className="nc-card-static p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-[var(--nc-tx-muted)]">Errors</span>
                  <AlertTriangle className="h-4 w-4 text-[var(--nc-tx-dim)]" />
                </div>
                <div className="mt-2 text-2xl font-semibold">{formatNumber(totals.errors_24h)}</div>
                <div className="text-xs text-[var(--nc-tx-dim)]">Last 24 hours</div>
              </div>
            </section>
            <section className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
              <div className="nc-card-static overflow-hidden">
                <div className="flex items-center gap-2 border-b border-[var(--nc-border)] px-4 py-3">
                  <Server className="h-4 w-4 text-[var(--nc-tx-dim)]" />
                  <h2 className="text-sm font-semibold">Service Health</h2>
                </div>
                {summary.services.map((service) => <ServiceRow key={service.name} service={service} />)}
              </div>
              <div className="nc-card-static overflow-hidden">
                <div className="flex items-center gap-2 border-b border-[var(--nc-border)] px-4 py-3">
                  <CheckCircle2 className="h-4 w-4 text-[var(--nc-tx-dim)]" />
                  <h2 className="text-sm font-semibold">Recently Active Users</h2>
                </div>
                {summary.active_users.length === 0 ? (
                  <div className="px-4 py-6 text-sm text-[var(--nc-tx-muted)]">No users in the last {summary.active_window_minutes} minutes.</div>
                ) : summary.active_users.map((user) => (
                  <div key={user.id} className="border-b border-[var(--nc-border)] px-4 py-3 last:border-b-0">
                    <div className="font-medium text-[var(--nc-tx)]">{user.full_name}</div>
                    <div className="text-xs text-[var(--nc-tx-dim)]">{user.email} | Last seen {formatTime(user.last_seen_at)}</div>
                  </div>
                ))}
              </div>
            </section>
            <section className="grid gap-6 xl:grid-cols-2">
              <div className="nc-card-static overflow-hidden">
                <div className="border-b border-[var(--nc-border)] px-4 py-3">
                  <h2 className="text-sm font-semibold">Recent Errors</h2>
                </div>
                {summary.recent_errors.length === 0 ? (
                  <div className="px-4 py-6 text-sm text-[var(--nc-tx-muted)]">No recent errors recorded.</div>
                ) : <ul>{summary.recent_errors.map((event) => <ErrorRow key={event.id} event={event} />)}</ul>}
              </div>
              <div className="nc-card-static overflow-hidden">
                <div className="border-b border-[var(--nc-border)] px-4 py-3">
                  <h2 className="text-sm font-semibold">Recent Activity</h2>
                </div>
                {summary.recent_activity.length === 0 ? (
                  <div className="px-4 py-6 text-sm text-[var(--nc-tx-muted)]">No recent audit activity recorded.</div>
                ) : <ul>{summary.recent_activity.map((event) => <ActivityRow key={event.id} event={event} />)}</ul>}
              </div>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
