import React, { useMemo, useState } from 'react';
import { ChevronRight, Eye, EyeOff } from 'lucide-react';
import { type LocationInfo } from './MriViewer';
import { isSurfaceLayer, type LayerType, type SurfaceColorMode, type Volume } from '../types';
import { resolveSurfaceLayerColorMode, SURFACE_COLOR_MODE_LABELS, surfaceColorModeAvailable } from '../utils/surfaceColors';

interface LayerControlProps {
    volumes: Volume[];
    onUpdateVolume: (id: string, updates: Partial<Volume>) => void;
    onReorderVolume: (sourceId: string, targetId: string, position: 'before' | 'after') => void;
    onRemoveVolume?: (id: string) => void;
    onOpenLayerPicker?: (type: LayerType) => void;
    canAddLayers?: boolean;
    location?: LocationInfo | null;
}

interface EditableNumberProps {
    value: number;
    min: number;
    max: number;
    step?: number;
    suffix?: string;
    ariaLabel: string;
    onCommit: (value: number) => void;
}

function EditableNumber({ value, min, max, step = 1, suffix = '', ariaLabel, onCommit }: EditableNumberProps) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(String(value));
    const inputRef = React.useRef<HTMLInputElement>(null);

    React.useEffect(() => {
        if (!editing) setDraft(String(value));
    }, [editing, value]);

    React.useEffect(() => {
        if (editing) {
            inputRef.current?.focus();
            inputRef.current?.select();
        }
    }, [editing]);

    const commit = () => {
        const parsed = draft.trim() === '' ? Number.NaN : Number(draft);
        if (Number.isFinite(parsed)) {
            const stepped = Math.round(parsed / step) * step;
            const clamped = Math.max(min, Math.min(max, stepped));
            onCommit(Number(clamped.toFixed(4)));
        }
        setEditing(false);
    };

    const cancel = () => {
        setDraft(String(value));
        setEditing(false);
    };

    if (editing) {
        return (
            <input
                ref={inputRef}
                type="text"
                inputMode="decimal"
                aria-label={ariaLabel}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onBlur={commit}
                onClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => {
                    if (event.key === 'Enter') commit();
                    if (event.key === 'Escape') cancel();
                }}
                className="nc-layer-value nc-mono h-[18px] w-14 shrink-0 rounded border border-[var(--nc-border)] bg-[var(--nc-bg-surface)] px-1 text-right text-[var(--nc-tx)] outline-none"
            />
        );
    }

    return (
        <button
            type="button"
            aria-label={`${ariaLabel}: ${value}${suffix}`}
            onClick={(event) => {
                event.stopPropagation();
                setEditing(true);
            }}
            className="nc-layer-value nc-mono h-[18px] w-[42px] shrink-0 rounded border border-transparent bg-transparent px-1 text-right text-[var(--nc-tx-muted)] hover:border-[var(--nc-border)] hover:bg-[var(--nc-bg-surface)]"
        >
            {value}{suffix}
        </button>
    );
}

function layerTypeOf(volume: Volume): LayerType {
    return volume.type ?? 'intensity';
}

