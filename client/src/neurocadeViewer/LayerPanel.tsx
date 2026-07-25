import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Brush, ChevronDown, ChevronRight, Download, Eraser, Eye, EyeOff, Layers, MousePointer2, Pencil, Plus, Undo2, X } from 'lucide-react';

import { isSegmentationLayer, isSurfaceLayer, type LayerType, type SegmentationVolumeLayer, type SurfaceColorMode, type Volume } from '../types';
import { clampOpacity, layerDefaultOpacity, layerType } from './niivueLayers';
import type { WindowSetting } from './paneSyncKeys';
import type { DrawingOptions, DrawingSession } from './nativeDrawing';
import type { DrawingLabelOption } from './useNativeDrawingSession';
import {
  curvatureNegativeThreshold,
  curvaturePositiveThreshold,
  resolveSurfaceLayerColorMode,
  SURFACE_COLOR_MODE_LABELS,
  surfaceColorModeAvailable,
} from '../utils/surfaceColors';

function titleCaseColormap(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function layerAccent(type: LayerType) {
  if (type === 'surface') return 'text-[var(--nc-warning)]';
  if (type === 'drawing') return 'text-[var(--nc-accent)]';
  if (type === 'segmentation') return 'text-[var(--nc-success)]';
  return 'text-[var(--nc-interactive)]';
}

function sectionTitle(type: LayerType) {
  if (type === 'surface') return 'Surfaces';
  if (type === 'drawing') return 'Drawing';
  if (type === 'segmentation') return 'Segmentations';
  return 'Intensity';
}

interface LayerOpacityControlProps {
  volume: Volume;
  defaultOpacity: number;
  onPreview: (id: string, opacity: number) => void;
  onCommit: (id: string, opacity: number) => void;
}

const LayerOpacityControl = React.memo(function LayerOpacityControl({
  volume,
  defaultOpacity,
  onPreview,
  onCommit,
}: LayerOpacityControlProps) {
  const committedOpacity = clampOpacity(volume.opacity, defaultOpacity);
  const [draftOpacity, setDraftOpacity] = useState(committedOpacity);
  const draftOpacityRef = useRef(committedOpacity);
  const committedOpacityRef = useRef(committedOpacity);

  useEffect(() => {
    committedOpacityRef.current = committedOpacity;
    draftOpacityRef.current = committedOpacity;
    setDraftOpacity(committedOpacity);
  }, [committedOpacity]);

  const preview = useCallback((value: number) => {
    const next = clampOpacity(value, defaultOpacity);
    draftOpacityRef.current = next;
    setDraftOpacity(next);
    onPreview(volume.id, next);
  }, [defaultOpacity, onPreview, volume.id]);

  const commit = useCallback(() => {
    const next = draftOpacityRef.current;
    if (Math.abs(next - committedOpacityRef.current) < 0.0001) return;
    committedOpacityRef.current = next;
    onCommit(volume.id, next);
  }, [onCommit, volume.id]);

  return (
    <>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={draftOpacity}
        onChange={(event) => preview(Number(event.currentTarget.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
        className="nc-viewer-layer-slider"
        aria-label={`${volume.name} opacity`}
        data-testid="viewer-layer-opacity"
      />
      <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(draftOpacity * 100)}</span>
    </>
  );
});

interface DrawingLabelControlProps {
  labels: DrawingLabelOption[];
  value: number;
  onChange: (value: number) => void;
}

function drawingLabelText(label: DrawingLabelOption | undefined, value: number): string {
  return label ? `${label.value} · ${label.name}` : String(value);
}

const DrawingLabelControl = React.memo(function DrawingLabelControl({
  labels,
  value,
  onChange,
}: DrawingLabelControlProps) {
  const selected = labels.find((label) => label.value === value);
  const selectedText = drawingLabelText(selected, value);
  const [draft, setDraft] = useState(selectedText);

  useEffect(() => {
    setDraft(selectedText);
  }, [selectedText]);

  const update = (text: string) => {
    setDraft(text);
    const parsed = Number.parseInt(text, 10);
    if (Number.isInteger(parsed) && parsed > 0) onChange(parsed);
  };

  return (
    <>
      <span
        className="h-3 w-3 shrink-0 rounded-sm border border-[var(--nc-border)]"
        style={{ backgroundColor: selected?.color ?? 'transparent' }}
        aria-hidden="true"
      />
      <input
        type="text"
        list="viewer-drawing-label-options"
        value={draft}
        onChange={(event) => update(event.currentTarget.value)}
        onFocus={(event) => event.currentTarget.select()}
        className="nc-viewer-layer-select nc-mono min-w-0 flex-1"
        aria-label="Drawing label"
        placeholder="Type a label name or number"
      />
      <datalist id="viewer-drawing-label-options">
        {labels.map((label) => (
          <option key={label.value} value={drawingLabelText(label, label.value)} />
        ))}
      </datalist>
    </>
  );
});

interface CurvatureThresholdControlProps {
  label: 'Green' | 'Red';
  ariaLabel: string;
  value: number;
  onCommit: (value: number) => void;
}

const CurvatureThresholdControl = React.memo(function CurvatureThresholdControl({
  label,
  ariaLabel,
  value,
  onCommit,
}: CurvatureThresholdControlProps) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = () => {
    const parsed = Number(draft);
    const valid = Number.isFinite(parsed) && (label === 'Green' ? parsed < 0 : parsed > 0);
    if (!valid) {
      setDraft(String(value));
      return;
    }
    onCommit(parsed);
  };

  return (
    <div className="flex items-center gap-2">
      <span className={`nc-mono w-12 shrink-0 text-[11px] ${label === 'Green' ? 'text-green-400' : 'text-red-400'}`}>{label}</span>
      <input
        type="number"
        step={0.01}
        value={draft}
        onChange={(event) => setDraft(event.currentTarget.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            commit();
            event.currentTarget.blur();
          }
        }}
        className="nc-viewer-layer-select nc-mono min-w-0 flex-1"
        aria-label={ariaLabel}
      />
    </div>
  );
});

interface LayerPanelProps {
  layerPanelWidth: number;
  onStartLayerPanelResize: (event: React.MouseEvent<HTMLDivElement>) => void;
  groupedLayers: {
    intensity: Volume[];
    segmentation: Volume[];
    surface: Volume[];
  };
  canAddLayers: boolean;
  onOpenLayerPicker?: (type: LayerType) => void;
  onRemoveVolume?: (id: string) => void;
  expandedLayerId: string | null;
  draggingLayerId: string | null;
  dragTarget: { id: string; position: 'before' | 'after' } | null;
  referenceVolumeId: string | null;
  windowings: Record<string, WindowSetting>;
  intensityColormaps: string[];
  drawingSession: DrawingSession;
  drawingLabels: DrawingLabelOption[];
  canUndo: boolean;
  onToggleExpandLayer: (id: string, type: LayerType) => void;
  onUpdateVolume: (id: string, updates: Partial<Volume>) => void;
  onPreviewVolumeOpacity: (id: string, opacity: number) => void;
  onCommitVolumeOpacity: (id: string, opacity: number) => void;
  onUpdateWindowing: (id: string, field: 'calMin' | 'calMax', value: number) => void;
  onLayerDragOver: (event: React.DragEvent<HTMLDivElement>, target: Volume) => void;
  onLayerDrop: (event: React.DragEvent<HTMLDivElement>, target: Volume) => void;
  onLayerDragStart: (id: string, event: React.DragEvent<HTMLButtonElement>) => void;
  onLayerDragEnd: () => void;
  onLayerDragLeave: (id: string) => void;
  onLayerReorderKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>, layer: Volume, sectionLayers: Volume[]) => void;
  onUpdateDrawingOptions: (updates: Partial<DrawingOptions>) => void;
  onBeginBlankDrawing: () => void;
  onBeginDrawingFromSegmentation: (source: SegmentationVolumeLayer) => void;
  onDrawUndo: () => void;
  onSaveDrawing: () => void;
  onCloseDrawing: () => void;
}

