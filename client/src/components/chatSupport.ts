import type {
    AssistantScope,
    ChatContentPart,
    ChatMessage,
    LocationInfo,
    MriSnapshots,
    ReasoningEntry,
    ToolCallEntry,
} from '../types';
import { reportClientError } from '../utils/api';
import { createUuid } from '../utils/randomUuid';

export const STATUS_MESSAGES = [
    'Assistant is working',
    'Assistant is figuring it out',
    'Assistant is thinking',
];
export const CHAT_REQUEST_TIMEOUT_MS = 300_000;
export const CHAT_REQUEST_TIMEOUT_SECONDS = CHAT_REQUEST_TIMEOUT_MS / 1000;
const VISION_COMMANDS = ['@sagittal', '@coronal', '@axial', '@mri'];

export interface ApiResponse {
    message: { content: string };
    turn_id?: string;
    tool_calls_log?: ToolCallEntry[];
    approval_request?: AssistantApprovalRequest;
}

export interface AssistantApprovalRequest {
    name: string;
    call_id?: string | null;
    execution_id?: string | null;
    arguments: Record<string, unknown>;
    digest: string;
    description: string;
    presentation?: AssistantApprovalPresentation;
}

export interface AssistantApprovalPresentation {
    kind: 'workflow';
    title: string;
    description: string;
    details: string;
    inputs: {
        name: string;
        description: string;
        path: string;
    }[];
    outputs: {
        name: string;
        description: string;
        path: string;
    }[];
    execution: {
        mode: 'background' | 'synchronous';
        gpu: boolean;
    };
}

export interface AssistantMessageEvent {
    content: string;
    round?: number;
}

interface SseEvent {
    eventType: string;
    data: string;
}

export function getRandomStatusMessage(exclude?: string): string {
    const pool = exclude ? STATUS_MESSAGES.filter((message) => message !== exclude) : STATUS_MESSAGES;
    const candidates = pool.length > 0 ? pool : STATUS_MESSAGES;
    return candidates[Math.floor(Math.random() * candidates.length)];
}

export function createChatRequestId(): string {
    return createUuid();
}

export function reportChatEvent(
    level: 'info' | 'warning' | 'error',
    eventType: string,
    message: string,
    details: Record<string, unknown>,
) {
    void reportClientError({
        level,
        event_type: eventType,
        message,
        path: globalThis.location?.pathname ?? null,
        details: {
            ...details,
            user_agent: globalThis.navigator?.userAgent,
        },
    }).catch(() => {
        // Telemetry must never affect chat behavior.
    });
}

export function defaultMessages(scope: AssistantScope): ChatMessage[] {
    return [
        {
            role: 'system',
            content: scope === 'workspace'
                ? 'Welcome! I can help you with MRI viewing, analysis and neuroimaging runs across this workspace.'
                : 'Welcome! I can help you with MRI viewing, analysis and neuroimaging runs.',
        },
    ];
}

function inputWithCursorContext(input: string, currentLocation?: LocationInfo | null): string {
    if (!input.includes('@cursor') || !currentLocation) {
        return input;
    }
    const { vox, labelName } = currentLocation;
    return input.replace(/@cursor/g, `[Cursor Position: (${vox.join(', ')}), Label: ${labelName}]`);
}

export function buildUserContent(
    input: string,
    currentLocation?: LocationInfo | null,
    getMriSnapshots?: () => MriSnapshots | null,
): { content: string | ChatContentPart[]; error?: string } {
    const text = inputWithCursorContext(input, currentLocation);
    if (!VISION_COMMANDS.some((command) => input.includes(command)) || !getMriSnapshots) {
        return { content: text };
    }
    const snapshots = getMriSnapshots();
    if (!snapshots) {
        return { content: text, error: 'Could not capture MRI views. Please try again.' };
    }
    const parts: ChatContentPart[] = [{ type: 'text', text }];
    if (text.includes('@sagittal') || text.includes('@mri')) parts.push({ type: 'image_url', image_url: { url: snapshots.sagittal } });
    if (text.includes('@coronal') || text.includes('@mri')) parts.push({ type: 'image_url', image_url: { url: snapshots.coronal } });
    if (text.includes('@axial') || text.includes('@mri')) parts.push({ type: 'image_url', image_url: { url: snapshots.axial } });
    return { content: parts };
}

export function parseSsePart(part: string): SseEvent | null {
    if (!part.trim()) return null;
    let eventType = '';
    let data = '';
    for (const line of part.split('\n')) {
        if (line.startsWith('event: ')) eventType = line.slice(7);
        else if (line.startsWith('data: ')) data = line.slice(6);
    }
    return eventType && data ? { eventType, data } : null;
}

export function upsertToolCallsMessage(
    messages: ChatMessage[],
    toolCalls: ToolCallEntry[],
    reasoningEntries: ReasoningEntry[],
): ChatMessage[] {
    const count = toolCalls.length;
    const toolMessage: ChatMessage = {
        role: 'tool-calls',
        content: `Used ${count} tool${count === 1 ? '' : 's'}`,
        toolCalls: [...toolCalls],
        reasoningEntries: [...reasoningEntries],
    };
    const last = messages[messages.length - 1];
    return last?.role === 'tool-calls'
        ? [...messages.slice(0, -1), toolMessage]
        : [...messages, toolMessage];
}
