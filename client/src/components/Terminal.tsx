import React, { useEffect, useRef, useMemo } from 'react';
import { X, Activity } from 'lucide-react';

interface TerminalProps {
    isVisible: boolean;
    onClose: () => void;
    logs: string;
    status: string;
}

/** Match tqdm-style output: "  5%|███       | 12/256 [00:01<00:02, 95.29batch/s]" */
const TQDM_REGEX = /^\s*(\d+)%\|[^|]*\|\s*(\d+)\/(\d+)\s*\[([^\]]*)\]/;

interface TqdmInfo {
    percent: number;
    current: number;
    total: number;
    timing: string;
}

function parseTqdmLine(line: string): TqdmInfo | null {
    const match = TQDM_REGEX.exec(line);
    if (!match) return null;
    return {
        percent: parseInt(match[1], 10),
        current: parseInt(match[2], 10),
        total: parseInt(match[3], 10),
        timing: match[4],
    };
}

const ProgressBar: React.FC<{ info: TqdmInfo; lineNumber: number }> = ({ info, lineNumber }) => {
    const isComplete = info.percent >= 100;
    const barColor = isComplete
        ? 'bg-emerald-500'
        : 'bg-sky-500';
    const barGlow = isComplete
        ? 'shadow-[0_0_8px_rgba(16,185,129,0.4)]'
        : 'shadow-[0_0_8px_rgba(14,165,233,0.3)]';

    return (
        <div className="py-1 px-2 rounded-sm border-l-2 border-sky-500/50 bg-sky-500/5 hover:bg-white/5 transition-colors">
            <div className="flex items-center gap-2">
                <span className="opacity-20 mr-1 select-none tabular-nums text-slate-400">
                    {lineNumber.toString().padStart(4, '0')}
                </span>
                <span className={`text-[10px] font-bold tabular-nums w-[3ch] text-right ${isComplete ? 'text-emerald-400' : 'text-sky-400'}`}>
                    {info.percent}%
                </span>
                <div className={`flex-1 h-2.5 bg-white/5 rounded-full overflow-hidden ${barGlow}`}>
                    <div
                        className={`h-full rounded-full ${barColor} transition-all duration-500 ease-out`}
                        style={{ width: `${info.percent}%` }}
                    />
                </div>
                <span className="text-[9px] text-slate-500 tabular-nums whitespace-nowrap">
                    {info.current}/{info.total}
                </span>
                <span className="text-[9px] text-slate-600 tabular-nums whitespace-nowrap">
                    {info.timing}
                </span>
            </div>
        </div>
    );
};

export const Terminal: React.FC<TerminalProps> = ({ isVisible, onClose, logs, status }) => {
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [logs, isVisible]);

    const lines = useMemo(() => logs.split('\n'), [logs]);

    if (!isVisible) return null;

    return (
        <div
            data-testid="terminal-panel"
            className="fixed right-0 top-0 bottom-0 w-[400px] bg-black/95 border-l border-white/10 flex flex-col shadow-[-10px_0_30px_rgba(0,0,0,0.5)] z-50 animate-in slide-in-from-right duration-300"
        >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/5 bg-white/5 backdrop-blur-md">
                <div className="flex items-center gap-2">
                    <div className="p-1.5 bg-primary/20 rounded-md">
                        <Activity size={14} className="text-primary animate-pulse" />
                    </div>
                    <div>
                        <h3 className="text-[10px] font-bold uppercase tracking-widest text-slate-200">Output</h3>
                        <p className="text-[8px] text-slate-500 font-mono">Real-time pipeline logs</p>
                    </div>
                </div>
                <button
                    onClick={onClose}
                    className="p-1.5 text-slate-400 hover:text-white hover:bg-white/10 rounded-full transition-all"
                    title="Close Panel"
                >
                    <X size={16} />
                </button>
            </div>

            {/* Log Stream */}
            <div
                ref={scrollRef}
                data-testid="terminal-content"
                className="flex-1 p-4 font-mono text-[10px] leading-normal overflow-y-auto custom-scrollbar bg-black/40"
            >
                <div className="space-y-1">
                    {lines.map((line, i) => {
                        const tqdm = parseTqdmLine(line);
                        if (tqdm) {
                            return <ProgressBar key={i} info={tqdm} lineNumber={i + 1} />;
                        }

                        const isError = line.includes('ERROR') || line.includes('CRITICAL') || line.includes('--- STDERR ---') || line.includes('stderr');
                        const isWarning = line.includes('WARNING');
                        const isInfo = line.includes('INFO') || line.includes('SUCCESS');

                        return (
                            <div key={i} className={`
                                py-0.5 px-2 rounded-sm transition-colors border-l-2
                                ${isError ? 'text-rose-400 bg-rose-500/5 border-rose-500/50' :
                                    isWarning ? 'text-amber-400 bg-amber-500/5 border-amber-500/50' :
                                        isInfo ? 'text-emerald-400 bg-emerald-500/5 border-emerald-500/50' :
                                            'text-slate-400 border-transparent'}
                                hover:bg-white/5
                            `}>
                                <span className="opacity-20 mr-3 select-none tabular-nums">{(i + 1).toString().padStart(4, '0')}</span>
                                {line}
                            </div>
                        );
                    })}
                    {logs.length > 0 && (
                        <div className="flex items-center gap-2 px-2 mt-4 text-[9px] text-primary/50 font-bold uppercase">
                            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-ping" />
                        </div>
                    )}
                </div>
            </div>

            {/* Footer Status */}
            <div className="px-4 py-2 bg-white/5 border-t border-white/5 text-[9px] font-mono text-slate-500 flex justify-between">
                <span>FastSurfer v2.4.2</span>
                <span className={status === 'running' ? "animate-pulse" : ""}>
                    {status ? (status.charAt(0).toUpperCase() + status.slice(1)) : 'Idle'}
                </span>
            </div>
        </div>
    );
};
