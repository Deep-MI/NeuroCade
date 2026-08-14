import { ArrowLeft, Download, FileUp, Folder, Layers, LoaderCircle, MessageSquare, Moon, Play, Square, Sun, TerminalSquare } from 'lucide-react';

import { isRunActive } from '../constants';
import type { AnalysisToolSummary } from '../types';

export type WorkspaceRightPanel = 'chat' | 'results' | null;

interface CaseWorkspaceToolbarProps {
  workspaceName: string;
  caseTitle: string;
  hasCase: boolean;
  layerPanelOpen: boolean;
  rightPanel: WorkspaceRightPanel;
  isLight: boolean;
  runStatus: string;
  isSubmittingRun: boolean;
  analysisTools: AnalysisToolSummary[];
  selectedAnalysisToolId: string;
  onBack: () => void;
  onOpenCaseManager: () => void;
  onUpload: () => void;
  onDownload: () => void;
  onToggleLayers: () => void;
  onSelectAnalysisTool: (toolId: string) => void;
  onAnalyze: () => void;
  onCancel: () => void;
  onToggleRightPanel: (panel: Exclude<WorkspaceRightPanel, null>) => void;
  onToggleTheme: () => void;
}

export function CaseWorkspaceToolbar(props: CaseWorkspaceToolbarProps) {
  const selectedTool = props.analysisTools.find((tool) => tool.id === props.selectedAnalysisToolId)
    ?? props.analysisTools[0]
    ?? null;
  const runActive = isRunActive(props.runStatus);

  return (
    <div className="nc-topbar">
      <div className="nc-logo"><img src="/logo-192.png" alt="" className="nc-logo-mark" aria-hidden="true" /><span>NeuroCade</span></div>
      <div className="h-5 w-px bg-[var(--nc-border)]" />
      <button type="button" onClick={props.onBack} className="nc-btn" data-testid="case-workspace-back">
        <ArrowLeft size={13} className="text-[var(--nc-interactive)]" />
        <span className="hidden max-w-[130px] truncate lg:inline">{props.workspaceName}</span>
      </button>
      <button type="button" onClick={props.onOpenCaseManager} className="nc-btn nc-btn-active">
        <Folder size={13} className="text-[var(--nc-interactive)]" />
        <span className="max-w-[105px] truncate font-normal sm:max-w-[150px]">{props.caseTitle}</span>
      </button>
      <button type="button" onClick={props.onUpload} className="nc-btn"><FileUp size={13} /><span className="hidden lg:inline">Upload</span><span className="sr-only">Choose MRI File</span></button>
      <button type="button" onClick={props.onDownload} disabled={!props.hasCase} className="nc-btn"><Download size={13} /><span className="hidden lg:inline">Download</span></button>
      <div className="flex-1" />
      <button type="button" onClick={props.onToggleLayers} className={`nc-btn ${props.layerPanelOpen ? 'nc-btn-active' : ''}`}><Layers size={13} /><span className="hidden lg:inline">Layers</span></button>
      <div className={`nc-analysis-launch ${runActive ? 'is-running' : ''}`}>
        {!runActive && (
          <div className="nc-analysis-launch-select-wrap max-w-[230px]">
            <select aria-label="Analysis workflow" value={selectedTool?.id ?? ''} onChange={(event) => props.onSelectAnalysisTool(event.target.value)} disabled={props.isSubmittingRun || props.analysisTools.length === 0} className="nc-btn nc-select nc-analysis-launch-select w-full text-xs">
              {props.analysisTools.length === 0 ? <option value="">No analyses configured</option> : props.analysisTools.map((tool) => <option key={tool.id} value={tool.id}>{tool.label}</option>)}
            </select>
          </div>
        )}
        <button type="button" onClick={runActive ? props.onCancel : props.onAnalyze} disabled={props.isSubmittingRun || (!runActive && (!props.hasCase || !selectedTool))} className={`nc-btn ${runActive ? 'nc-btn-warning' : 'nc-analysis-launch-button'}`} aria-busy={props.isSubmittingRun}>
          {props.isSubmittingRun ? <LoaderCircle size={13} className="animate-spin" /> : runActive ? <Square size={13} /> : <Play size={13} />}
          <span className="hidden lg:inline">{props.isSubmittingRun ? 'Starting' : runActive ? 'Cancel' : 'Launch'}</span>
          <span className="sr-only">{props.isSubmittingRun ? 'Starting analysis' : runActive ? 'Cancel Analysis' : `Launch ${selectedTool?.label ?? 'selected'} Analysis`}</span>
        </button>
      </div>
      <button type="button" onClick={() => props.onToggleRightPanel('chat')} className={`nc-btn ${props.rightPanel === 'chat' ? 'nc-btn-active' : ''}`}><MessageSquare size={13} /><span className="hidden lg:inline">Chat</span></button>
      <button type="button" onClick={() => props.onToggleRightPanel('results')} className={`nc-btn ${props.rightPanel === 'results' ? 'nc-btn-active' : ''}`}><TerminalSquare size={13} /><span className="hidden lg:inline">Terminal</span></button>
      <button type="button" onClick={props.onToggleTheme} className="nc-btn nc-icon-btn" title={props.isLight ? 'Switch to dark mode' : 'Switch to light mode'}>{props.isLight ? <Moon size={14} /> : <Sun size={14} />}</button>
    </div>
  );
}
