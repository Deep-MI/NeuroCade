import { useEffect, useState } from 'react';
import { appJson } from '../utils/api/core';

interface Provenance {
  state: string;
  provenance: Record<string, string>;
  series: { series_uid: string; state: string; description: string }[];
}

export function PacsProvenance({ caseId }: { caseId: string }) {
  const [data, setData] = useState<Provenance | null>(null);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await appJson<Provenance | null>(`/pacs/cases/${encodeURIComponent(caseId)}`, 'Provenance unavailable');
        if (!disposed) {
          setData(result);
          if (result && ['queued', 'running', 'canceling'].includes(result.state)) timer = setTimeout(() => { void poll(); }, 2000);
        }
      } catch { /* Non-PACS cases do not need this panel. */ }
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [caseId]);
  if (!data) return null;
  return <details className="border-b border-[var(--nc-border)] px-4 py-2 text-sm">
    <summary>Research copy · PACS import {data.state.replaceAll('_', ' ')}</summary>
    <dl className="grid max-h-40 grid-cols-2 gap-1 overflow-auto">
      {Object.entries(data.provenance).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{value || 'Not supplied'}</dd></div>)}
    </dl>
    {data.series.map(series => <p key={series.series_uid}>{series.description}: {series.state}</p>)}
  </details>;
}
