import { Folder, Trash2, X } from 'lucide-react';

import type { WorkspaceSummary } from '../types';

interface WorkspaceDialogsProps {
  workspaceDeleteTarget: WorkspaceSummary | null;
  workspaceDeleteCaseCount: number;
  workspaceDeleteNeedsExtraConfirm: boolean;
  deletingWorkspace: boolean;
  workspaceDeleteError: string | null;
  confirmNonEmptyWorkspaceDelete: boolean;
  creatingWorkspace: boolean;
  editingWorkspaceId: string | null;
  workspaceName: string;
  workspaceDescription: string;
  workspaceError: string | null;
  onCancelDeleteWorkspace: () => void;
  onConfirmDeleteWorkspace: () => void;
  onConfirmNonEmptyWorkspaceDeleteChange: (value: boolean) => void;
  onCloseWorkspaceForm: () => void;
  onWorkspaceNameChange: (value: string) => void;
  onWorkspaceDescriptionChange: (value: string) => void;
  onSubmitWorkspace: () => void;
}

export function WorkspaceDialogs({
  workspaceDeleteTarget,
  workspaceDeleteCaseCount,
  workspaceDeleteNeedsExtraConfirm,
  deletingWorkspace,
  workspaceDeleteError,
  confirmNonEmptyWorkspaceDelete,
  creatingWorkspace,
  editingWorkspaceId,
  workspaceName,
  workspaceDescription,
  workspaceError,
  onCancelDeleteWorkspace,
  onConfirmDeleteWorkspace,
  onConfirmNonEmptyWorkspaceDeleteChange,
  onCloseWorkspaceForm,
  onWorkspaceNameChange,
  onWorkspaceDescriptionChange,
  onSubmitWorkspace,
}: WorkspaceDialogsProps) {
  return (
    <>
      {workspaceDeleteTarget && (
        <div className="fixed inset-0 z-[125] flex items-center justify-center bg-black/70 p-4" onClick={() => { if (!deletingWorkspace) onCancelDeleteWorkspace(); }}>
          <div className="w-full max-w-sm rounded-lg border border-[var(--nc-border)] bg-[var(--nc-bg-surface)] shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center border-b border-[var(--nc-border)] px-4 py-3">
              <Trash2 size={14} className="mr-2 text-[var(--nc-danger)]" />
              <h2 className="flex-1 text-[var(--nc-fs-sm)] font-semibold text-[var(--nc-tx)]">Delete Workspace</h2>
              <button type="button" onClick={onCancelDeleteWorkspace} disabled={deletingWorkspace} className="nc-btn !h-7 !w-7 !p-0"><X size={14} /></button>
            </div>
            <div className="space-y-3 p-4">
              <p className="nc-helper-text text-[var(--nc-tx-muted)]">
                Delete "{workspaceDeleteTarget.name}" from the workspace list?
              </p>
              {workspaceDeleteNeedsExtraConfirm && (
                <label className="flex items-start gap-2 rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] p-3 text-[var(--nc-tx-muted)]">
                  <input
                    type="checkbox"
                    checked={confirmNonEmptyWorkspaceDelete}
                    onChange={(event) => onConfirmNonEmptyWorkspaceDeleteChange(event.target.checked)}
                    disabled={deletingWorkspace}
                    className="mt-0.5"
                  />
                  <span className="nc-helper-text !text-[var(--nc-danger)]">
                    This workspace still contains {workspaceDeleteCaseCount} {workspaceDeleteCaseCount === 1 ? 'case' : 'cases'}. Delete the workspace anyway.
                  </span>
                </label>
              )}
              {workspaceDeleteError && <div className="nc-helper-text !text-[var(--nc-danger)]">{workspaceDeleteError}</div>}
              <div className="flex justify-end gap-2">
                <button type="button" className="nc-btn" onClick={onCancelDeleteWorkspace} disabled={deletingWorkspace}>Cancel</button>
                <button type="button" className="nc-btn nc-chip-red" onClick={onConfirmDeleteWorkspace} disabled={deletingWorkspace || (workspaceDeleteNeedsExtraConfirm && !confirmNonEmptyWorkspaceDelete)}>
                  {deletingWorkspace ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {creatingWorkspace && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 p-4" onClick={onCloseWorkspaceForm}>
          <div className="w-full max-w-md rounded-lg border border-[var(--nc-border)] bg-[var(--nc-bg-surface)] shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center border-b border-[var(--nc-border)] px-4 py-3">
              <Folder size={14} className="mr-2 text-[var(--nc-interactive)]" />
              <h2 className="flex-1 text-[var(--nc-fs-sm)] font-semibold text-[var(--nc-tx)]">{editingWorkspaceId ? 'Edit Workspace' : 'New Workspace'}</h2>
              <button type="button" onClick={onCloseWorkspaceForm} className="nc-btn !h-7 !w-7 !p-0"><X size={14} /></button>
            </div>
            <div className="space-y-3 p-4">
              <label className="block">
                <span className="nc-mono mb-1 block text-[var(--nc-fs-2xs)] uppercase tracking-[1px] text-[var(--nc-tx-dim)]">Name</span>
                <input value={workspaceName} onChange={(event) => onWorkspaceNameChange(event.target.value)} className="nc-form-control w-full rounded border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] px-3 py-2 outline-none" />
              </label>
              <label className="block">
                <span className="nc-mono mb-1 block text-[var(--nc-fs-2xs)] uppercase tracking-[1px] text-[var(--nc-tx-dim)]">Description</span>
                <input value={workspaceDescription} onChange={(event) => onWorkspaceDescriptionChange(event.target.value)} className="nc-form-control w-full rounded border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] px-3 py-2 outline-none" />
              </label>
              {workspaceError && <div className="nc-helper-text !text-[var(--nc-danger)]">{workspaceError}</div>}
              <div className="flex justify-end gap-2">
                <button type="button" className="nc-btn" onClick={onCloseWorkspaceForm}>Cancel</button>
                <button type="button" className="nc-btn nc-btn-active" onClick={onSubmitWorkspace}>{editingWorkspaceId ? 'Save Workspace' : 'Create Workspace'}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
