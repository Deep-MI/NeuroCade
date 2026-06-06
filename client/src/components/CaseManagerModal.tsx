import React, { useState, useEffect } from 'react';
import { FolderOpen, X, Pencil, Trash2, Check, XCircle, Loader2 } from 'lucide-react';
import type { CaseSummary, StatusConfig } from '../types';
import { isRunActive } from '../constants';
import { getCaseNameValidationError } from '../utils/caseNames';

interface CaseManagerModalProps {
    isOpen: boolean;
    onClose: () => void;
    availableCases: CaseSummary[];
    activeCaseId: string | null;
    statusConfig: Record<string, StatusConfig>;
    onRename: (oldId: string, newTitle: string) => Promise<void>;
    onDelete: (caseId: string) => Promise<void>;
    onLoadCase: (caseId: string) => void;
}

export const CaseManagerModal: React.FC<CaseManagerModalProps> = ({
    isOpen,
    onClose,
    availableCases,
    activeCaseId,
    statusConfig,
    onRename,
    onDelete,
    onLoadCase,
}) => {
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editValue, setEditValue] = useState('');
    const [deletingId, setDeletingId] = useState<string | null>(null);
    const [loading, setLoading] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Reset transient state whenever the modal opens / closes
    useEffect(() => {
        if (isOpen) {
            setEditingId(null);
            setEditValue('');
            setDeletingId(null);
            setLoading(null);
            setError(null);
        }
    }, [isOpen]);

    if (!isOpen) return null;

    const startEditing = (caseItem: CaseSummary) => {
        setEditingId(caseItem.case_id);
        setEditValue(caseItem.subject_name);
        setDeletingId(null);
        setError(null);
    };

    const cancelEditing = () => {
        setEditingId(null);
        setEditValue('');
        setError(null);
    };

    const confirmRename = async (oldId: string) => {
        const trimmed = editValue.trim();
        const validationError = getCaseNameValidationError(trimmed);
        if (validationError) {
            setError(validationError);
            return;
        }
        // No-op: user didn't change the title.
        const currentCase = availableCases.find((entry) => entry.case_id === oldId);
        if (trimmed === currentCase?.subject_name) {
            cancelEditing();
            return;
        }
        try {
            setLoading(oldId);
            setError(null);
            await onRename(oldId, trimmed);
            setEditingId(null);
            setEditValue('');
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            setError(msg);
        } finally {
            setLoading(null);
        }
    };

    const confirmDelete = async (caseId: string) => {
        try {
            setLoading(caseId);
            setError(null);
            await onDelete(caseId);
            setDeletingId(null);
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            setError(msg);
        } finally {
            setLoading(null);
        }
    };

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/80 backdrop-blur-md" onClick={onClose} />

            <div className="relative bg-slate-900/60 backdrop-blur-xl border border-white/10 rounded-2xl p-8 max-w-xl w-full shadow-[0_0_50px_rgba(0,0,0,0.5)] overflow-hidden max-h-[90vh] flex flex-col">
                {/* Glow effect */}
                <div className="absolute -top-24 -right-24 w-48 h-48 bg-primary/10 blur-[80px] rounded-full" />

                <button
                    onClick={onClose}
                    className="absolute top-4 right-4 p-1 text-secondary hover:text-white transition-colors"
                >
                    <X size={20} />
                </button>

                <div className="flex flex-col items-center text-center mb-6">
                    <div className="w-16 h-16 bg-primary/10 rounded-full flex items-center justify-center mb-4 border border-primary/20">
                        <FolderOpen className="text-primary w-8 h-8" />
                    </div>
                    <h3 className="text-xl font-bold text-white mb-1 tracking-tight">Manage Cases</h3>
                    <p className="text-slate-400 text-sm">Rename, delete, or load your analysis cases</p>
                </div>

                {error && (
                    <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-3">
                        <XCircle className="text-red-500 w-5 h-5 shrink-0" />
                        <span className="text-red-200 text-sm font-medium">{error}</span>
                    </div>
                )}

                <div className="flex-1 overflow-y-auto custom-scrollbar space-y-2 min-h-0">
                    {availableCases.length === 0 && (
                        <div className="text-center py-12 text-slate-500 text-sm">
                            No cases found. Upload an MRI file to get started.
                        </div>
                    )}
                    {availableCases.map((caseItem) => {
                        const sc = statusConfig[caseItem.status] ?? statusConfig.unknown;
                        const isActive = caseItem.case_id === activeCaseId;
                        const locked = isRunActive(caseItem.status);
                        const isEditing = editingId === caseItem.case_id;
                        const isDeleting = deletingId === caseItem.case_id;
                        const isLoading = loading === caseItem.case_id;

                        return (
                            <div
                                key={caseItem.case_id}
                                className={`group rounded-xl border transition-all ${
                                    isActive
                                        ? 'bg-primary/10 border-primary/30'
                                        : 'bg-white/5 border-white/5 hover:border-white/10'
                                }`}
                            >
                                <div className="flex items-center gap-3 px-4 py-3">
                                    {/* Name / inline edit */}
                                    <div className="flex-1 min-w-0">
                                        {isEditing ? (
                                            <div className="flex items-center gap-2">
                                                <input
                                                    data-testid={`manage-case-rename-input-${caseItem.case_id}`}
                                                    type="text"
                                                    value={editValue}
                                                    onChange={(e) => setEditValue(e.target.value)}
                                                    onKeyDown={(e) => {
                                                        if (e.key === 'Enter') void confirmRename(caseItem.case_id);
                                                        if (e.key === 'Escape') cancelEditing();
                                                    }}
                                                    autoFocus
                                                    className="flex-1 bg-black/40 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-primary/50 transition-colors"
                                                />
                                                <button
                                                    data-testid={`manage-case-rename-confirm-${caseItem.case_id}`}
                                                    onClick={() => void confirmRename(caseItem.case_id)}
                                                    disabled={isLoading}
                                                    className="p-1.5 text-green-400 hover:text-green-300 transition-colors disabled:opacity-50"
                                                    title="Confirm rename"
                                                >
                                                    {isLoading ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
                                                </button>
                                                <button
                                                    onClick={cancelEditing}
                                                    disabled={isLoading}
                                                    className="p-1.5 text-slate-400 hover:text-white transition-colors disabled:opacity-50"
                                                    title="Cancel"
                                                >
                                                    <X size={16} />
                                                </button>
                                            </div>
                                        ) : isDeleting ? (
                                            <div className="flex items-center gap-2">
                                                <span className="text-sm text-red-300 font-medium">Delete &quot;{caseItem.subject_name}&quot;?</span>
                                                <button
                                                    onClick={() => void confirmDelete(caseItem.case_id)}
                                                    disabled={isLoading}
                                                    className="px-2.5 py-1 text-xs font-semibold bg-red-500/20 text-red-300 hover:bg-red-500/30 rounded-lg transition-colors disabled:opacity-50"
                                                >
                                                    {isLoading ? 'Deleting...' : 'Yes, Delete'}
                                                </button>
                                                <button
                                                    onClick={() => { setDeletingId(null); setError(null); }}
                                                    disabled={isLoading}
                                                    className="px-2.5 py-1 text-xs font-semibold bg-white/5 text-slate-300 hover:bg-white/10 rounded-lg transition-colors disabled:opacity-50"
                                                >
                                                    Cancel
                                                </button>
                                            </div>
                                        ) : (
                                            <button
                                                onClick={() => { onLoadCase(caseItem.case_id); onClose(); }}
                                                className="text-left w-full"
                                            >
                                                <div className="text-sm font-semibold text-white truncate">
                                                    <span data-testid={`manage-case-title-${caseItem.case_id}`}>
                                                        {caseItem.subject_name}
                                                    </span>
                                                    {isActive && (
                                                        <span className="ml-2 text-[10px] font-medium text-primary uppercase tracking-wider">Active</span>
                                                    )}
                                                </div>
                                                <div className="text-xs text-slate-500 flex items-center gap-2 mt-0.5">
                                                    <span>{new Date(caseItem.created_at * 1000).toLocaleDateString()}</span>
                                                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wider ${sc.badge}`}>
                                                        {sc.label}
                                                    </span>
                                                </div>
                                            </button>
                                        )}
                                    </div>

                                    {/* Action buttons (hidden during edit/delete modes) */}
                                    {!isEditing && !isDeleting && (
                                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                                            <button
                                                data-testid={`manage-case-rename-${caseItem.case_id}`}
                                                onClick={() => startEditing(caseItem)}
                                                disabled={locked}
                                                className="p-1.5 text-slate-400 hover:text-white transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                                title={locked ? 'Cannot rename while running' : 'Rename case'}
                                            >
                                                <Pencil size={14} />
                                            </button>
                                            <button
                                                onClick={() => { setDeletingId(caseItem.case_id); setEditingId(null); setError(null); }}
                                                disabled={locked}
                                                className="p-1.5 text-slate-400 hover:text-red-400 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                                                title={locked ? 'Cannot delete while running' : 'Delete case'}
                                            >
                                                <Trash2 size={14} />
                                            </button>
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>

                <div className="flex gap-4 w-full pt-4 mt-4 border-t border-white/5">
                    <button
                        onClick={onClose}
                        className="flex-1 px-4 py-3 rounded-xl bg-white/5 hover:bg-white/10 text-slate-300 font-semibold transition-all border border-white/5"
                    >
                        Close
                    </button>
                </div>
            </div>
        </div>
    );
};
