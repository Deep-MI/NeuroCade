/* ------------------------------------------------------------------ */
/*  Application-wide constants                                         */
/* ------------------------------------------------------------------ */
import type { StatusConfig } from './types.js';

export const APP_DISPLAY_NAME = 'NeuroCade';

/** Status badge styling keyed by analysis status string. */
export const STATUS_CONFIG: Record<string, StatusConfig> = {
  uploaded:  { label: 'Uploaded',  color: 'text-purple-500', badge: 'bg-purple-500/20 text-purple-500' },
  queued:    { label: 'Queued',    color: 'text-yellow-500', badge: 'bg-yellow-500/20 text-yellow-500' },
  starting:  { label: 'Starting',  color: 'text-blue-500',   badge: 'bg-blue-500/20 text-blue-500' },
  running:   { label: 'Running',   color: 'text-blue-500',   badge: 'bg-blue-500/20 text-blue-500' },
  finished:  { label: 'Finished',  color: 'text-green-500',  badge: 'bg-green-500/20 text-green-500' },
  completed: { label: 'Finished',  color: 'text-green-500',  badge: 'bg-green-500/20 text-green-500' },
  failed:    { label: 'Failed',    color: 'text-red-500',    badge: 'bg-red-500/20 text-red-500' },
  error:     { label: 'Error',     color: 'text-red-500',    badge: 'bg-red-500/20 text-red-500' },
  canceled:  { label: 'Canceled',  color: 'text-slate-500',  badge: 'bg-slate-500/20 text-slate-500' },
  unknown:   { label: 'Unknown',   color: 'text-slate-400',  badge: 'bg-slate-500/10 text-slate-400' },
};

export const DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD = 0.35;
export const DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD = 0.25;

/* ------------------------------------------------------------------ */
/*  Derived-status helpers (avoids repeated array literals in JSX)     */
/* ------------------------------------------------------------------ */

const ACTIVE_STATUSES = new Set(['queued', 'starting', 'running']);
const DONE_STATUSES   = new Set(['finished', 'completed']);
const FAILED_STATUSES = new Set(['error', 'failed', 'canceled']);
const TERMINAL_STATUSES = new Set(['finished', 'completed', 'error', 'failed', 'canceled']);

export const isRunActive   = (s: string): boolean => ACTIVE_STATUSES.has(s);
export const isRunDone     = (s: string): boolean => DONE_STATUSES.has(s);
export const isRunFailed   = (s: string): boolean => FAILED_STATUSES.has(s);
export const isRunTerminal = (s: string): boolean => TERMINAL_STATUSES.has(s);
