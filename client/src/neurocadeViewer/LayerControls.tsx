import React, { useCallback, useEffect, useRef, useState } from 'react';

import type { Volume } from '../types';
import type { DrawingLabelOption } from './useNativeDrawingSession';
import { clampOpacity, parseEditableSliderValue } from './layerDisplay';

function formatRoundedSliderValue(value: number): string {
  return String(Math.round(value));
}

interface EditableSliderValueProps {
  value: number;
  min: number;
  max: number;
  step: number;
  ariaLabel: string;
  disabled?: boolean;
  constrainToSliderRange?: boolean;
  onCommit: (value: number) => void;
  format?: (value: number) => string;
}

export const EditableSliderValue = React.memo(function EditableSliderValue({
  value,
  min,
  max,
  step,
  ariaLabel,
  disabled = false,
  constrainToSliderRange = true,
  onCommit,
  format = String,
}: EditableSliderValueProps) {
  const editingRef = useRef(false);
  const [draft, setDraft] = useState(() => format(value));

  useEffect(() => {
    if (!editingRef.current) setDraft(format(value));
  }, [format, value]);

  const commit = (text: string) => {
    editingRef.current = false;
    const next = parseEditableSliderValue(text, min, max, constrainToSliderRange);
    if (next === null) {
      setDraft(format(value));
      return;
    }
    setDraft(format(next));
    onCommit(next);
  };

  return (
    <input
      type="number"
      min={constrainToSliderRange ? min : undefined}
      max={constrainToSliderRange ? max : undefined}
      step={step}
      value={draft}
      disabled={disabled}
      onChange={(event) => setDraft(event.currentTarget.value)}
      onFocus={(event) => {
        editingRef.current = true;
        event.currentTarget.select();
      }}
      onBlur={(event) => commit(event.currentTarget.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
      }}
      className="nc-viewer-slider-value nc-mono"
      aria-label={ariaLabel}
      title={constrainToSliderRange
        ? 'Click to type a value'
        : 'Click to type any finite value; the slider range stays unchanged'}
    />
  );
});

interface LayerOpacityControlProps {
  volume: Volume;
  defaultOpacity: number;
  onPreview: (id: string, opacity: number) => void;
  onCommit: (id: string, opacity: number) => void;
}

export const LayerOpacityControl = React.memo(function LayerOpacityControl({
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
      <EditableSliderValue
        value={Math.round(draftOpacity * 100)}
        min={0}
        max={100}
        step={1}
        ariaLabel={`${volume.name} opacity value`}
        onCommit={(percent) => {
          preview(percent / 100);
          commit();
        }}
        format={formatRoundedSliderValue}
      />
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

export const DrawingLabelControl = React.memo(function DrawingLabelControl({
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

export const CurvatureThresholdControl = React.memo(function CurvatureThresholdControl({
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
