import { Check, Download, Loader2, Pencil, Trash2, X, XCircle } from 'lucide-react';

import { STATUS_CONFIG, isRunActive } from '../constants';
import type { CaseSummary } from '../types';

function statusChipClass(status: string) {
  if (isRunActive(status)) return 'nc-chip-yellow';
  if (status === 'completed' || status === 'finished') return 'nc-chip-green';
  if (status === 'failed' || status === 'error') return 'nc-chip-red';
  return 'nc-chip-blue';
}

interface CaseCardProps {
  caseItem: CaseSummary;
  viewMode: 'cards' | 'list';
  isEditing: boolean;
  isDeleting: boolean;
  isActionLoading: boolean;
  actionError: string | null;
  editValue: string;
  destructiveActionsEnabled: boolean;
  onOpen: (caseItem: CaseSummary) => void;
  onEditValueChange: (value: string) => void;
  onStartEditing: (caseItem: CaseSummary) => void;
  onCancelEditing: () => void;
  onRename: (caseItem: CaseSummary) => void;
  onStartDeleting: (caseId: string) => void;
  onCancelDeleting: () => void;
  onDelete: (caseItem: CaseSummary) => void;
  onDownload: (caseItem: CaseSummary) => void;
}

export function CaseCard({
  caseItem,
  viewMode,
  isEditing,
  isDeleting,
  isActionLoading,
  actionError,
  editValue,
  destructiveActionsEnabled,
  onOpen,
  onEditValueChange,
  onStartEditing,
  onCancelEditing,
  onRename,
  onStartDeleting,
  onCancelDeleting,
  onDelete,
  onDownload,
}: CaseCardProps) {
  const runStatus = caseItem.latest_run_status ?? 'uploaded';
  const status = STATUS_CONFIG[runStatus] ?? STATUS_CONFIG.unknown;
  const isLocked = isRunActive(runStatus);
  const cardIsList = viewMode === 'list';

  return (
    <article
      onClick={isEditing || isDeleting ? undefined : () => onOpen(caseItem)}
      className={`group nc-card relative overflow-hidden ${cardIsList ? 'flex min-h-[68px] items-center gap-4 p-2.5' : 'flex min-h-[160px] flex-col p-3'} ${isEditing || isDeleting ? '' : 'cursor-pointer'}`}
    >
      {isEditing ? (
        <div className="w-full">
          <div className="mb-3 flex items-start gap-2">
            <input
              data-testid={`workspace-case-rename-input-${caseItem.id}`}
              type="text"
              value={editValue}
              onChange={(event) => onEditValueChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onRename(caseItem);
                if (event.key === 'Escape') onCancelEditing();
              }}
              autoFocus
              className="nc-form-control min-w-0 flex-1 rounded border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] px-3 py-2 outline-none"
            />
            <button type="button" data-testid={`workspace-case-rename-confirm-${caseItem.id}`} onClick={() => onRename(caseItem)} disabled={isActionLoading} className="nc-btn nc-btn-active !h-9 !w-9 !p-0">
              {isActionLoading ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            </button>
            <button type="button" data-testid={`workspace-case-rename-cancel-${caseItem.id}`} onClick={onCancelEditing} disabled={isActionLoading} className="nc-btn !h-9 !w-9 !p-0">
              <X size={14} />
            </button>
          </div>
          <span className={`nc-chip ${statusChipClass(runStatus)}`}>{status.label}</span>
        </div>
      ) : isDeleting ? (
        <div className="w-full">
          <div className="mb-3 flex items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold text-[var(--nc-tx)]">Delete "{caseItem.title}"?</h3>
              <p className="nc-helper-text mt-1">This removes the case from the workspace.</p>
            </div>
            <span className={`nc-chip ${statusChipClass(runStatus)}`}>{status.label}</span>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" data-testid={`workspace-case-delete-confirm-${caseItem.id}`} onClick={() => onDelete(caseItem)} disabled={isActionLoading} className="nc-btn nc-chip-red">
              {isActionLoading ? 'Deleting...' : 'Delete'}
            </button>
            <button type="button" data-testid={`workspace-case-delete-cancel-${caseItem.id}`} onClick={onCancelDeleting} disabled={isActionLoading} className="nc-btn">Cancel</button>
          </div>
        </div>
      ) : (
        <>
          <div className={cardIsList ? 'flex min-w-0 flex-1 items-center gap-4' : 'flex h-full min-w-0 flex-col'}>
            <div className="min-w-0 flex-1">
              <div className={cardIsList ? '' : 'flex items-start justify-between gap-2'}>
                <button type="button" onClick={(event) => { event.stopPropagation(); onOpen(caseItem); }} className="block min-w-0 cursor-pointer text-left">
                  <h3 data-testid={`workspace-case-title-${caseItem.id}`} className="nc-card-title truncate">
                    {caseItem.title}
                  </h3>
                </button>
                {!cardIsList && <span className={`nc-chip shrink-0 ${statusChipClass(runStatus)}`}>{status.label}</span>}
              </div>
              <p className="nc-case-secondary-text mt-1 line-clamp-2 text-[var(--nc-tx-muted)]">
                {caseItem.description ?? `Case ID: ${caseItem.id}`}
              </p>
            </div>
            <div className={`${cardIsList ? 'flex-1' : 'mt-3'} flex min-w-0 flex-wrap items-center gap-1.5`}>
              {(caseItem.modalities ?? []).map((modality) => <span key={modality} className="nc-chip nc-chip-blue">{modality}</span>)}
              {(caseItem.tags ?? []).map((tag) => <span key={tag} className="nc-chip">{tag}</span>)}
            </div>
            <div className={`${cardIsList ? '' : 'mt-auto pr-28 pt-3'} flex items-center gap-2`}>
              {cardIsList && <span className={`nc-chip ${statusChipClass(runStatus)}`}>{status.label}</span>}
              <span className="nc-case-secondary-text nc-mono text-[var(--nc-tx-faint)]">
                {new Date(caseItem.created_at).toLocaleDateString()}
              </span>
              <span className={`${cardIsList ? '' : 'hidden'} nc-case-secondary-text nc-mono text-[var(--nc-tx-faint)]`}>
                {caseItem.artifact_count ?? 0} artifacts
              </span>
            </div>
          </div>
          <div className={cardIsList ? 'ml-auto flex shrink-0 items-center gap-1' : 'absolute bottom-2 right-2 flex shrink-0 items-center gap-1 opacity-0 transition group-hover:opacity-100 group-focus-within:opacity-100'}>
            <button type="button" onClick={(event) => { event.stopPropagation(); onStartEditing(caseItem); }} disabled={isLocked || !destructiveActionsEnabled} className="nc-btn !h-8 !w-8 !p-0" title={!destructiveActionsEnabled ? 'Disabled for this deployment' : isLocked ? 'Cannot rename while running' : 'Rename case'} data-testid={`workspace-case-rename-${caseItem.id}`}>
              <Pencil size={14} />
            </button>
            <button type="button" onClick={(event) => { event.stopPropagation(); onDownload(caseItem); }} className="nc-btn !h-8 !w-8 !p-0" title="Download case">
              <Download size={14} />
            </button>
            <button type="button" onClick={(event) => { event.stopPropagation(); onStartDeleting(caseItem.id); }} disabled={isLocked || !destructiveActionsEnabled} className="nc-btn !h-8 !w-8 !p-0" title={!destructiveActionsEnabled ? 'Disabled for this deployment' : isLocked ? 'Cannot delete while running' : 'Delete case'} data-testid={`workspace-case-delete-${caseItem.id}`}>
              <Trash2 size={14} />
            </button>
          </div>
        </>
      )}
      {actionError && (
        <div className="nc-helper-text mt-3 flex items-start gap-2 rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] px-3 py-2 !text-[var(--nc-danger)]">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{actionError}</span>
        </div>
      )}
    </article>
  );
}
