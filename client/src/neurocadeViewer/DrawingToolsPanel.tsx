import { useState } from 'react';
import { Brush, Download, Eraser, MousePointer2, Pencil, Plus, Undo2, X } from 'lucide-react';

import { isSegmentationLayer, type SegmentationVolumeLayer, type Volume } from '../types';
import type { DrawingOptions, DrawingSession } from './nativeDrawing';
import { DrawingLabelControl, EditableSliderValue } from './LayerControls';
import type { DrawingLabelOption } from './useNativeDrawingSession';

interface DrawingToolsPanelProps {
  intensityLayers: Volume[];
  segmentationLayers: Volume[];
  canAddLayers: boolean;
  drawingSession: DrawingSession;
  drawingLabels: DrawingLabelOption[];
  canUndo: boolean;
  onUpdateDrawingOptions: (updates: Partial<DrawingOptions>) => void;
  onBeginBlankDrawing: () => void;
  onBeginDrawingFromSegmentation: (source: SegmentationVolumeLayer) => void;
  onDrawUndo: () => void;
  onSaveDrawing: () => void;
  onCloseDrawing: () => void;
}

function formatRoundedSliderValue(value: number): string {
  return String(Math.round(value));
}

export function DrawingToolsPanel({
  intensityLayers,
  segmentationLayers,
  canAddLayers,
  drawingSession,
  drawingLabels,
  canUndo,
  onUpdateDrawingOptions,
  onBeginBlankDrawing,
  onBeginDrawingFromSegmentation,
  onDrawUndo,
  onSaveDrawing,
  onCloseDrawing,
}: DrawingToolsPanelProps) {
  const [drawingMenuOpen, setDrawingMenuOpen] = useState(false);
  const segmentationSources = segmentationLayers.filter(isSegmentationLayer);

  return (
    <section className="nc-viewer-layer-section">
      <div className="nc-viewer-layer-section-header">
        <span>Voxel edit</span>
        <div className="relative">
          <button
            type="button"
            className="nc-btn nc-icon-btn !border-0"
            onClick={() => setDrawingMenuOpen((open) => !open)}
            title="Start voxel editing"
            aria-label="Start voxel editing"
            aria-expanded={drawingMenuOpen}
            disabled={!canAddLayers}
          >
            <Plus size={13} />
          </button>
          {drawingMenuOpen && (
            <button
              type="button"
              aria-hidden="true"
              tabIndex={-1}
              className="fixed inset-0 z-10 cursor-default"
              onClick={() => setDrawingMenuOpen(false)}
            />
          )}
          {drawingMenuOpen && (
            <div className="nc-viewer-drawing-menu" role="menu">
              <button
                type="button"
                role="menuitem"
                className="nc-viewer-drawing-menu-item"
                onClick={() => { setDrawingMenuOpen(false); onBeginBlankDrawing(); }}
                disabled={intensityLayers.length === 0}
                title={intensityLayers.length === 0 ? 'Load an intensity volume first' : 'Empty drawing matching the active volume'}
              >
                New label volume
              </button>
              <div className="nc-viewer-drawing-menu-label">Edit segmentation</div>
              {segmentationSources.length === 0 ? (
                <div className="nc-viewer-drawing-menu-empty">No segmentations loaded</div>
              ) : (
                segmentationSources.map((segmentation) => (
                  <button
                    key={segmentation.id}
                    type="button"
                    role="menuitem"
                    className="nc-viewer-drawing-menu-item truncate"
                    onClick={() => { setDrawingMenuOpen(false); onBeginDrawingFromSegmentation(segmentation); }}
                    title={`Edit a copy of ${segmentation.name}`}
                  >
                    {segmentation.name}
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      <div className="nc-viewer-drawing-tools p-2 space-y-1.5">
        <div className="flex items-center gap-2">
          <Pencil size={12} className="text-[var(--nc-accent)]" />
          <span className="nc-mono min-w-0 flex-1 truncate text-[11px] text-[var(--nc-tx-dim)]" title={drawingSession.source?.name ?? drawingSession.filename}>
            {drawingSession.active ? `Editing: ${drawingSession.source?.name ?? drawingSession.filename}` : 'No active label edit'}
          </span>
        </div>

        {drawingSession.error && (
          <div className="rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] px-2 py-1 text-[11px] text-[var(--nc-danger)]">
            {drawingSession.error}
          </div>
        )}

        <div className="grid grid-cols-3 gap-1">
          <button
            type="button"
            className={`nc-btn flex items-center justify-center gap-1 text-[11px] ${drawingSession.tool === 'navigate' ? 'nc-btn-active' : ''}`}
            onClick={() => onUpdateDrawingOptions({ tool: 'navigate' })}
            disabled={!drawingSession.active}
            title="Navigate without painting"
          >
            <MousePointer2 size={11} /> Navigate
          </button>
          <button
            type="button"
            className={`nc-btn flex items-center justify-center gap-1 text-[11px] ${drawingSession.tool === 'paint' ? 'nc-btn-active' : ''}`}
            onClick={() => onUpdateDrawingOptions({ tool: 'paint' })}
            disabled={!drawingSession.active}
            title="Paint the selected label"
            data-testid="viewer-drawing-paint"
          >
            <Brush size={11} /> Paint
          </button>
          <button
            type="button"
            className={`nc-btn flex items-center justify-center gap-1 text-[11px] ${drawingSession.tool === 'erase' ? 'nc-btn-active' : ''}`}
            onClick={() => onUpdateDrawingOptions({ tool: 'erase' })}
            disabled={!drawingSession.active}
            title="Erase labels with the current brush"
          >
            <Eraser size={11} /> Erase
          </button>
        </div>

        <div className="flex items-center gap-2">
          <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">File</span>
          <input
            type="text"
            value={drawingSession.filename}
            disabled={!drawingSession.active}
            onChange={(event) => onUpdateDrawingOptions({ filename: event.currentTarget.value })}
            className="nc-viewer-layer-select nc-mono min-w-0 flex-1"
            aria-label="Drawing filename"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Opacity</span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={drawingSession.opacity}
            disabled={!drawingSession.active}
            onChange={(event) => onUpdateDrawingOptions({ opacity: Number(event.currentTarget.value) })}
            className="nc-viewer-layer-slider"
            aria-label="Drawing opacity"
          />
          <EditableSliderValue
            value={Math.round(drawingSession.opacity * 100)}
            min={0}
            max={100}
            step={1}
            disabled={!drawingSession.active}
            ariaLabel="Drawing opacity value"
            onCommit={(percent) => onUpdateDrawingOptions({ opacity: percent / 100 })}
            format={formatRoundedSliderValue}
          />
        </div>

        {drawingSession.tool !== 'navigate' && (
          <div className="flex items-center gap-2">
            <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Brush</span>
            <input
              type="range"
              min={1}
              max={10}
              step={1}
              value={drawingSession.brushSize}
              disabled={!drawingSession.active}
              onChange={(event) => onUpdateDrawingOptions({ brushSize: Number(event.currentTarget.value) })}
              className="nc-viewer-layer-slider"
              aria-label="Drawing brush size"
            />
            <EditableSliderValue
              value={drawingSession.brushSize}
              min={1}
              max={10}
              step={1}
              disabled={!drawingSession.active}
              ariaLabel="Drawing brush size value"
              onCommit={(brushSize) => onUpdateDrawingOptions({ brushSize: Math.round(brushSize) })}
              format={formatRoundedSliderValue}
            />
          </div>
        )}

        {drawingSession.tool === 'paint' && (
          <div className="flex items-center gap-2">
            <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">LUT</span>
            <select
              className="nc-viewer-layer-select nc-mono min-w-0 flex-1"
              value={drawingSession.lut}
              onChange={(event) => onUpdateDrawingOptions({
                lut: event.currentTarget.value as DrawingOptions['lut'],
                penValue: 1,
              })}
              disabled={!drawingSession.active}
              aria-label="Drawing color table"
            >
              <option value="binary">Binary</option>
              <option value="freesurfer">FreeSurfer</option>
            </select>
          </div>
        )}

        {drawingSession.active && drawingSession.lut === 'freesurfer' && (
          <div className="nc-mono text-[10px] text-[var(--nc-tx-dim)]">
            Native NiiVue editing supports FreeSurfer label values 1–255.
          </div>
        )}

        {drawingSession.tool === 'paint' && (
          <div className="flex items-center gap-2">
            <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Label</span>
            <DrawingLabelControl
              labels={drawingLabels}
              value={drawingSession.penValue}
              onChange={(penValue) => onUpdateDrawingOptions({ penValue })}
            />
          </div>
        )}

        {drawingSession.tool === 'paint' && (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex cursor-pointer items-center gap-2 text-[11px] text-[var(--nc-tx-dim)]">
              <input
                type="checkbox"
                checked={drawingSession.fillOutline}
                disabled={!drawingSession.active}
                onChange={(event) => onUpdateDrawingOptions({ fillOutline: event.currentTarget.checked })}
              />
              <span>Fill outline</span>
            </label>
            <label className="flex cursor-pointer items-center gap-2 text-[11px] text-[var(--nc-tx-dim)]">
              <input
                type="checkbox"
                checked={drawingSession.overwrite}
                disabled={!drawingSession.active}
                onChange={(event) => onUpdateDrawingOptions({ overwrite: event.currentTarget.checked })}
              />
              <span>Replace labels</span>
            </label>
          </div>
        )}

        <div className="flex gap-1 pt-0.5">
          <button type="button" className="nc-btn flex flex-1 items-center justify-center gap-1 text-[11px]" onClick={onDrawUndo} disabled={!drawingSession.active || !canUndo} title="Undo last drawing change">
            <Undo2 size={11} /> Undo
          </button>
          <button type="button" className="nc-btn flex flex-1 items-center justify-center gap-1 text-[11px]" onClick={onSaveDrawing} disabled={!drawingSession.active} title="Save as segmentation artifact">
            <Download size={11} /> Save
          </button>
          <button type="button" className="nc-btn flex flex-1 items-center justify-center gap-1 text-[11px]" onClick={onCloseDrawing} disabled={!drawingSession.active} title="Close drawing without saving">
            <X size={11} /> Close
          </button>
        </div>
      </div>
    </section>
  );
}
