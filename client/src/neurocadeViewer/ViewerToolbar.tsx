import React from 'react';
import { Compass, HelpCircle, Move, RotateCcw, Ruler, SlidersHorizontal } from 'lucide-react';

import type { LocationInfo } from '../types';
import {
  VIEW_MODES,
  type NeuroCadeViewMode,
  type ViewerDragMode,
} from './viewerControls';

interface ViewerToolbarProps {
  dragMode: ViewerDragMode;
  viewMode: NeuroCadeViewMode;
  location: LocationInfo | null;
  showOrientationLabels: boolean;
  onDragModeChange: (mode: ViewerDragMode) => void;
  onViewModeChange: (mode: NeuroCadeViewMode) => void;
  onToggleOrientationLabels: () => void;
  onOpenHelp: () => void;
  onResetView: () => void;
}

const INTERACTION_TOOLS: { mode: ViewerDragMode; Icon: React.ComponentType<{ size?: number }>; label: string }[] = [
  { mode: 'pan', Icon: Move, label: 'Right-click pan / zoom' },
  { mode: 'contrast', Icon: SlidersHorizontal, label: 'Right-click window / level' },
  { mode: 'measurement', Icon: Ruler, label: 'Right-click measure distance' },
];

export function ViewerToolbar({
  dragMode,
  viewMode,
  location,
  showOrientationLabels,
  onDragModeChange,
  onViewModeChange,
  onToggleOrientationLabels,
  onOpenHelp,
  onResetView,
}: ViewerToolbarProps) {
  const labelIndex = location?.labelIndex ?? 0;
  const labelName = location?.labelName ?? 'Background';
  const labelColor = location?.labelColor;
  const coordinates = location
    ? location.vox.map((value) => Math.round(value)).join(',')
    : null;

  return (
    <div className="nc-viewer-toolbar">
      <div className="nc-viewer-toolbar-cluster">
        <div className="nc-viewer-toolbar-group" role="group" aria-label="Right-click mouse action">
          {INTERACTION_TOOLS.map(({ mode, Icon, label }) => (
            <button
              key={mode}
              type="button"
              className={`nc-viewer-toolbar-btn nc-viewer-toolbar-icon ${dragMode === mode ? 'is-active' : ''}`}
              onClick={() => onDragModeChange(mode)}
              title={label}
              aria-label={label}
              aria-pressed={dragMode === mode}
              data-testid={`viewer-tool-${mode}`}
            >
              <Icon size={14} />
            </button>
          ))}
        </div>
        <div className="nc-viewer-toolbar-group" role="group" aria-label="View layout">
          {VIEW_MODES.map((mode) => (
            <button
              key={mode.id}
              type="button"
              className={`nc-viewer-toolbar-btn ${viewMode === mode.id ? 'is-active' : ''}`}
              onClick={() => onViewModeChange(mode.id)}
              aria-pressed={viewMode === mode.id}
              data-testid={`viewer-view-${mode.id}`}
            >
              {mode.label}
            </button>
          ))}
        </div>
      </div>
      <div className="nc-viewer-toolbar-cluster">
        {location && (
          <div className="nc-viewer-toolbar-status" title={`${labelName}${labelIndex > 0 ? ` (${labelIndex})` : ''}${coordinates ? ` @ ${coordinates}` : ''}`}>
            <span
              className={`nc-viewer-toolbar-label-dot ${labelIndex > 0 ? '' : 'is-background'}`}
              style={labelColor ? { backgroundColor: `rgb(${labelColor.join(',')})` } : undefined}
              aria-hidden="true"
            />
            <span className="nc-viewer-toolbar-label-name">
              {labelIndex > 0 ? `${labelName} (${labelIndex})` : labelName}
            </span>
            {coordinates && <span className="nc-viewer-toolbar-coordinate nc-mono">{coordinates}</span>}
          </div>
        )}
        <div className="nc-viewer-toolbar-group" role="group" aria-label="Display options">
          <button
            type="button"
            className={`nc-viewer-toolbar-btn nc-viewer-toolbar-icon ${showOrientationLabels ? 'is-active' : ''}`}
            onClick={onToggleOrientationLabels}
            title={showOrientationLabels ? 'Hide orientation labels' : 'Show orientation labels'}
            aria-label={showOrientationLabels ? 'Hide orientation labels' : 'Show orientation labels'}
            aria-pressed={showOrientationLabels}
            data-testid="viewer-toggle-orientation-labels"
          >
            <Compass size={14} />
          </button>
          <button
            type="button"
            className="nc-viewer-toolbar-btn nc-viewer-toolbar-icon"
            onClick={onOpenHelp}
            title="Viewer help"
            aria-label="Viewer help"
          >
            <HelpCircle size={14} />
          </button>
          <button type="button" className="nc-viewer-toolbar-btn nc-viewer-toolbar-icon" onClick={onResetView} title="Reset view" aria-label="Reset view">
            <RotateCcw size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
