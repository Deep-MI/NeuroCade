import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  FilePlus2,
  Grid2X2,
  List,
  LoaderCircle,
  MessageSquare,
  Moon,
  Pencil,
  Search,
  Sun,
  Upload,
} from 'lucide-react';

import { SessionActions } from '../auth/AppSession';
import { useAppSession } from '../auth/sessionContext';
import { CaseCard } from '../components/CaseCard';
import { Chat } from '../components/Chat';
import { UploadCaseModal } from '../components/UploadCaseModal';
import { WorkspaceDialogs } from '../components/WorkspaceDialogs';
import { WorkspaceSidebar } from '../components/WorkspaceSidebar';
import { isRunActive } from '../constants';
import { useCaseUploadModal } from '../hooks/useCaseUploadModal';
import { useHorizontalPaneResize } from '../hooks/useHorizontalPaneResize';
import { useWorkspaceCaseListData } from '../hooks/useWorkspaceCaseListData';
import type { CaseSummary } from '../types';
import {
  cancelWorkspaceBatchRun,
  createCaseWithUpload,
  createWorkspace,
  deleteCase,
  deleteWorkspace,
  downloadCaseArchive,
  renameCase,
  renameWorkspace,
} from '../utils/api';
import { removeCaseState } from '../utils/caseStorage';
import { caseSummaryPath, caseViewerPath, workspaceCasesPath } from '../utils/caseRoutes';
import { getCaseNameValidationError, getSlugNameValidationError } from '../utils/caseNames';
import { createGuiSessionId, defaultPaneWidth } from '../utils/guiSession';

