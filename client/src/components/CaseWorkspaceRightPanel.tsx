import { lazy, Suspense, type MouseEventHandler } from 'react';
import { Check, LoaderCircle, MessageSquare, TerminalSquare, X } from 'lucide-react';

import { isRunActive, isRunDone, isRunFailed } from '../constants';
import type { ChatMessage, LocationInfo, MriSnapshots } from '../types';
import { ErrorBoundary } from './ErrorBoundary';
import type { WorkspaceRightPanel } from './CaseWorkspaceToolbar';

const Chat = lazy(() => import('./Chat').then((module) => ({ default: module.Chat })));

function AnalysisStatusIndicator({ status }: { status: string }) {
  if (isRunDone(status)) return <span className="analysis-status-indicator is-done" title="Analysis finished" aria-label="Analysis finished"><Check size={13} /></span>;
  if (isRunFailed(status)) return <span className="analysis-status-indicator is-failed" title="Analysis failed" aria-label="Analysis failed"><X size={13} /></span>;
  if (status === 'queued') return <span className="analysis-status-indicator is-queued" title="Analysis queued" aria-label="Analysis queued"><span aria-hidden="true">...</span></span>;
  if (isRunActive(status)) return <span className="analysis-status-indicator is-running" title="Analysis running" aria-label="Analysis running"><LoaderCircle size={13} className="animate-spin" /></span>;
  return null;
}

interface CaseWorkspaceRightPanelProps {
  panel: Exclude<WorkspaceRightPanel, null>;
  width: number;
  onStartResize: MouseEventHandler<HTMLDivElement>;
  runStatus: string;
  terminalOutput: string;
  terminalStatusMessage: string | null;
  chatMessages: ChatMessage[];
  currentLocation: LocationInfo | null;
  getMriSnapshots: () => MriSnapshots | null;
  workspaceId: string | null;
  caseId: string | null;
  guiSessionId: string;
  chatClearRequestToken: number;
  isChatClearing: boolean;
  onRequestChatClear: () => void;
  onChatClearStateChange: (isClearing: boolean) => void;
  onAssistantTurnComplete: () => void;
}

export function CaseWorkspaceRightPanel(props: CaseWorkspaceRightPanelProps) {
  return (
    <aside className="nc-panel relative flex shrink-0 flex-col overflow-hidden border-l" style={{ width: props.width }}>
      <div className="nc-pane-header">
        {props.panel === 'chat' ? <MessageSquare size={12} /> : <TerminalSquare size={12} />}
        <span>{props.panel === 'chat' ? 'Case Assistant' : 'Terminal Output'}</span>
        {props.panel === 'chat' ? (
          <button type="button" className="chat-clear-button ml-auto" onClick={props.onRequestChatClear} disabled={props.isChatClearing} title="Clear chat context" aria-label="Clear chat context"><span aria-hidden="true">+</span></button>
        ) : <AnalysisStatusIndicator status={props.runStatus} />}
      </div>
      {props.panel === 'chat' ? (
        <ErrorBoundary label="Chat">
          <Suspense fallback={<div className="p-4 text-sm text-[var(--nc-tx-muted)]">Loading chat...</div>}>
            <Chat externalMessages={props.chatMessages} style={{ flex: 1, minHeight: 0, marginTop: 0, borderRadius: 0 }} hideHeader currentLocation={props.currentLocation} getMriSnapshots={props.getMriSnapshots} workspaceId={props.workspaceId} caseId={props.caseId} guiSessionId={props.guiSessionId} clearRequestToken={props.chatClearRequestToken} onClearStateChange={props.onChatClearStateChange} onAssistantTurnComplete={props.onAssistantTurnComplete} />
          </Suspense>
        </ErrorBoundary>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col bg-[var(--nc-bg-deep)]">
          <div data-testid="terminal-content" className="nc-mono min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap p-3 text-[11px] leading-[1.45]">
            <pre className="whitespace-pre-wrap text-[var(--nc-tx-muted)]">{props.terminalOutput || 'No analysis run yet. Click Launch to start.'}</pre>
            {props.terminalStatusMessage && <div data-testid="terminal-job-status" role="status" className={`mt-3 border-t border-[var(--nc-border)] pt-2 font-semibold ${isRunDone(props.runStatus) ? 'text-green-500' : 'text-red-500'}`}>{props.terminalStatusMessage}</div>}
          </div>
        </div>
      )}
      <div role="separator" aria-orientation="vertical" className="nc-resize-handle nc-resize-handle-left" onMouseDown={props.onStartResize} />
    </aside>
  );
}
