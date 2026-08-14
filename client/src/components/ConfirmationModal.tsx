import React, { useEffect, useState } from 'react';
import { AlertCircle, LoaderCircle, X } from 'lucide-react';

import type { AnalysisRunParams, AnalysisToolSummary, OutputVolume } from '../types';
import { layerDisplayName } from '../utils/layerAliases';

interface ConfirmationModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (params: AnalysisRunParams) => Promise<void> | void;
  tool: AnalysisToolSummary | null;
  message: string;
  inputOptions: OutputVolume[];
  defaultInputArtifactId?: string | null;
}

export const ConfirmationModal: React.FC<ConfirmationModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  tool,
  message,
  inputOptions,
  defaultInputArtifactId = null,
}) => {
  const [inputArtifactIds, setInputArtifactIds] = useState<string[]>([]);
  const [outputNames, setOutputNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !tool) return;
    const fallback = inputOptions.find((option) => option.id === defaultInputArtifactId)?.id
      ?? inputOptions[0]?.id
      ?? '';
    setInputArtifactIds(tool.inputs.map(() => fallback));
    setOutputNames(Object.fromEntries(tool.outputs.map((output) => [output.name, output.name])));
    setError(null);
    setLoading(false);
  }, [defaultInputArtifactId, inputOptions, isOpen, tool]);

  if (!isOpen || !tool) return null;

  const setInput = (index: number, artifactId: string) => {
    setInputArtifactIds((current) => current.map((value, itemIndex) => itemIndex === index ? artifactId : value));
  };

  const setOutputName = (outputName: string, displayName: string) => {
    setOutputNames((current) => ({ ...current, [outputName]: displayName }));
  };

  const handleConfirm = async () => {
    try {
      setLoading(true);
      setError(null);
      if (inputArtifactIds.length !== tool.inputs.length || inputArtifactIds.some((value) => !value)) {
        throw new Error('Choose a file for every workflow input.');
      }
      if (tool.outputs.some((output) => !outputNames[output.name]?.trim())) {
        throw new Error('Choose a display name for every workflow output.');
      }
      await onConfirm({
        tool_id: tool.id,
        input_artifact_ids: inputArtifactIds,
        output_name_overrides: Object.fromEntries(
          tool.outputs
            .map((output) => [output.name, outputNames[output.name].trim()] as const)
            .filter(([outputName, displayName]) => displayName !== outputName),
        ),
      });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err) || 'An error occurred');
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/80 backdrop-blur-md" onClick={!loading ? onClose : undefined} />
      <div
        className="relative flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-slate-900/60 p-8 shadow-[0_0_50px_rgba(0,0,0,0.5)] backdrop-blur-xl"
        aria-busy={loading}
      >
        <button
          type="button"
          onClick={onClose}
          disabled={loading}
          className="absolute right-4 top-4 p-1 text-[var(--nc-tx-dim)] transition-colors hover:text-white disabled:opacity-50"
        >
          <X size={20} />
        </button>
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-full border border-[var(--nc-warning-border)] bg-[var(--nc-warning-bg)]">
            <AlertCircle className="h-8 w-8 text-[var(--nc-warning)]" />
          </div>
          <h3 className="mb-2 text-xl font-bold tracking-tight text-white">Start {tool.label}</h3>
          <p className="text-sm leading-relaxed text-slate-400">{tool.description}</p>
          <p className="mt-2 text-xs leading-relaxed text-slate-500">{message}</p>
        </div>
        <div className="mb-6 flex-1 space-y-4 overflow-y-auto pr-2">
          {tool.inputs.map((input, index) => (
            <div key={input.name} className="text-left">
              <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-slate-500">
                {input.name.replaceAll('_', ' ')}
              </label>
              <p className="mb-2 text-xs text-slate-500">{input.description}</p>
              <select
                value={inputArtifactIds[index] ?? ''}
                onChange={(event) => setInput(index, event.target.value)}
                disabled={loading || inputOptions.length === 0}
                className="nc-select nc-select-dark w-full rounded-lg border border-white/10 px-3 py-2 text-sm focus:border-[var(--nc-interactive)] focus:outline-none disabled:opacity-50"
              >
                {inputOptions.length === 0 ? (
                  <option value="">No intensity volumes available</option>
                ) : inputOptions.map((option) => (
                  <option key={option.id ?? option.filename} value={option.id ?? ''}>
                    {layerDisplayName(option)}
                  </option>
                ))}
              </select>
            </div>
          ))}
          {tool.outputs.map((output) => (
            <div key={output.name} className="text-left">
              <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-slate-500">
                Output display name
              </label>
              <input
                type="text"
                value={outputNames[output.name] ?? output.name}
                onChange={(event) => setOutputName(output.name, event.target.value)}
                disabled={loading}
                maxLength={255}
                aria-label={`Display name for ${output.name}`}
                className="nc-select nc-select-dark w-full rounded-lg border border-white/10 px-3 py-2 text-sm focus:border-[var(--nc-interactive)] focus:outline-none disabled:opacity-50"
              />
              <p className="mt-2 text-xs text-slate-500">
                {output.description} <code>{output.path}</code>
              </p>
            </div>
          ))}
        </div>
        {error && (
          <div className="mb-4 flex items-center gap-3 rounded-lg border border-red-500/20 bg-red-500/10 p-3">
            <AlertCircle className="h-5 w-5 shrink-0 text-red-500" />
            <span className="text-sm font-medium text-red-200">{error}</span>
          </div>
        )}
        <div className="flex w-full gap-4 border-t border-white/5 pt-4">
          <button type="button" onClick={onClose} disabled={loading} className="flex-1 rounded-xl border border-white/5 bg-white/5 px-4 py-3 font-semibold text-slate-300 hover:bg-white/10 disabled:opacity-50">
            Cancel
          </button>
          <button type="button" onClick={() => void handleConfirm()} disabled={loading} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-[var(--nc-interactive)] px-4 py-3 font-bold text-white hover:brightness-110 disabled:opacity-50">
            {loading && <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />}
            <span aria-live="polite">{loading ? 'Starting analysis…' : 'Start'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};