export function CaseListPage() {
  const navigate = useNavigate();
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const { session, refresh } = useAppSession();
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [editingCaseId, setEditingCaseId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [deletingCaseId, setDeletingCaseId] = useState<string | null>(null);
  const [caseActionLoadingId, setCaseActionLoadingId] = useState<string | null>(null);
  const [caseActionError, setCaseActionError] = useState<{ caseId: string; message: string } | null>(null);
  const [cancelingRunId, setCancelingRunId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [viewMode, setViewMode] = useState<'cards' | 'list'>('cards');
  const [chatOpen, setChatOpen] = useState(true);
  const [isLight, setIsLight] = useState(false);
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [workspaceName, setWorkspaceName] = useState('');
  const [workspaceDescription, setWorkspaceDescription] = useState('');
  const [editingWorkspaceId, setEditingWorkspaceId] = useState<string | null>(null);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceMenuOpenId, setWorkspaceMenuOpenId] = useState<string | null>(null);
  const [workspaceDeleteTargetId, setWorkspaceDeleteTargetId] = useState<string | null>(null);
  const [deletingWorkspace, setDeletingWorkspace] = useState(false);
  const [workspaceDeleteError, setWorkspaceDeleteError] = useState<string | null>(null);
  const [confirmNonEmptyWorkspaceDelete, setConfirmNonEmptyWorkspaceDelete] = useState(false);
  const [workspacePaneWidth, startWorkspaceResize] = useHorizontalPaneResize(defaultPaneWidth(220, 280), { minWidth: 180, maxWidth: 480, edge: 'right' });
  const [chatPaneWidth, startChatResize] = useHorizontalPaneResize(defaultPaneWidth(300, 380), { minWidth: 260, maxWidth: 620, edge: 'left' });
  const guiSessionIdRef = useRef<string>(createGuiSessionId());
  const {
    cases,
    setCases,
    workspaceCaseCounts,
    setWorkspaceCaseCounts,
    loading,
    error,
    setError,
    workspaceBatchRuns,
    setWorkspaceBatchRuns,
    workspaceChatNotifications,
    workspaceChatClearRequestToken,
    requestWorkspaceChatClear,
    isWorkspaceChatClearing,
    setIsWorkspaceChatClearing,
  } = useWorkspaceCaseListData(workspaceId, session?.workspaces);
  const currentWorkspace = session?.workspaces.find((workspace) => workspace.id === workspaceId) ?? null;
  const workspaceDeleteTarget = session?.workspaces.find((workspace) => workspace.id === workspaceDeleteTargetId) ?? null;
  const activeWorkspaceRuns = workspaceBatchRuns.filter((run) => isRunActive(run.status));
  const uploadsEnabled = session?.features.uploads !== false;
  const destructiveActionsEnabled = session?.features.destructive_actions !== false;

  useEffect(() => {
    if (!workspaceMenuOpenId) return;
    const closeMenu = () => setWorkspaceMenuOpenId(null);
    window.addEventListener('click', closeMenu);
    return () => window.removeEventListener('click', closeMenu);
  }, [workspaceMenuOpenId]);

  const filteredCases = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return cases;
    return cases.filter((caseItem) => (
      caseItem.subject_name.toLowerCase().includes(query)
      || caseItem.case_id.toLowerCase().includes(query)
      || (caseItem.description ?? '').toLowerCase().includes(query)
      || (caseItem.modalities ?? []).join(' ').toLowerCase().includes(query)
      || (caseItem.tags ?? []).join(' ').toLowerCase().includes(query)
    ));
  }, [cases, search]);

  useEffect(() => {
    setEditingCaseId(null);
    setEditValue('');
    setDeletingCaseId(null);
    setCaseActionLoadingId(null);
    setCaseActionError(null);
  }, [workspaceId]);

  const uploadModal = useCaseUploadModal({
    isBusy: uploading,
    onCreateNewCaseUpload: async (files, caseName, metadata) => {
      if (!workspaceId) throw new Error('No file selected for upload.');
      setUploadError(null);
      setUploading(true);
      try {
        const uploaded = await createCaseWithUpload(files, workspaceId, caseName, metadata);
        void navigate(caseViewerPath(uploaded.workspace_id, uploaded.case_id, uploaded.title));
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setUploadError(message);
        throw err;
      } finally {
        setUploading(false);
      }
    },
  });

  const openCase = (caseItem: CaseSummary) => {
    const target = caseSummaryPath(caseItem, workspaceId);
    if (!target) return;
    void navigate(target);
  };

  const handleUploadCardClick = () => {
    if (!workspaceId || uploading || !uploadsEnabled) return;
    setUploadError(null);
    uploadModal.requestUploadFile();
  };

  const startEditingCase = (caseItem: CaseSummary) => {
    setEditingCaseId(caseItem.case_id);
    setEditValue(caseItem.subject_name);
    setDeletingCaseId(null);
    setCaseActionError(null);
  };

  const cancelEditingCase = () => {
    setEditingCaseId(null);
    setEditValue('');
    setCaseActionError(null);
  };

  const handleRenameCase = async (caseItem: CaseSummary) => {
    const trimmed = editValue.trim();
    const validationError = getCaseNameValidationError(trimmed);
    if (validationError) {
      setCaseActionError({ caseId: caseItem.case_id, message: validationError });
      return;
    }
    if (trimmed === caseItem.subject_name) {
      cancelEditingCase();
      return;
    }
    setCaseActionLoadingId(caseItem.case_id);
    setCaseActionError(null);
    try {
      const renamed = await renameCase(caseItem.case_id, trimmed);
      setCases((current) => current.map((entry) => (
        entry.case_id === caseItem.case_id
          ? { ...entry, case_id: renamed.new_id, subject_name: renamed.new_title }
          : entry
      )));
      setEditingCaseId(null);
      setEditValue('');
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCaseActionError({ caseId: caseItem.case_id, message });
    } finally {
      setCaseActionLoadingId(null);
    }
  };

  const handleDeleteCase = async (caseItem: CaseSummary) => {
    setCaseActionLoadingId(caseItem.case_id);
    setCaseActionError(null);
    try {
      await deleteCase(caseItem.case_id);
      removeCaseState(caseItem.case_id);
      setCases((current) => current.filter((entry) => entry.case_id !== caseItem.case_id));
      if (workspaceId) {
        setWorkspaceCaseCounts((current) => ({
          ...current,
          [workspaceId]: Math.max(0, (current[workspaceId] ?? cases.length) - 1),
        }));
      }
      setDeletingCaseId(null);
      if (editingCaseId === caseItem.case_id) cancelEditingCase();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setCaseActionError({ caseId: caseItem.case_id, message });
    } finally {
      setCaseActionLoadingId(null);
    }
  };

  const handleDownloadCase = async (caseItem: CaseSummary) => {
    try {
      await downloadCaseArchive(caseItem.case_id, caseItem.subject_name);
    } catch (err) {
      setCaseActionError({ caseId: caseItem.case_id, message: err instanceof Error ? err.message : String(err) });
    }
  };

  const handleCancelBatchRun = async (runId: string) => {
    if (!workspaceId || cancelingRunId) return;
    setCancelingRunId(runId);
    try {
      const detail = await cancelWorkspaceBatchRun(workspaceId, runId);
      setWorkspaceBatchRuns((current) => current.map((run) => (run.run_id === detail.run_id ? detail : run)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCancelingRunId(null);
    }
  };

  const submitWorkspace = async () => {
    const name = workspaceName.trim();
    if (!name) {
      setWorkspaceError('Workspace name cannot be empty.');
      return;
    }
    const validationError = getSlugNameValidationError(name, 'Workspace name');
    if (validationError) {
      setWorkspaceError(validationError);
      return;
    }
    try {
      setWorkspaceError(null);
      if (editingWorkspaceId) {
        const workspace = await renameWorkspace(editingWorkspaceId, name, workspaceDescription);
        void navigate(workspaceCasesPath(workspace.id));
      } else {
        const workspace = await createWorkspace(name, workspaceDescription);
        void navigate(workspaceCasesPath(workspace.id));
      }
      setCreatingWorkspace(false);
      setEditingWorkspaceId(null);
      setWorkspaceName('');
      setWorkspaceDescription('');
      await refresh();
    } catch (err) {
      setWorkspaceError(err instanceof Error ? err.message : String(err));
    }
  };

  const openWorkspaceEdit = (workspace = currentWorkspace) => {
    if (!workspace) return;
    setEditingWorkspaceId(workspace.id);
    setWorkspaceName(workspace.name);
    setWorkspaceDescription(workspace.description ?? '');
    setCreatingWorkspace(true);
    setWorkspaceMenuOpenId(null);
  };

  const openWorkspaceCreate = () => {
    if (!destructiveActionsEnabled) return;
    setCreatingWorkspace(true);
    setEditingWorkspaceId(null);
    setWorkspaceName('');
    setWorkspaceDescription('');
  };

  const openWorkspaceDelete = (targetWorkspaceId: string) => {
    setWorkspaceDeleteTargetId(targetWorkspaceId);
    setConfirmNonEmptyWorkspaceDelete(false);
    setWorkspaceDeleteError(null);
    setWorkspaceMenuOpenId(null);
  };

  const cancelWorkspaceDelete = () => {
    setWorkspaceDeleteTargetId(null);
    setConfirmNonEmptyWorkspaceDelete(false);
  };

  const startDeletingCase = (caseId: string) => {
    setDeletingCaseId(caseId);
    setEditingCaseId(null);
    setEditValue('');
    setCaseActionError(null);
  };

  const confirmDeleteWorkspace = async () => {
    if (!workspaceDeleteTarget) return;
    setDeletingWorkspace(true);
    setWorkspaceDeleteError(null);
    try {
      await deleteWorkspace(workspaceDeleteTarget.id, confirmNonEmptyWorkspaceDelete);
      const fallbackWorkspace = session?.workspaces.find((workspace) => workspace.id !== workspaceDeleteTarget.id && workspace.status === 'active');
      setWorkspaceDeleteTargetId(null);
      setWorkspaceMenuOpenId(null);
      if (workspaceDeleteTarget.id === workspaceId) {
        void navigate(fallbackWorkspace ? workspaceCasesPath(fallbackWorkspace.id) : '/');
      }
      await refresh();
    } catch (err) {
      setWorkspaceDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingWorkspace(false);
    }
  };

  const workspaceDeleteCaseCount = workspaceDeleteTarget
    ? workspaceDeleteTarget.id === workspaceId
      ? cases.length
      : workspaceCaseCounts[workspaceDeleteTarget.id] ?? workspaceDeleteTarget.case_count ?? 0
    : 0;
  const workspaceDeleteNeedsExtraConfirm = !!workspaceDeleteTarget && workspaceDeleteCaseCount > 0;

  return (
    <div className={`nc-shell ${isLight ? 'nc-light' : ''}`}>
      <div className="nc-topbar">
        <div className="nc-logo">
          <img src="/logo-192.png" alt="" className="nc-logo-mark" aria-hidden="true" />
          <span>NeuroCade</span>
        </div>
        <div className="min-w-0 flex-1" />
        <div className="nc-topbar-search relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--nc-tx-dim)]" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Filter cases..."
            className="nc-topbar-search-input h-[30px] w-full rounded border border-[var(--nc-border)] bg-[var(--nc-bg-surface)] px-7 text-[var(--nc-tx)] outline-none"
          />
        </div>
        <button type="button" onClick={handleUploadCardClick} disabled={!workspaceId || uploading || !uploadsEnabled} className="nc-btn nc-btn-warning" title={uploadsEnabled ? 'New Case' : 'Uploads disabled'}>
          {uploading ? <LoaderCircle size={13} className="animate-spin" /> : <FilePlus2 size={13} />}
          <span className="nc-topbar-button-text">New Case</span>
        </button>
        <button type="button" onClick={() => setChatOpen((value) => !value)} className={`nc-btn ${chatOpen ? 'nc-btn-active' : ''}`} title="Assistant">
          <MessageSquare size={13} />
          <span className="nc-topbar-button-text">Assistant</span>
        </button>
        <button type="button" onClick={() => setIsLight((value) => !value)} className="nc-btn nc-icon-btn" title={isLight ? 'Switch to dark mode' : 'Switch to light mode'}>
          {isLight ? <Moon size={14} /> : <Sun size={14} />}
        </button>
        <SessionActions />
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <WorkspaceSidebar
          workspaces={session?.workspaces ?? []}
          activeWorkspaceId={workspaceId}
          activeCaseCount={cases.length}
          workspaceCaseCounts={workspaceCaseCounts}
          workspaceMenuOpenId={workspaceMenuOpenId}
          destructiveActionsEnabled={destructiveActionsEnabled}
          width={workspacePaneWidth}
          onStartResize={startWorkspaceResize}
          onOpenWorkspace={(targetWorkspaceId) => void navigate(workspaceCasesPath(targetWorkspaceId))}
          onEditWorkspace={openWorkspaceEdit}
          onToggleWorkspaceMenu={(targetWorkspaceId) => setWorkspaceMenuOpenId((current) => current === targetWorkspaceId ? null : targetWorkspaceId)}
          onDeleteWorkspace={openWorkspaceDelete}
          onCreateWorkspace={openWorkspaceCreate}
        />

        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <section className="nc-panel nc-workspace-summary">
            <div className="flex items-start gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center">
                  <h1 className="nc-workspace-summary-title truncate">{currentWorkspace?.name ?? 'Workspace'}</h1>
                </div>
                <div className="flex min-w-0 items-center gap-1">
                  <p className="nc-workspace-summary-description truncate">
                    {currentWorkspace?.description ?? 'Use this workspace for a cohort, study, or set of analyses.'}
                  </p>
                  <button type="button" className="nc-btn nc-workspace-summary-edit" onClick={() => openWorkspaceEdit()} title="Edit workspace" aria-label="Edit workspace">
                    <Pencil size={10} />
                  </button>
                </div>
              </div>
              <button
                type="button"
                className="nc-view-mode-toggle"
                onClick={() => setViewMode((current) => (current === 'cards' ? 'list' : 'cards'))}
                title={viewMode === 'cards' ? 'Switch to list view' : 'Switch to card view'}
                aria-label={viewMode === 'cards' ? 'Switch to list view' : 'Switch to card view'}
              >
                <span className={`nc-view-mode-option ${viewMode === 'cards' ? 'nc-view-mode-option-active' : ''}`}>
                  <Grid2X2 size={13} />
                </span>
                <span className={`nc-view-mode-option ${viewMode === 'list' ? 'nc-view-mode-option-active' : ''}`}>
                  <List size={13} />
                </span>
              </button>
            </div>
          </section>

          <section className="min-h-0 flex-1 overflow-y-auto p-4">
            {!loading && !error && cases.length === 0 && (
              <div className="nc-helper-text mb-4 rounded border border-dashed border-[var(--nc-border)] bg-[var(--nc-bg-panel)] px-4 py-3">
                This workspace is empty. Upload an MRI file to create the first case.
              </div>
            )}
            {error && <div className="nc-helper-text mb-4 rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] p-4 !text-[var(--nc-danger)]">{error}</div>}
            {uploadError && <div className="nc-helper-text mb-4 rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] p-4 !text-[var(--nc-danger)]">{uploadError}</div>}

            <div className={viewMode === 'cards' ? 'grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3' : 'flex flex-col gap-2.5'}>
              {loading && (
                <div className="nc-card flex min-h-[140px] items-center justify-center gap-3 p-4 text-[var(--nc-tx-muted)]">
                  <LoaderCircle className="animate-spin" size={18} />
                  <span>Loading cases...</span>
                </div>
              )}
              {filteredCases.map((caseItem) => (
                <CaseCard
                  key={caseItem.case_id}
                  caseItem={caseItem}
                  viewMode={viewMode}
                  isEditing={editingCaseId === caseItem.case_id}
                  isDeleting={deletingCaseId === caseItem.case_id}
                  isActionLoading={caseActionLoadingId === caseItem.case_id}
                  actionError={caseActionError?.caseId === caseItem.case_id ? caseActionError.message : null}
                  editValue={editValue}
                  destructiveActionsEnabled={destructiveActionsEnabled}
                  onOpen={openCase}
                  onEditValueChange={setEditValue}
                  onStartEditing={startEditingCase}
                  onCancelEditing={cancelEditingCase}
                  onRename={(targetCase) => void handleRenameCase(targetCase)}
                  onStartDeleting={startDeletingCase}
                  onCancelDeleting={() => setDeletingCaseId(null)}
                  onDelete={(targetCase) => void handleDeleteCase(targetCase)}
                  onDownload={(targetCase) => void handleDownloadCase(targetCase)}
                />
              ))}
              <button
                type="button"
                onClick={handleUploadCardClick}
                disabled={!workspaceId || uploading || !uploadsEnabled}
                className={`nc-card nc-upload-case-card ${viewMode === 'list' ? 'nc-upload-case-card-list' : 'nc-upload-case-card-grid'} disabled:opacity-60`}
                title={uploadsEnabled ? 'Upload case' : 'Uploads disabled for this deployment'}
              >
                <span className="nc-upload-case-icon">
                  {uploading ? <LoaderCircle className="animate-spin" size={18} /> : <Upload size={18} />}
                </span>
                <div className="min-w-0">
                  <div className="nc-upload-case-title">{uploadsEnabled ? 'Upload Case' : 'Sample Data Only'}</div>
                  <div className="nc-upload-case-description">
                    {uploadsEnabled ? 'Supports NIfTI, MGZ, DICOM files, and DICOM ZIP archives.' : 'Uploads are disabled for this deployment.'}
                  </div>
                </div>
              </button>
            </div>
          </section>
        </main>

        {chatOpen && (
          <aside className="nc-panel relative flex shrink-0 flex-col overflow-hidden border-l" style={{ width: chatPaneWidth }}>
            <div className="nc-pane-header">
              <MessageSquare size={12} />
              <span>Workspace Assistant</span>
              <button
                type="button"
                className="chat-clear-button ml-auto"
                onClick={requestWorkspaceChatClear}
                disabled={isWorkspaceChatClearing}
                title="Clear chat context"
                aria-label="Clear chat context"
              >
                <span aria-hidden="true">+</span>
              </button>
            </div>
            {activeWorkspaceRuns.length > 0 && (
              <div className="border-b border-[var(--nc-border)] p-3">
                <div className="nc-mono mb-2 text-[var(--nc-fs-2xs)] uppercase tracking-[1px] text-[var(--nc-warning)]">Active Runs</div>
                <div className="space-y-2">
                  {activeWorkspaceRuns.map((run) => (
                    <div key={run.run_id} className="rounded border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] p-2">
                      <div className="truncate text-[var(--nc-fs-sm)] text-[var(--nc-tx)]">{run.report_name}</div>
                      <div className="nc-mono mt-1 text-[var(--nc-fs-2xs)] text-[var(--nc-tx-dim)]">
                        {run.running_cases} running / {run.completed_cases} done
                      </div>
                      {(run.status === 'queued' || run.status === 'running') && (
                        <button type="button" onClick={() => void handleCancelBatchRun(run.run_id)} disabled={cancelingRunId === run.run_id} className="nc-btn mt-2 !min-h-0 !px-2 !py-1">
                          {cancelingRunId === run.run_id ? 'Canceling...' : 'Cancel Run'}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <Chat
              workspaceId={workspaceId ?? null}
              externalMessages={workspaceChatNotifications}
              style={{ flex: 1, minHeight: 0, marginTop: 0, borderRadius: 0 }}
              hideHeader
              guiSessionId={guiSessionIdRef.current}
              clearRequestToken={workspaceChatClearRequestToken}
              onClearStateChange={setIsWorkspaceChatClearing}
            />
            <div
              role="separator"
              aria-orientation="vertical"
              className="nc-resize-handle nc-resize-handle-left"
              onMouseDown={startChatResize}
            />
          </aside>
        )}
      </div>

      {uploadModal.showUploadModal && (
        <UploadCaseModal
          key={`${uploadModal.pendingUploadFiles[0]?.name ?? ''}:${uploadModal.pendingUploadFiles.length}:${uploadModal.pendingUploadDefaultName}`}
          isOpen={uploadModal.showUploadModal}
          filename={uploadModal.pendingUploadFiles[0]?.name ?? null}
          fileCount={uploadModal.pendingUploadFiles.length}
          defaultName={uploadModal.pendingUploadDefaultName}
          onClose={uploadModal.closeUploadModal}
          onSelectFiles={uploadModal.selectUploadFiles}
          onCreateNewCase={uploadModal.confirmCreateNewCaseUpload}
        />
      )}

      <WorkspaceDialogs
        workspaceDeleteTarget={workspaceDeleteTarget}
        workspaceDeleteCaseCount={workspaceDeleteCaseCount}
        workspaceDeleteNeedsExtraConfirm={workspaceDeleteNeedsExtraConfirm}
        deletingWorkspace={deletingWorkspace}
        workspaceDeleteError={workspaceDeleteError}
        confirmNonEmptyWorkspaceDelete={confirmNonEmptyWorkspaceDelete}
        creatingWorkspace={creatingWorkspace}
        editingWorkspaceId={editingWorkspaceId}
        workspaceName={workspaceName}
        workspaceDescription={workspaceDescription}
        workspaceError={workspaceError}
        onCancelDeleteWorkspace={cancelWorkspaceDelete}
        onConfirmDeleteWorkspace={() => void confirmDeleteWorkspace()}
        onConfirmNonEmptyWorkspaceDeleteChange={setConfirmNonEmptyWorkspaceDelete}
        onCloseWorkspaceForm={() => setCreatingWorkspace(false)}
        onWorkspaceNameChange={setWorkspaceName}
        onWorkspaceDescriptionChange={setWorkspaceDescription}
        onSubmitWorkspace={() => void submitWorkspace()}
      />
    </div>
  );
}
