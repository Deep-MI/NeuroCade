import type React from 'react';
import { ChevronDown, ChevronRight, Eye, EyeOff, Layers, Plus, X } from 'lucide-react';

import { isSurfaceLayer, type LayerType, type SegmentationVolumeLayer, type SurfaceColorMode, type Volume } from '../types';
import { DrawingToolsPanel } from './DrawingToolsPanel';
import { layerDefaultOpacity } from './layerDisplay';
import {
  CurvatureThresholdControl,
  EditableSliderValue,
  LayerOpacityControl,
} from './LayerControls';
import { layerType } from './niivueLayers';
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
                              <EditableSliderValue
                                value={win.calMin}
                                min={win.globalMin}
                                max={win.globalMax}
                                step={windowStep}
                                constrainToSliderRange={false}
                                ariaLabel={`${volume.name} window minimum value`}
                                onCommit={(value) => {
                                  const adjusted = value === win.calMax
                                    ? value - windowStep
                                    : value;
                                  if (adjusted !== win.calMax) onUpdateWindowing(volume.id, 'calMin', adjusted);
                                }}
                              />
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
                              <EditableSliderValue
                                value={win.calMax}
                                min={win.globalMin}
                                max={win.globalMax}
                                step={windowStep}
                                constrainToSliderRange={false}
                                ariaLabel={`${volume.name} window maximum value`}
                                onCommit={(value) => {
                                  const adjusted = value === win.calMin
                                    ? value + windowStep
                                    : value;
                                  if (adjusted !== win.calMin) onUpdateWindowing(volume.id, 'calMax', adjusted);
                                }}
                              />
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

  return (
    <aside className="nc-panel relative flex shrink-0 flex-col overflow-hidden border-r" style={{ width: layerPanelWidth }}>
      <div className="nc-pane-header">
        <Layers size={12} />
        <span>Layers</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {renderLayerSection('intensity', groupedLayers.intensity)}
        {renderLayerSection('segmentation', groupedLayers.segmentation)}
        <DrawingToolsPanel
          intensityLayers={groupedLayers.intensity}
          segmentationLayers={groupedLayers.segmentation}
          canAddLayers={canAddLayers}
          drawingSession={drawingSession}
          drawingLabels={drawingLabels}
          canUndo={canUndo}
          onUpdateDrawingOptions={onUpdateDrawingOptions}
          onBeginBlankDrawing={onBeginBlankDrawing}
          onBeginDrawingFromSegmentation={onBeginDrawingFromSegmentation}
          onDrawUndo={onDrawUndo}
          onSaveDrawing={onSaveDrawing}
          onCloseDrawing={onCloseDrawing}
        />
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
