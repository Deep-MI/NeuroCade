import type React from 'react';
import { Folder, List, MoreHorizontal, Pencil, Trash2 } from 'lucide-react';

import type { WorkspaceSummary } from '../types';

interface WorkspaceSidebarProps {
  workspaces: WorkspaceSummary[];
  activeWorkspaceId?: string;
  activeCaseCount: number;
  workspaceCaseCounts: Record<string, number>;
  workspaceMenuOpenId: string | null;
  destructiveActionsEnabled: boolean;
  width: number;
  onStartResize: (event: React.MouseEvent<HTMLElement>) => void;
  onOpenWorkspace: (workspaceId: string) => void;
  onEditWorkspace: (workspace: WorkspaceSummary) => void;
  onToggleWorkspaceMenu: (workspaceId: string) => void;
  onDeleteWorkspace: (workspaceId: string) => void;
  onCreateWorkspace: () => void;
}

export function WorkspaceSidebar({
  workspaces,
  activeWorkspaceId,
  activeCaseCount,
  workspaceCaseCounts,
  workspaceMenuOpenId,
  destructiveActionsEnabled,
  width,
  onStartResize,
  onOpenWorkspace,
  onEditWorkspace,
  onToggleWorkspaceMenu,
  onDeleteWorkspace,
  onCreateWorkspace,
}: WorkspaceSidebarProps) {
  return (
    <aside className="nc-panel relative flex shrink-0 flex-col overflow-hidden border-r" style={{ width }}>
      <div className="nc-pane-header">
        <List size={12} />
        <span>Workspaces</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {workspaces.map((workspace) => {
          const active = workspace.id === activeWorkspaceId;
          const count = active ? activeCaseCount : workspaceCaseCounts[workspace.id] ?? workspace.case_count ?? 0;
          const canDeleteWorkspace = !workspace.is_default;
          return (
            <div
              key={workspace.id}
              className={`group relative mb-1 rounded border transition ${
                active ? 'border-[var(--nc-interactive-border)] bg-[var(--nc-interactive-subtle)]' : 'border-transparent hover:bg-[var(--nc-row-hover)]'
              }`}
            >
              <button
                type="button"
                onDoubleClick={() => onEditWorkspace(workspace)}
                onClick={() => onOpenWorkspace(workspace.id)}
                className="w-full px-2.5 py-1.5 pr-8 text-left"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <Folder size={13} className={active ? 'text-[var(--nc-interactive)]' : 'text-[var(--nc-tx-muted)]'} />
                  <span className="nc-workspace-list-title truncate">{workspace.name}</span>
                </div>
                <div className="nc-workspace-list-meta mt-0.5 pl-5">
                  {count} {count === 1 ? 'case' : 'cases'}
                </div>
              </button>
              <button
                type="button"
                className="nc-workspace-menu-trigger"
                onClick={(event) => {
                  event.stopPropagation();
                  onToggleWorkspaceMenu(workspace.id);
                }}
                title={`${workspace.name} actions`}
                aria-label={`${workspace.name} actions`}
                aria-expanded={workspaceMenuOpenId === workspace.id}
              >
                <MoreHorizontal size={13} />
              </button>
              {workspaceMenuOpenId === workspace.id && (
                <div className="nc-workspace-menu" onClick={(event) => event.stopPropagation()}>
                  <button type="button" className="nc-workspace-menu-item" onClick={() => onEditWorkspace(workspace)}>
                    <Pencil size={12} />
                    <span>Rename</span>
                  </button>
                  <button
                    type="button"
                    className="nc-workspace-menu-item nc-workspace-menu-danger"
                    disabled={!canDeleteWorkspace || !destructiveActionsEnabled}
                    onClick={() => onDeleteWorkspace(workspace.id)}
                    title={!destructiveActionsEnabled ? 'Disabled for this deployment' : workspace.is_default ? 'Default workspaces cannot be deleted' : 'Delete workspace'}
                  >
                    <Trash2 size={12} />
                    <span>Delete</span>
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="p-1.5">
        <button
          type="button"
          onClick={onCreateWorkspace}
          className="nc-btn nc-workspace-action w-full"
          disabled={!destructiveActionsEnabled}
          title={destructiveActionsEnabled ? 'New Workspace' : 'Disabled for this deployment'}
        >
          <span>New Workspace</span>
        </button>
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        className="nc-resize-handle nc-resize-handle-right"
        onMouseDown={onStartResize}
      />
    </aside>
  );
}
