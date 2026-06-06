import { useMemo, useState } from 'react';
import { Download, FolderDown, X } from 'lucide-react';

import type { ArtifactListItem } from '../types';

interface DownloadCaseModalProps {
  isOpen: boolean;
  caseTitle: string | null;
  artifacts: ArtifactListItem[];
  loadingArtifacts: boolean;
  error: string | null;
  actionLoading: 'volume' | 'case' | null;
  onClose: () => void;
  onDownloadVolume: (artifactId: string) => Promise<void> | void;
  onDownloadCase: () => Promise<void> | void;
}

function artifactLabel(artifact: ArtifactListItem): string {
  const volumeRole = typeof artifact.metadata?.volume_role === 'string' ? artifact.metadata.volume_role : null;
  if (volumeRole === 'segmentation') {
    return `${artifact.name} (segmentation)`;
  }
  if (volumeRole === 'intensity') {
    return `${artifact.name} (intensity)`;
  }
  return artifact.name;
}

export function DownloadCaseModal({
  isOpen,
  caseTitle,
  artifacts,
  loadingArtifacts,
  error,
  actionLoading,
  onClose,
  onDownloadVolume,
  onDownloadCase,
}: DownloadCaseModalProps) {
  const [selectedArtifactId, setSelectedArtifactId] = useState<string>('');
  const downloadableArtifacts = useMemo(
    () => artifacts.filter((artifact) => artifact.kind === 'volume'),
    [artifacts],
  );
  const selectedDownloadArtifactId = downloadableArtifacts.some((artifact) => artifact.id === selectedArtifactId)
    ? selectedArtifactId
    : downloadableArtifacts[0]?.id ?? '';

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[115] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={actionLoading === null ? onClose : undefined} />
      <div className="relative w-full max-w-lg rounded-2xl border border-white/10 bg-slate-900/85 p-6 shadow-[0_0_50px_rgba(0,0,0,0.45)]">
        <button
          type="button"
          onClick={onClose}
          disabled={actionLoading !== null}
          className="absolute right-4 top-4 p-1 text-slate-400 transition-colors hover:text-white disabled:opacity-50"
        >
          <X size={18} />
        </button>

        <div className="mb-5 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-full border border-sky-400/20 bg-sky-400/10 text-sky-300">
            <Download size={20} />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-white">Download Case Data</h3>
            <p className="text-sm text-slate-400">
              Download one volume from {caseTitle ?? 'the current case'} or export the whole case folder as a zip archive.
            </p>
          </div>
        </div>

        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-slate-500">
          Download Volume
        </label>
        <select
          data-testid="download-volume-select"
          value={selectedDownloadArtifactId}
          onChange={(event) => setSelectedArtifactId(event.target.value)}
          disabled={downloadableArtifacts.length === 0 || actionLoading !== null}
          className="mb-4 w-full rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-sm text-white outline-none transition-colors focus:border-primary/50 disabled:opacity-50"
        >
          {downloadableArtifacts.length === 0 ? (
            <option value="">No downloadable volumes available</option>
          ) : (
            downloadableArtifacts.map((artifact) => (
              <option key={artifact.id} value={artifact.id}>
                {artifactLabel(artifact)}
              </option>
            ))
          )}
        </select>

        {loadingArtifacts && (
          <div className="mb-4 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-300">
            Loading full artifact list…
          </div>
        )}

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        )}

        <div className="flex gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={actionLoading !== null}
            className="flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-3 font-semibold text-slate-300 transition-colors hover:bg-white/10 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="download-volume-button"
            onClick={() => void onDownloadVolume(selectedDownloadArtifactId)}
            disabled={actionLoading !== null || !selectedDownloadArtifactId}
            className="flex-1 rounded-xl border border-sky-400/30 bg-sky-400/10 px-4 py-3 font-semibold text-sky-100 transition-colors hover:bg-sky-400/20 disabled:opacity-50"
          >
            {actionLoading === 'volume' ? 'Preparing…' : 'Download Volume'}
          </button>
          <button
            type="button"
            data-testid="download-case-archive-button"
            onClick={() => void onDownloadCase()}
            disabled={actionLoading !== null}
            className="flex-1 rounded-xl bg-sky-500 px-4 py-3 font-semibold text-white transition-colors hover:bg-sky-600 disabled:opacity-50"
          >
            <span className="inline-flex items-center gap-2">
              <FolderDown size={16} />
              {actionLoading === 'case' ? 'Preparing…' : 'Whole Folder'}
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
