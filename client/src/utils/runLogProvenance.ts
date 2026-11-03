export interface RunLogSource {
  id: string;
  tool_id: string | null;
  status: string;
  created_at: string | null;
}

function describeWhen(createdAt: string | null): string | null {
  if (!createdAt) return null;
  const started = new Date(createdAt);
  if (Number.isNaN(started.getTime())) return null;
  return started.toLocaleString();
}

/**
 * Caption the terminal with the run whose output it is showing.
 *
 * The panel fills itself from the newest run whenever a case is opened, so
 * without this the reader has no way to tell live output from a transcript
 * left behind by a run that ended long ago.
 */
export function describeRunLogSource(source: RunLogSource | null): string | null {
  if (!source) return null;
  const parts = [source.tool_id ?? 'workflow', source.status];
  const when = describeWhen(source.created_at);
  if (when) parts.push(when);
  return parts.join(' · ');
}
