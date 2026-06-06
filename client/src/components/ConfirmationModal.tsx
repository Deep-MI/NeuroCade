import React, { useState } from 'react';
import { AlertCircle, X, ChevronDown, ChevronUp } from 'lucide-react';
import type { FastSurferParams, OutputVolume } from '../types';
import { layerDisplayName } from '../utils/layerAliases';

interface ConfirmationModalProps {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: (params: FastSurferParams) => Promise<void> | void;
    title: string;
    message: string;
    defaultCaseName?: string;
    inputOptions: OutputVolume[];
    defaultInputArtifactId?: string | null;
}

export const ConfirmationModal: React.FC<ConfirmationModalProps> = ({
    isOpen,
    onClose,
    onConfirm,
    title,
    message,
    defaultCaseName = '',
    inputOptions,
    defaultInputArtifactId = null,
}) => {
    const [showOptions, setShowOptions] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [params, setParams] = useState<FastSurferParams>({
        input_artifact_id: '',
        seg_only: true,
        no_bias: false,
        no_cereb: false,
        no_asegdkt: false,
        no_hypothal: false,
        three_t: false,
        vox_size: '',
    });

    // Reset transient modal state whenever the dialog is reopened.
    React.useEffect(() => {
        setError(null);
        setLoading(false);
        if (isOpen) {
            const fallbackInputId = inputOptions.find((option) => option.id === defaultInputArtifactId)?.id
                ?? inputOptions[0]?.id
                ?? '';
            setParams(prev => ({ ...prev, input_artifact_id: fallbackInputId }));
        }
    }, [defaultInputArtifactId, inputOptions, isOpen]);

    if (!isOpen) return null;

    const toggleParam = (key: keyof Omit<FastSurferParams, 'input_artifact_id' | 'vox_size' | 'case_name'>) => {
        setParams(prev => {
            if (key === 'no_bias') {
                const noBiasEnabled = !prev.no_bias;
                return {
                    ...prev,
                    no_bias: noBiasEnabled,
                    no_cereb: noBiasEnabled ? true : prev.no_cereb,
                };
            }

            if (key === 'no_cereb' && prev.no_bias) {
                return prev;
            }

            return { ...prev, [key]: !prev[key] };
        });
    };

    const handleConfirm = async () => {
        try {
            setLoading(true);
            setError(null);
            if (!params.input_artifact_id) {
                throw new Error('Choose an input volume for FastSurfer');
            }
            await onConfirm({ ...params, case_name: defaultCaseName });
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err) || 'An error occurred';
            setError(msg);
            setLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/80 backdrop-blur-md" onClick={!loading ? onClose : undefined} />

            <div className="relative bg-slate-900/60 backdrop-blur-xl border border-white/10 rounded-2xl p-8 max-w-lg w-full shadow-[0_0_50px_rgba(0,0,0,0.5)] overflow-hidden max-h-[90vh] flex flex-col">
                {/* Glow effect */}
                <div className="absolute -top-24 -right-24 w-48 h-48 bg-primary/10 blur-[80px] rounded-full" />

                <button
                    onClick={onClose}
                    disabled={loading}
                    className="absolute top-4 right-4 p-1 text-secondary hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    <X size={20} />
                </button>

                <div className="flex flex-col items-center text-center mb-6">
                    <div className="w-16 h-16 bg-gold/10 rounded-full flex items-center justify-center mb-4 border border-gold/20">
                        <AlertCircle className="text-gold w-8 h-8" />
                    </div>

                    <h3 className="text-xl font-bold text-white mb-2 tracking-tight">{title}</h3>

                    <p className="text-slate-400 text-sm leading-relaxed">
                        {message}
                    </p>
                </div>

                <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar mb-6">
                    <div className="mb-4 text-left">
                        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                            Input Volume
                        </label>
                        <select
                            value={params.input_artifact_id}
                            onChange={(e) => setParams(prev => ({ ...prev, input_artifact_id: e.target.value }))}
                            disabled={loading || inputOptions.length === 0}
                            className="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-primary/50 transition-colors disabled:opacity-50"
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

                    <button
                        onClick={() => setShowOptions(!showOptions)}
                        className="flex items-center gap-2 text-slate-400 font-semibold text-sm mb-4 hover:opacity-80 transition-opacity"
                    >
                        {showOptions ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                        Advanced Options
                    </button>

                    {showOptions && (
                        <div className="space-y-4 bg-white/5 rounded-xl p-4 border border-white/5 animate-in fade-in slide-in-from-top-2 duration-200">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <input
                                        type="checkbox"
                                        checked={params.seg_only}
                                        onChange={() => toggleParam('seg_only')}
                                        className="w-4 h-4 rounded border-white/20 bg-black/40 text-primary focus:ring-primary/50"
                                    />
                                    <span className="text-sm text-slate-300 group-hover:text-white transition-colors">Segmentation Only</span>
                                </label>

                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <input
                                        type="checkbox"
                                        checked={params.no_bias}
                                        onChange={() => toggleParam('no_bias')}
                                        className="w-4 h-4 rounded border-white/20 bg-black/40 text-primary focus:ring-primary/50"
                                    />
                                    <span className="text-sm text-slate-300 group-hover:text-white transition-colors">No Biasfield</span>
                                </label>

                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <input
                                        type="checkbox"
                                        checked={params.no_bias || params.no_cereb}
                                        onChange={() => toggleParam('no_cereb')}
                                        disabled={params.no_bias}
                                        className="w-4 h-4 rounded border-white/20 bg-black/40 text-primary focus:ring-primary/50"
                                    />
                                    <span className="text-sm text-slate-300 group-hover:text-white transition-colors">No cerebellum subsegm. {params.no_bias ? '(required with No Biasfield)' : ''}</span>
                                </label>

                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <input
                                        type="checkbox"
                                        checked={params.no_asegdkt}
                                        onChange={() => toggleParam('no_asegdkt')}
                                        className="w-4 h-4 rounded border-white/20 bg-black/40 text-primary focus:ring-primary/50"
                                    />
                                    <span className="text-sm text-slate-300 group-hover:text-white transition-colors">No ASEG-DKT</span>
                                </label>

                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <input
                                        type="checkbox"
                                        checked={params.no_hypothal}
                                        onChange={() => toggleParam('no_hypothal')}
                                        className="w-4 h-4 rounded border-white/20 bg-black/40 text-primary focus:ring-primary/50"
                                    />
                                    <span className="text-sm text-slate-300 group-hover:text-white transition-colors">No Hypothalamus</span>
                                </label>

                                <label className="flex items-center gap-3 cursor-pointer group">
                                    <input
                                        type="checkbox"
                                        checked={params.three_t}
                                        onChange={() => toggleParam('three_t')}
                                        className="w-4 h-4 rounded border-white/20 bg-black/40 text-primary focus:ring-primary/50"
                                    />
                                    <span className="text-sm text-slate-300 group-hover:text-white transition-colors">3T Optimized</span>
                                </label>
                            </div>

                            <div className="pt-2 border-t border-white/5 mt-2">
                                <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                                    Voxel Size (Optional)
                                </label>
                                <input
                                    type="text"
                                    value={params.vox_size}
                                    onChange={(e) => setParams(prev => ({ ...prev, vox_size: e.target.value }))}
                                    placeholder="FastSurfer default (min)"
                                    className="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-primary/50 transition-colors"
                                />
                            </div>
                        </div>
                    )}
                </div>

                {error && (
                    <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-3">
                        <AlertCircle className="text-red-500 w-5 h-5 shrink-0" />
                        <span className="text-red-200 text-sm font-medium">{error}</span>
                    </div>
                )}

                <div className="flex gap-4 w-full pt-4 border-t border-white/5">
                    <button
                        onClick={onClose}
                        disabled={loading}
                        className="flex-1 px-4 py-3 rounded-xl bg-white/5 hover:bg-white/10 text-slate-300 font-semibold transition-all border border-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={() => void handleConfirm()}
                        disabled={loading}
                        className="flex-1 px-4 py-3 rounded-xl bg-primary text-white font-bold hover:bg-accent transition-all shadow-[0_4px_20px_rgba(14,165,233,0.3)] hover:scale-[1.02] active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:scale-100 flex justify-center items-center gap-2"
                    >
                        {loading && (
                            <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                        )}
                        {loading ? 'Starting...' : 'Begin Run'}
                    </button>
                </div>
            </div>
        </div>
    );
};
