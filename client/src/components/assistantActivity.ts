import type { AssistantActivity } from '../types';

function readableToolName(name: string): string {
    return name.replace(/^tool_/, '').replaceAll('_', ' ');
}

const TOOL_ACTIVITY_MESSAGES: Record<string, string> = {
    tool_call: 'Assistant is preparing the workflow…',
    tool_search: 'Assistant is searching the workflow catalog…',
    tool_inspect: 'Assistant is inspecting workflow details…',
    tool_probe: 'Assistant is checking workflow documentation…',
    case_file_tree: 'Assistant is inspecting case files…',
    workspace_file_tree: 'Assistant is inspecting workspace files…',
    read: 'Assistant is reading a file…',
    search_text: 'Assistant is searching file contents…',
};

function formatBytes(value: number): string {
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let amount = value;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
        amount /= 1024;
        unit += 1;
    }
    return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

export function assistantActivityMessage(
    activity: AssistantActivity | null,
    streamConnected: boolean,
    modelFallback: string,
): string {
    if (activity?.kind === 'image') {
        const percent = activity.progress == null ? null : Math.round(activity.progress * 100);
        const bytes = activity.current_bytes != null && activity.total_bytes
            ? ` — ${formatBytes(activity.current_bytes)} / ${formatBytes(activity.total_bytes)}`
            : '';
        let message = `Downloading ${activity.label}${bytes}${percent == null ? '' : ` (${percent}%)`}…`;
        if (activity.phase === 'waiting') message = `Waiting to prepare ${activity.label}…`;
        if (activity.phase === 'preparing') message = `Preparing ${activity.label}…`;
        if (activity.phase === 'verifying') message = `Verifying ${activity.label}…`;
        if (activity.phase === 'extracting') {
            message = `Extracting ${activity.label}${bytes}${percent == null ? '' : ` (${percent}%)`}…`;
        }
        if (activity.phase === 'ready') message = `Prepared ${activity.label}`;
        if ((activity.stalled_seconds ?? 0) >= 30 && activity.process_active) {
            message += ` No progress update for ${activity.stalled_seconds}s; Docker is still active.`;
        }
        return message;
    }
    if (activity?.kind === 'workflow') {
        const device = activity.device ? ` on ${activity.device.toUpperCase()}` : '';
        return activity.blocking
            ? `Running ${activity.label}${device} — assistant is waiting…`
            : `${activity.label} is continuing in the background…`;
    }
    if (activity?.kind === 'tool') {
        return TOOL_ACTIVITY_MESSAGES[activity.label]
            ?? `Assistant is using ${readableToolName(activity.label)}…`;
    }
    return streamConnected
        ? modelFallback
        : 'Live updates disconnected — assistant is still working…';
}

export function assistantActivityProgress(activity: AssistantActivity | null): number | null {
    if (
        activity?.kind !== 'image'
        || activity.progress == null
        || !['downloading', 'extracting'].includes(activity.phase ?? '')
    ) return null;
    return Math.max(0, Math.min(1, activity.progress));
}