export const LayerControl: React.FC<LayerControlProps> = ({ volumes, onUpdateVolume, onReorderVolume, onOpenLayerPicker, canAddLayers = false, location }) => {
    const [expandedLayers, setExpandedLayers] = useState<Set<string>>(new Set());
    const [draggingLayerId, setDraggingLayerId] = useState<string | null>(null);
    const [dragTarget, setDragTarget] = useState<{ id: string; position: 'before' | 'after' } | null>(null);
    const intensityLayers = useMemo(() => volumes.filter(v => layerTypeOf(v) === 'intensity'), [volumes]);
    const segmentationLayers = useMemo(() => volumes.filter(v => layerTypeOf(v) === 'segmentation'), [volumes]);
    const surfaceLayers = useMemo(() => volumes.filter(isSurfaceLayer), [volumes]);

    const collapseLayerControls = (ids: string[]) => {
        if (ids.length === 0) return;
        setExpandedLayers((current) => {
            const next = new Set(current);
            ids.forEach((id) => next.delete(id));
            return next.size === current.size ? current : next;
        });
    };

    const handleToggleLayer = (id: string, currentlyChecked: boolean) => {
        const nextVisible = !currentlyChecked;

        if (!nextVisible) collapseLayerControls([id]);
        onUpdateVolume(id, { visible: nextVisible });
    };

    const sameSectionLayer = (sourceId: string | null, target: Volume) => {
        if (!sourceId || sourceId === target.id) return false;
        const source = volumes.find((volume) => volume.id === sourceId);
        return Boolean(source && layerTypeOf(source) === layerTypeOf(target));
    };

    const handleDragOver = (event: React.DragEvent<HTMLDivElement>, target: Volume) => {
        if (!sameSectionLayer(draggingLayerId, target)) return;
        event.preventDefault();
        const rect = event.currentTarget.getBoundingClientRect();
        const position = event.clientY < rect.top + rect.height / 2 ? 'before' : 'after';
        setDragTarget({ id: target.id, position });
    };

    const handleDrop = (event: React.DragEvent<HTMLDivElement>, target: Volume) => {
        event.preventDefault();
        const sourceId = draggingLayerId ?? event.dataTransfer.getData('text/plain');
        if (sameSectionLayer(sourceId, target)) {
            onReorderVolume(sourceId, target.id, dragTarget?.id === target.id ? dragTarget.position : 'before');
        }
        setDraggingLayerId(null);
        setDragTarget(null);
    };

    const handleReorderKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, layer: Volume, sectionLayers: Volume[]) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        event.preventDefault();
        event.stopPropagation();
        const index = sectionLayers.findIndex((candidate) => candidate.id === layer.id);
        if (event.key === 'ArrowUp' && index > 0) {
            onReorderVolume(layer.id, sectionLayers[index - 1].id, 'before');
        }
        if (event.key === 'ArrowDown' && index >= 0 && index < sectionLayers.length - 1) {
            onReorderVolume(layer.id, sectionLayers[index + 1].id, 'after');
        }
    };

    const toggleExpanded = (id: string) => {
        setExpandedLayers((current) => {
            const next = new Set(current);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
            }
            return next;
        });
    };

    const renderControlRow = (
        label: string,
        value: number,
        min: number,
        max: number,
        step: number,
        accentClass: string,
        onChange: (value: number) => void,
        disabled: boolean,
        ariaLabel: string,
        displayValue: number,
        suffix = '',
        numberMin = min,
        numberMax = max,
        numberStep = step,
        onNumberCommit = onChange,
    ) => (
        <div className="flex items-center gap-2">
            <label className="nc-layer-control-label nc-mono w-[78px] shrink-0 whitespace-nowrap leading-none text-[var(--nc-tx-dim)]">{label}</label>
            <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={value}
                disabled={disabled}
                aria-label={ariaLabel}
                onChange={(event) => onChange(parseFloat(event.target.value))}
                className={`h-1 min-w-0 flex-1 cursor-pointer outline-none focus:outline-none focus-visible:outline-none ${accentClass}`}
            />
            <EditableNumber
                value={displayValue}
                min={numberMin}
                max={numberMax}
                step={numberStep}
                suffix={suffix}
                ariaLabel={ariaLabel}
                onCommit={onNumberCommit}
            />
        </div>
    );

    const renderLayerItem = (v: Volume, sectionLayers: Volume[]) => {
        const isSegmentation = v.type === 'segmentation';
        const isSurface = isSurfaceLayer(v);
        const expanded = expandedLayers.has(v.id);
        const brightness = isSurface ? 0 : v.brightness ?? 0;
        const contrast = isSurface ? 1.0 : v.contrast ?? 1.0;
        const opacity = v.opacity ?? (isSurface ? 1 : 0.7);
        const surfaceColorMode = isSurface ? resolveSurfaceLayerColorMode(v) : 'solid';
        const dropClass = dragTarget?.id === v.id ? `nc-layer-drop-${dragTarget.position}` : '';
        const layerFilename = v.filename;
        const displayName = v.name;

        return (
            <div
                key={v.id}
                className={`layer-item nc-layer-item border-b border-[rgba(100,100,140,0.18)] last:border-b-0 ${draggingLayerId === v.id ? 'opacity-55' : ''} ${dropClass}`}
                onDragOver={(event) => handleDragOver(event, v)}
                onDragLeave={() => {
                    if (dragTarget?.id === v.id) setDragTarget(null);
                }}
                onDrop={(event) => handleDrop(event, v)}
            >
                <div className="flex w-full items-center gap-2 text-left transition hover:bg-[var(--nc-row-hover)]" title={layerFilename}>
                    <button
                        type="button"
                        draggable
                        className={`nc-layer-drag-handle grid h-5 w-4 shrink-0 place-items-center rounded ${isSurface ? 'text-[var(--nc-warning)]' : isSegmentation ? 'text-[var(--nc-success)]' : 'text-[var(--nc-interactive)]'} ${v.visible ? '' : 'opacity-45'}`}
                        aria-label={`Reorder ${displayName}`}
                        title="Drag to reorder"
                        onClick={(event) => event.stopPropagation()}
                        onKeyDown={(event) => handleReorderKeyDown(event, v, sectionLayers)}
                        onDragStart={(event) => {
                            event.stopPropagation();
                            setDraggingLayerId(v.id);
                            event.dataTransfer.effectAllowed = 'move';
                            event.dataTransfer.setData('text/plain', v.id);
                        }}
                        onDragEnd={() => {
                            setDraggingLayerId(null);
                            setDragTarget(null);
                        }}
                    >
                        <span className="h-1.5 w-1.5 rounded-full bg-current" />
                    </button>
                    <button
                        type="button"
                        className="flex min-w-0 flex-1 items-center gap-2 py-1 text-left"
                        onClick={() => handleToggleLayer(v.id, v.visible)}
                        aria-label={`${v.visible ? 'Hide' : 'Show'} ${displayName}`}
                        title={layerFilename}
                    >
                        <span className={`nc-layer-text nc-mono min-w-0 flex-1 truncate ${v.visible ? 'text-[var(--nc-tx)]' : 'text-[var(--nc-tx-faint)]'}`}>
                            {displayName}
                        </span>
                        <span className="grid h-5 w-5 place-items-center rounded text-[var(--nc-tx-dim)]">
                            {v.visible ? <Eye size={12} /> : <EyeOff size={12} />}
                        </span>
                    </button>
                    <button
                        type="button"
                        className={`grid h-5 w-5 shrink-0 place-items-center rounded text-[var(--nc-tx-faint)] transition hover:text-[var(--nc-tx)] ${expanded ? 'rotate-90' : ''} ${v.visible ? '' : 'pointer-events-none opacity-0'}`}
                        onClick={(event) => {
                            event.stopPropagation();
                            toggleExpanded(v.id);
                        }}
                        aria-expanded={expanded}
                        aria-hidden={!v.visible}
                        tabIndex={v.visible ? 0 : -1}
                        title={expanded ? 'Hide layer controls' : 'Show layer controls'}
                    >
                        <ChevronRight size={12} />
                    </button>
                </div>

                {expanded && (
                    <div className="space-y-1.5 bg-[var(--nc-bg-deep)] px-3 py-2">
                        {isSegmentation || isSurface ? (
                            <>
                                {renderControlRow(
                                    'opacity',
                                    opacity,
                                    0,
                                    1,
                                    0.01,
                                    isSurface ? 'accent-[var(--nc-warning)]' : 'accent-[var(--nc-success)]',
                                    (value) => onUpdateVolume(v.id, { opacity: value }),
                                    !v.visible,
                                    `${v.name} opacity percent`,
                                    Math.round(opacity * 100),
                                    '%',
                                    0,
                                    100,
                                    1,
                                    (value) => onUpdateVolume(v.id, { opacity: value / 100 }),
                                )}
                                {isSurface && (
                                    <div className="flex items-center gap-2">
                                        <label className="nc-layer-control-label nc-mono w-[78px] shrink-0 whitespace-nowrap leading-none text-[var(--nc-tx-dim)]">color</label>
                                        <select
                                            value={surfaceColorMode}
                                            disabled={!v.visible}
                                            aria-label={`${v.name} surface coloring`}
                                            onChange={(event) => onUpdateVolume(v.id, { surfaceColorMode: event.target.value as SurfaceColorMode })}
                                            className="nc-layer-select nc-mono min-w-0 flex-1 rounded border border-[var(--nc-border)] bg-[var(--nc-bg-surface)] px-1.5 py-1 text-[var(--nc-tx-muted)] outline-none"
                                        >
                                            {(Object.keys(SURFACE_COLOR_MODE_LABELS) as SurfaceColorMode[])
                                                .filter((mode) => surfaceColorModeAvailable(v, mode))
                                                .map((mode) => (
                                                    <option key={mode} value={mode}>{SURFACE_COLOR_MODE_LABELS[mode]}</option>
                                                ))}
                                        </select>
                                    </div>
                                )}
                            </>
                        ) : (
                            <>
                                {renderControlRow(
                                    'opacity',
                                    opacity,
                                    0,
                                    1,
                                    0.01,
                                    'accent-[var(--nc-interactive)]',
                                    (value) => onUpdateVolume(v.id, { opacity: value }),
                                    !v.visible,
                                    `${v.name} opacity percent`,
                                    Math.round(opacity * 100),
                                    '%',
                                    0,
                                    100,
                                    1,
                                    (value) => onUpdateVolume(v.id, { opacity: value / 100 }),
                                )}
                                {renderControlRow(
                                    'brightness',
                                    brightness,
                                    -100,
                                    100,
                                    1,
                                    'accent-[var(--nc-interactive)]',
                                    (value) => onUpdateVolume(v.id, { brightness: value }),
                                    !v.visible,
                                    `${v.name} brightness`,
                                    brightness,
                                )}
                                {renderControlRow(
                                    'contrast',
                                    Math.round(contrast * 100),
                                    0,
                                    200,
                                    1,
                                    'accent-[var(--nc-interactive)]',
                                    (value) => onUpdateVolume(v.id, { contrast: value / 100 }),
                                    !v.visible,
                                    `${v.name} contrast percent`,
                                    Math.round(contrast * 100),
                                    '%',
                                    0,
                                    200,
                                    1,
                                    (value) => onUpdateVolume(v.id, { contrast: value / 100 }),
                                )}
                            </>
                        )}
                    </div>
                )}
            </div>
        );
    };

    const isAnySegmentationVisible = segmentationLayers.some(v => v.visible);
    const posX = location ? Math.round(location.vox[0]) : 0;
    const posY = location ? Math.round(location.vox[1]) : 0;
    const posZ = location ? Math.round(location.vox[2]) : 0;
    const labelIndex = location?.labelIndex ?? 0;
    const labelName = location?.labelName ?? 'Unknown';
    const labelColor = location?.labelColor;
    const labelDisplayName = `${labelName} (${labelIndex})`;

    const renderSectionHeading = (label: string, type: LayerType) => (
        <div className="mb-1.5 flex items-center gap-2">
            <h3 className="nc-layer-heading min-w-0 flex-1">{label}</h3>
            <button
                type="button"
                className="chat-clear-button shrink-0"
                onClick={() => onOpenLayerPicker?.(type)}
                disabled={!canAddLayers || !onOpenLayerPicker}
                title={`Load ${label.toLowerCase()} from the case directory`}
                aria-label={`Load ${label.toLowerCase()} from the case directory`}
            >
                <span aria-hidden="true">+</span>
            </button>
        </div>
    );

    return (
        <div className="nc-layer-panel flex h-full flex-col gap-3 text-[var(--nc-tx)]">
            <div>
                {renderSectionHeading('Intensity Volumes', 'intensity')}
                <div className="border-t border-[var(--nc-border)]">
                    {intensityLayers.length > 0 ? (
                        intensityLayers.map((layer) => renderLayerItem(layer, intensityLayers))
                    ) : (
                        <div className="nc-layer-text nc-mono py-2 italic text-[var(--nc-tx-faint)]">No intensity layers available</div>
                    )}
                </div>
            </div>

            <div>
                {renderSectionHeading('Segmentation Volumes', 'segmentation')}
                <div className="border-t border-[var(--nc-border)]">
                    {segmentationLayers.length > 0 ? (
                        segmentationLayers.map((layer) => renderLayerItem(layer, segmentationLayers))
                    ) : (
                        <div className="nc-layer-text nc-mono py-2 italic text-[var(--nc-tx-faint)]">No segmentation layers available</div>
                    )}
                </div>
            </div>

            <div>
                {renderSectionHeading('Surface Meshes', 'surface')}
                <div className="border-t border-[var(--nc-border)]">
                    {surfaceLayers.length > 0 ? (
                        surfaceLayers.map((layer) => renderLayerItem(layer, surfaceLayers))
                    ) : (
                        <div className="nc-layer-text nc-mono py-2 italic text-[var(--nc-tx-faint)]">No surface layers available</div>
                    )}
                </div>
            </div>

            <section className="nc-position-card mt-auto" aria-label="Current cursor position">
                <div className="nc-position-card-header">
                    <h3 aria-label="Cursor Position">
                        <span className="nc-position-title-full">Cursor Position</span>
                        <span className="nc-position-title-short" aria-hidden="true">Cursor</span>
                    </h3>
                    <span className="nc-position-coordinates nc-mono">{posX},{posY},{posZ}</span>
                </div>

                {isAnySegmentationVisible && labelIndex > 0 && (
                    <div className="nc-position-region">
                        <span
                            className="nc-position-region-dot"
                            style={labelColor ? { backgroundColor: `rgb(${labelColor.join(',')})` } : undefined}
                            aria-hidden="true"
                        />
                        <span className="nc-position-region-name">{labelDisplayName}</span>
                    </div>
                )}
            </section>
        </div>
    );
};