export function LayerPanel({
  layerPanelWidth,
  onStartLayerPanelResize,
  groupedLayers,
  canAddLayers,
  onOpenLayerPicker,
  onRemoveVolume,
  expandedLayerId,
  draggingLayerId,
  dragTarget,
  referenceVolumeId,
  windowings,
  intensityColormaps,
  drawingSession,
  drawingLabels,
  canUndo,
  onToggleExpandLayer,
  onUpdateVolume,
  onPreviewVolumeOpacity,
  onCommitVolumeOpacity,
  onUpdateWindowing,
  onLayerDragOver,
  onLayerDrop,
  onLayerDragStart,
  onLayerDragEnd,
  onLayerDragLeave,
  onLayerReorderKeyDown,
  onUpdateDrawingOptions,
  onBeginBlankDrawing,
  onBeginDrawingFromSegmentation,
  onDrawUndo,
  onSaveDrawing,
  onCloseDrawing,
}: LayerPanelProps) {
  const segmentationSources = groupedLayers.segmentation.filter(isSegmentationLayer);
  const [drawingMenuOpen, setDrawingMenuOpen] = useState(false);

  const renderLayerSection = (type: LayerType, items: Volume[]) => (
    <section key={type} className="nc-viewer-layer-section">
      <div className="nc-viewer-layer-section-header">
        <span>{sectionTitle(type)}</span>
        {canAddLayers && onOpenLayerPicker && (
          <button type="button" className="nc-btn nc-icon-btn !border-0" onClick={() => onOpenLayerPicker(type)} title={`Load ${sectionTitle(type)}`} aria-label={`Load ${sectionTitle(type)}`}>
            <Plus size={13} />
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <div className="nc-viewer-empty-layer">No {sectionTitle(type).toLowerCase()} loaded</div>
      ) : (
        <div className="nc-viewer-layer-list">
          {items.map((volume) => {
            const typeName = layerType(volume);
            const isExpanded = expandedLayerId === volume.id;
            const isReferenceVolume = !isSurfaceLayer(volume) && volume.id === referenceVolumeId;
            const showWindowing = typeName === 'intensity';
            const showSurfaceDisplay = isSurfaceLayer(volume);
            const surfaceColorMode = showSurfaceDisplay ? resolveSurfaceLayerColorMode(volume) : 'solid';
            const win = windowings[volume.id];
            const defaultOpacity = layerDefaultOpacity(volume);
            const dropClass = dragTarget?.id === volume.id ? `nc-layer-drop-${dragTarget.position}` : '';
            const currentColormap = (volume.colormap || 'gray').toLowerCase();
            const colormapOptions = intensityColormaps.includes(currentColormap)
              ? intensityColormaps
              : [currentColormap, ...intensityColormaps];
            const windowStep = win ? ((win.globalMax - win.globalMin) / 200 || 0.01) : 0.01;
            return (
              <div
                key={volume.id}
                className={`nc-layer-item ${draggingLayerId === volume.id ? 'opacity-[0.55]' : ''} ${dropClass}`}
                data-testid="viewer-layer-item"
                data-layer-id={volume.id}
                data-layer-type={typeName}
                onDragOver={(event) => onLayerDragOver(event, volume)}
                onDragLeave={() => onLayerDragLeave(volume.id)}
                onDrop={(event) => onLayerDrop(event, volume)}
              >
                <div className="nc-viewer-layer-row">
                  <button
                    type="button"
                    className={`nc-viewer-layer-visibility ${volume.visible ? layerAccent(typeName) : 'text-[var(--nc-tx-faint)]'}`}
                    onClick={() => onUpdateVolume(volume.id, { visible: !volume.visible })}
                    aria-label={`${volume.visible ? 'Hide' : 'Show'} ${volume.name}`}
                    title={volume.visible ? 'Hide layer' : 'Show layer'}
                    data-testid="viewer-layer-visibility"
                  >
                    {volume.visible ? <Eye size={14} /> : <EyeOff size={14} />}
                  </button>
                  <div className="min-w-0 flex-1">
                    <button
                      type="button"
                      draggable
                      className="nc-layer-drag-handle flex w-full items-center gap-1 text-left"
                      onClick={() => onToggleExpandLayer(volume.id, typeName)}
                      onKeyDown={(event) => onLayerReorderKeyDown(event, volume, items)}
                      onDragStart={(event) => onLayerDragStart(volume.id, event)}
                      onDragEnd={onLayerDragEnd}
                      aria-expanded={isExpanded}
                      title="Click to toggle settings · drag to reorder · ↑/↓ to move"
                    >
                      <span className="truncate text-[var(--nc-tx)]">{volume.name}</span>
                      {isExpanded ? <ChevronDown size={11} className="shrink-0 text-[var(--nc-tx-dim)]" /> : <ChevronRight size={11} className="shrink-0 text-[var(--nc-tx-dim)]" />}
                    </button>
                    <div className="nc-mono truncate text-[11px] text-[var(--nc-tx-dim)]">{volume.filename}</div>
                  </div>
                </div>
                {isExpanded && (
                  <div className="border-b border-[var(--nc-border)] px-2 pb-2 pt-1 space-y-1.5">
                    {isReferenceVolume && (
                      <div className="nc-mono rounded border border-[var(--nc-interactive-border)] bg-[var(--nc-interactive-subtle)] px-2 py-1 text-[11px] text-[var(--nc-interactive)]">
                        Reference volume
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Opacity</span>
                      <LayerOpacityControl
                        volume={volume}
                        defaultOpacity={defaultOpacity}
                        onPreview={onPreviewVolumeOpacity}
                        onCommit={onCommitVolumeOpacity}
                      />
                    </div>
                    {showWindowing && (
                      <>
                        {win ? (
                          <>
                            <div className="flex items-center gap-2">
                              <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Min</span>
                              <input
                                type="range"
                                min={win.globalMin}
                                max={win.globalMax}
                                step={windowStep}
                                value={win.calMin}
                                onInput={(event) => onUpdateWindowing(volume.id, 'calMin', Number(event.currentTarget.value))}
                                onChange={(event) => onUpdateWindowing(volume.id, 'calMin', Number(event.currentTarget.value))}
                                onKeyDown={(event) => {
                                  const direction = event.key === 'ArrowRight' || event.key === 'ArrowUp' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -1 : 0;
                                  if (direction === 0) return;
                                  event.preventDefault();
                                  const nextMin = Math.max(win.globalMin, Math.min(win.globalMax, win.calMin + direction * windowStep));
                                  const adjustedMin = nextMin === win.calMax ? Math.max(win.globalMin, Math.min(win.globalMax, nextMin + direction * windowStep)) : nextMin;
                                  if (adjustedMin !== win.calMax) onUpdateWindowing(volume.id, 'calMin', adjustedMin);
                                }}
                                className="nc-viewer-layer-slider"
                                aria-label={`${volume.name} window minimum`}
                                data-testid="viewer-window-min"
                              />
                              <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(win.calMin)}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Max</span>
                              <input
                                type="range"
                                min={win.globalMin}
                                max={win.globalMax}
                                step={windowStep}
                                value={win.calMax}
                                onInput={(event) => onUpdateWindowing(volume.id, 'calMax', Number(event.currentTarget.value))}
                                onChange={(event) => onUpdateWindowing(volume.id, 'calMax', Number(event.currentTarget.value))}
                                onKeyDown={(event) => {
                                  const direction = event.key === 'ArrowRight' || event.key === 'ArrowUp' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? -1 : 0;
                                  if (direction === 0) return;
                                  event.preventDefault();
                                  const nextMax = Math.max(win.globalMin, Math.min(win.globalMax, win.calMax + direction * windowStep));
                                  const adjustedMax = nextMax === win.calMin ? Math.max(win.globalMin, Math.min(win.globalMax, nextMax + direction * windowStep)) : nextMax;
                                  if (adjustedMax !== win.calMin) onUpdateWindowing(volume.id, 'calMax', adjustedMax);
                                }}
                                className="nc-viewer-layer-slider"
                                aria-label={`${volume.name} window maximum`}
                                data-testid="viewer-window-max"
                              />
                              <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(win.calMax)}</span>
                            </div>
                          </>
                        ) : (
                          <div className="nc-mono text-[11px] italic text-[var(--nc-tx-dim)]">Loading volume bounds...</div>
                        )}
                        {colormapOptions.length > 0 && (
                          <div className="flex items-center gap-2">
                            <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Colormap</span>
                            <select
                              className="nc-mono nc-viewer-layer-select"
                              value={currentColormap}
                              onChange={(event) => onUpdateVolume(volume.id, { colormap: event.target.value })}
                              aria-label={`${volume.name} colormap`}
                            >
                              {colormapOptions.map((cm) => (
                                <option key={cm} value={cm}>{titleCaseColormap(cm)}</option>
                              ))}
                            </select>
                          </div>
                        )}
                      </>
                    )}
                    {showSurfaceDisplay && (
                      <>
                        <div className="flex items-center gap-2">
                          <span className="nc-mono w-12 shrink-0 text-[11px] text-[var(--nc-tx-dim)]">Display</span>
                          <select
                            className="nc-mono nc-viewer-layer-select"
                            value={surfaceColorMode}
                            disabled={!volume.visible}
                            onChange={(event) => onUpdateVolume(volume.id, { surfaceColorMode: event.target.value as SurfaceColorMode })}
                            aria-label={`${volume.name} surface display`}
                          >
                            {(Object.keys(SURFACE_COLOR_MODE_LABELS) as SurfaceColorMode[])
                              .filter((mode) => surfaceColorModeAvailable(volume, mode))
                              .map((mode) => (
                                <option key={mode} value={mode}>{SURFACE_COLOR_MODE_LABELS[mode]}</option>
                              ))}
                          </select>
                        </div>
                        {surfaceColorMode === 'curvature' && (
                          <>
                            <CurvatureThresholdControl
                              label="Green"
                              ariaLabel={`${volume.name} green curvature threshold`}
                              value={-curvatureNegativeThreshold(volume)}
                              onCommit={(value) => onUpdateVolume(volume.id, { curvatureNegativeThreshold: Math.abs(value) })}
                            />
                            <CurvatureThresholdControl
                              label="Red"
                              ariaLabel={`${volume.name} red curvature threshold`}
                              value={curvaturePositiveThreshold(volume)}
                              onCommit={(value) => onUpdateVolume(volume.id, { curvaturePositiveThreshold: value })}
                            />
                          </>
                        )}
                      </>
                    )}
                    {onRemoveVolume && (
                      <button
                        type="button"
                        className="nc-viewer-layer-close"
                        onClick={() => onRemoveVolume(volume.id)}
                        title={`Close ${volume.name}`}
                        aria-label={`Close ${volume.name}`}
                      >
                        <X size={12} className="shrink-0" />
                        <span>Close layer</span>
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );

  const renderDrawingTools = () => (
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
                disabled={groupedLayers.intensity.length === 0}
                title={groupedLayers.intensity.length === 0 ? 'Load an intensity volume first' : 'Empty drawing matching the active volume'}
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
          <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{Math.round(drawingSession.opacity * 100)}</span>
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
            <span className="nc-mono w-10 shrink-0 text-right text-[11px] text-[var(--nc-tx-dim)]">{drawingSession.brushSize}</span>
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

  return (
    <aside className="nc-panel relative flex shrink-0 flex-col overflow-hidden border-r" style={{ width: layerPanelWidth }}>
      <div className="nc-pane-header">
        <Layers size={12} />
        <span>Layers</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {renderLayerSection('intensity', groupedLayers.intensity)}
        {renderLayerSection('segmentation', groupedLayers.segmentation)}
        {renderDrawingTools()}
        {renderLayerSection('surface', groupedLayers.surface)}
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        className="nc-resize-handle nc-resize-handle-right"
        onMouseDown={onStartLayerPanelResize}
      />
    </aside>
  );
}
