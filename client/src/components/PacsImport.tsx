import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { appFetch, appJson, expectOk, jsonRequest } from '../utils/api/core';
import { pacsErrorMessage } from '../utils/errorMessages';
import { duplicateStudyCases } from '../utils/pacsConflict';

interface Study { study_uid: string; patient_name: string; patient_id: string; birth_date: string; study_date: string; description: string }
interface Series { series_uid: string; description: string; modality?: string; eligible: boolean; reason: string; state: string; error_code?: string }
interface Import { id: string; case_id: string; state: string; series: Series[]; error_code?: string }

export function PacsImport({ workspaceId }: { workspaceId: string }) {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [patientId, setPatientId] = useState('');
  const [accession, setAccession] = useState('');
  const [patientName, setPatientName] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [modality, setModality] = useState('');
  const [offset, setOffset] = useState(0);
  const [studies, setStudies] = useState<Study[]>([]);
  const [study, setStudy] = useState<Study | null>(null);
  const [series, setSeries] = useState<Series[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [verifiedT1, setVerifiedT1] = useState<string[]>([]);
  const [imports, setImports] = useState<Import[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [duplicates, setDuplicates] = useState<string[]>([]);
  const key = useRef(crypto.randomUUID());
  const dialog = useRef<HTMLDialogElement>(null);
  const active = (state: string) => ['queued', 'running', 'canceling'].includes(state);
  useEffect(() => {
    let disposed = false;
    void appJson<{ enabled: boolean }>(`/pacs/status?workspace_id=${encodeURIComponent(workspaceId)}`, 'PACS unavailable')
      .then(data => { if (!disposed) setEnabled(data.enabled); }).catch(() => { if (!disposed) setEnabled(false); });
    return () => { disposed = true; };
  }, [workspaceId]);
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const rows = await appJson<Import[]>(`/pacs/imports?workspace_id=${encodeURIComponent(workspaceId)}`, 'Unable to refresh imports');
        if (!disposed) { setImports(rows); timer = setTimeout(() => { void poll(); }, rows.some(row => active(row.state)) ? 2000 : 10000); }
      } catch { if (!disposed) setError('Unable to refresh imports. Reopen to reconnect.'); }
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [open, workspaceId]);
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close(); }, [open]);
  const perform = async (work: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await work(); } catch (err) { setError(err instanceof Error && !/^[a-z]+(?:_[a-z]+)+$/.test(err.message) ? err.message : pacsErrorMessage(err instanceof Error ? err.message : '')); }
    finally { setBusy(false); }
  };
  const submit = (confirm = false) => perform(async () => {
    const response = await appFetch('/pacs/imports', jsonRequest({ workspace_id: workspaceId, study_uid: study?.study_uid,
      series_uids: selected, verified_t1_series_uids: verifiedT1.filter(id => selected.includes(id)), submission_key: key.current, confirm_duplicate: confirm }, { method: 'POST' }));
    if (response.status === 409) {
      const duplicates = duplicateStudyCases(await response.clone().json());
      if (duplicates) { setDuplicates(duplicates); return; }
    }
    await expectOk(response, 'Import failed');
    const row = await response.json() as Import;
    setImports(current => [row, ...current.filter(item => item.id !== row.id)]); setStudy(null); setDuplicates([]);
  });
  if (!enabled) return null;
  return <>
    <button type="button" className="nc-btn" onClick={() => setOpen(true)}>Import from PACS</button>
    <dialog ref={dialog} onCancel={() => setOpen(false)} className="m-auto max-h-[85vh] w-[min(900px,95vw)] overflow-auto rounded-lg border border-[var(--nc-border)] bg-[var(--nc-bg-surface)] p-6 text-[var(--nc-tx)] backdrop:bg-black/60" aria-labelledby="pacs-title">
      <div className="flex justify-between"><h2 id="pacs-title">Import from PACS</h2><button className="nc-btn" onClick={() => setOpen(false)}>Close</button></div>
      <p className="my-3">Create a research copy. PACS remains unchanged. Source DICOM is removed after conversion.</p>
      {error && <p role="alert">{error}</p>}
      {!study ? <form className="flex flex-wrap gap-3" onSubmit={event => { event.preventDefault(); void perform(async () => {
        setStudies(await appJson<Study[]>('/pacs/studies/search', 'Search failed', jsonRequest({ workspace_id: workspaceId, patient_id: patientId, accession, patient_name: patientName, date_from: dateFrom || null, date_to: dateTo || null, modality, offset }, { method: 'POST' })));
      }); }}>
        <label>Exact patient ID <input value={patientId} onChange={e => { setPatientId(e.target.value); setOffset(0); }} /></label>
        <label>Accession <input value={accession} onChange={e => { setAccession(e.target.value); setOffset(0); }} /></label>
        <label>Patient name <input value={patientName} onChange={e => { setPatientName(e.target.value); setOffset(0); }} /></label>
        <label>From <input type="date" value={dateFrom} onChange={e => { setDateFrom(e.target.value); setOffset(0); }} /></label>
        <label>To <input type="date" value={dateTo} onChange={e => { setDateTo(e.target.value); setOffset(0); }} /></label>
        <label>Modality <input value={modality} onChange={e => { setModality(e.target.value.toUpperCase()); setOffset(0); }} /></label>
        <button className="nc-btn" disabled={busy}>Search</button>
        <label>Result offset <input type="number" min="0" max="10000" step="50" value={offset} onChange={e => setOffset(Number(e.target.value))} /></label>
      </form> : <>
        <button className="nc-btn" onClick={() => setStudy(null)}>Back</button>
        <p>{study.patient_name} · {study.patient_id} · {study.birth_date} · {study.study_date}</p>
        {series.map(row => <div key={row.series_uid}><label className="my-2 block"><input type="checkbox" disabled={!row.eligible || busy} checked={selected.includes(row.series_uid)} onChange={e => {
          setSelected(current => e.target.checked ? [...current, row.series_uid] : current.filter(id => id !== row.series_uid)); key.current = crypto.randomUUID(); setDuplicates([]);
        }} /> {row.description} · {row.eligible ? 'Available for conversion' : row.reason}</label>
          {row.eligible && row.modality === 'MR' && selected.includes(row.series_uid) && <label className="ml-6 block text-sm"><input type="checkbox" disabled={busy} checked={verifiedT1.includes(row.series_uid)} onChange={event => {
            setVerifiedT1(current => event.target.checked ? [...current, row.series_uid] : current.filter(id => id !== row.series_uid)); key.current = crypto.randomUUID(); setDuplicates([]);
          }} /> I have reviewed this series and verified it is native, noncontrast structural T1 suitable for T1 analysis.</label>}
        </div>)}
        <p className="my-2 text-sm">Leave verification unchecked to import for viewing only. Series names alone do not establish analysis suitability.</p>
        {duplicates.length > 0 && <div role="alert"><p>This study was already imported.</p>{duplicates.map(id => <Link key={id} className="block underline" to={`/workspaces/${workspaceId}/cases/${id}`}>Open existing case</Link>)}<button className="nc-btn" disabled={busy} onClick={() => { void submit(true); }}>Confirm separate copy</button></div>}
        <button className="nc-btn" disabled={busy || !selected.length} onClick={() => { void submit(); }}>Import {selected.length} series as one case</button>
      </>}
      {!study && studies.map(row => <button className="my-2 block rounded border p-3" key={row.study_uid} disabled={busy} onClick={() => { void perform(async () => {
        const rows = await appJson<Series[]>(`/pacs/studies/${encodeURIComponent(row.study_uid)}/series?workspace_id=${encodeURIComponent(workspaceId)}`, 'Unable to list series');
        setStudy(row); setSeries(rows); setSelected(rows.filter(item => item.eligible).map(item => item.series_uid)); setVerifiedT1([]); key.current = crypto.randomUUID(); setDuplicates([]);
      }); }}>{row.patient_name} · {row.patient_id} · {row.study_date} · {row.description}</button>)}
      <h3 className="mt-6">Recent imports</h3>
      {imports.map(row => <div key={row.id} className="my-3 rounded border p-3"><Link className="underline" to={`/workspaces/${workspaceId}/cases/${row.case_id}`}>Open research copy</Link> · {row.state.replaceAll('_', ' ')}
        {row.error_code && <p role="alert">{pacsErrorMessage(row.error_code)}</p>}
        {row.series.map(item => <p key={item.series_uid}>{item.description}: {item.state} {item.error_code && pacsErrorMessage(item.error_code)}</p>)}
        {row.state !== 'completed' && <button className="nc-btn" disabled={busy || row.state === 'canceling'} onClick={() => { void perform(async () => {
          const next = await appJson<Import>(`/pacs/imports/${row.id}/${active(row.state) ? 'cancel' : 'retry'}`, 'Unable to update import', { method: 'POST' });
          setImports(current => current.map(item => item.id === next.id ? next : item));
        }); }}>{active(row.state) ? 'Cancel import' : 'Retry unfinished series'}</button>}
      </div>)}
    </dialog>
  </>;
}
