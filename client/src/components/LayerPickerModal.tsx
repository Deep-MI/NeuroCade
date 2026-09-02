import { Layers, RefreshCw, X } from 'lucide-react';

import type { LayerType, OutputVolume } from '../types';
import { layerDisplayName } from '../utils/layerAliases';

const LABELS: Record<LayerType, string> = {
  intensity: 'Intensity Volume',
  segmentation: 'Segmentation Volume',
  drawing: 'Drawing Source',
  surface: 'Surface Mesh',
};

interface LayerPickerModalProps {
  type: LayerType;
  options: OutputVolume[];
  loadedFilenames: Set<string>;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onRefresh: () => void;
  onLoad: (option: OutputVolume) => void;
}

export function LayerPickerModal({ type, options, loadedFilenames, loading, error, onClose, onRefresh, onLoad }: LayerPickerModalProps) {
  return (
    <div className="fixed inset-0 z-[116] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative flex max-h-[82vh] w-full max-w-xl flex-col overflow-hidden rounded border border-[var(--nc-border)] bg-[var(--nc-bg-panel)] shadow-2xl">
        <div className="flex items-center gap-2 border-b border-[var(--nc-border)] px-4 py-3">
          <Layers size={14} className="text-[var(--nc-interactive)]" />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-[var(--nc-tx)]">Load {LABELS[type]}</h3>
            <div className="nc-mono text-[11px] text-[var(--nc-tx-dim)]">Select an existing file from this case directory.</div>
          </div>
          <button type="button" className="nc-btn nc-icon-btn" onClick={onRefresh} disabled={loading} title="Refresh case directory" aria-label="Refresh case directory">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <button type="button" className="nc-btn nc-icon-btn" onClick={onClose} title="Close" aria-label="Close layer picker"><X size={14} /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {error && <div className="mb-3 rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] px-3 py-2 text-sm text-[var(--nc-danger)]">{error}</div>}
          {loading && options.length === 0 ? (
            <div className="nc-mono py-8 text-center text-sm text-[var(--nc-tx-muted)]">Loading case directory...</div>
          ) : options.length > 0 ? (
            <div className="divide-y divide-[var(--nc-border)] border-y border-[var(--nc-border)]">
              {options.map((option) => {
                const loaded = loadedFilenames.has(option.filename);
                return (
                  <button key={`${option.type}:${option.filename}`} type="button" className="flex w-full items-center gap-3 px-2 py-2 text-left transition hover:bg-[var(--nc-row-hover)] disabled:cursor-default disabled:hover:bg-transparent" disabled={loaded} onClick={() => onLoad(option)} title={loaded ? `${option.filename} is already loaded` : `Load ${option.filename}`}>
                    <span className={`h-2 w-2 shrink-0 rounded-full ${type === 'surface' ? 'bg-[var(--nc-warning)]' : type === 'segmentation' ? 'bg-[var(--nc-success)]' : type === 'drawing' ? 'bg-[var(--nc-accent)]' : 'bg-[var(--nc-interactive)]'}`} />
                    <span className="min-w-0 flex-1">
                      <span className={`block truncate text-sm ${loaded ? 'text-[var(--nc-tx-faint)]' : 'text-[var(--nc-tx)]'}`}>{layerDisplayName(option)}</span>
                      <span className="nc-mono block truncate text-[11px] text-[var(--nc-tx-dim)]">{option.filename}</span>
                    </span>
                    <span className="nc-mono shrink-0 text-[11px] text-[var(--nc-tx-faint)]">{loaded ? 'Loaded' : 'Load'}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="rounded border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] px-3 py-8 text-center">
              <div className="text-sm text-[var(--nc-tx-muted)]">No matching files found in this case directory.</div>
              <div className="nc-mono mt-1 text-[11px] text-[var(--nc-tx-dim)]">New images can be added via Upload.</div>
            </div>
          )}
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-[var(--nc-border)] px-4 py-3">
          <div className="nc-mono text-[11px] text-[var(--nc-tx-dim)]">New images can be added via Upload.</div>
          <button type="button" className="nc-btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
