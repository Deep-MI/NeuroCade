import { Children, useCallback, useState, useRef, useEffect } from 'react';
import { AlertTriangle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import type { LocationInfo, MriSnapshots } from '../types';
import type {
    AssistantScope,
    ChatMessage,
    ChatContentPart,
    ChatTextPart,
    ChatImagePart,
    ToolCallEntry,
    ReasoningEntry,
} from '../types';
import { appFetch, clearAssistantHistory, fetchAssistantHistory, fetchProviders, parseError } from '../utils/api';
import { ChatToolCallsContent } from './ChatToolCallsContent';
import { ChatApprovalContent } from './ChatApprovalContent';
import {
    type ApiResponse,
    type AssistantApprovalRequest,
    type AssistantMessageEvent,
    buildUserContent,
    CHAT_REQUEST_TIMEOUT_MS,
    CHAT_REQUEST_TIMEOUT_SECONDS,
    createChatRequestId,
    defaultMessages,
    getRandomStatusMessage,
    parseSsePart,
    reportChatEvent,
    STATUS_MESSAGES,
    upsertToolCallsMessage,
} from './chatSupport';

export type { ChatMessage };

interface CodeProps {
    node?: unknown;
    inline?: boolean;
    className?: string;
    children?: React.ReactNode;
}

function renderMarkdownCodeChildren(children: React.ReactNode): string {
    return Children.toArray(children)
        .map((child) => {
            if (typeof child === 'string' || typeof child === 'number') {
                return String(child);
            }
            return '';
        })
        .join('')
        .replace(/\n$/, '');
}

interface ChatProps {
    externalMessages?: ChatMessage[];
    style?: React.CSSProperties;
    hideHeader?: boolean;
    currentLocation?: LocationInfo | null;
    getMriSnapshots?: () => MriSnapshots | null;
    workspaceId?: string | null;
    caseId?: string | null;
    guiSessionId: string;
    clearRequestToken?: number;
    onClearStateChange?: (isClearing: boolean) => void;
    onAssistantTurnComplete?: () => void;
}

export function Chat({ externalMessages = [], style, hideHeader = false, currentLocation, getMriSnapshots, workspaceId = null, caseId = null, guiSessionId, clearRequestToken, onClearStateChange, onAssistantTurnComplete }: ChatProps) {
    const scope: AssistantScope = caseId ? 'case' : 'workspace';
    const [messages, setMessages] = useState<ChatMessage[]>(defaultMessages(scope));
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [loadingMessage, setLoadingMessage] = useState<string>(STATUS_MESSAGES[1]);
    const [isClearing, setIsClearing] = useState(false);
    const [assistantDisabledMessage, setAssistantDisabledMessage] = useState<string | null>(null);
    const [providerRetryable, setProviderRetryable] = useState(false);
    const [providerRefreshKey, setProviderRefreshKey] = useState(0);
    const [pendingApproval, setPendingApproval] = useState<AssistantApprovalRequest | null>(null);
    const scrollRef = useRef<HTMLDivElement>(null);
    const abortRef = useRef<AbortController | null>(null);
    const historyRequestVersionRef = useRef(0);
    const suppressAbortMessageRef = useRef(false);
    const lastClearRequestTokenRef = useRef(clearRequestToken);
    const externalMessagesRef = useRef(externalMessages);
    externalMessagesRef.current = externalMessages;

    useEffect(() => {
        if (!workspaceId) {
            return;
        }
        let cancelled = false;
        const requestVersion = historyRequestVersionRef.current + 1;
        historyRequestVersionRef.current = requestVersion;
        setPendingApproval(null);
        void fetchAssistantHistory(workspaceId, scope, caseId)
            .then((history) => {
                if (cancelled || historyRequestVersionRef.current !== requestVersion) return;
                if (history.messages.length > 0) {
                    setMessages([
                        ...defaultMessages(scope),
                        ...history.messages,
                        ...externalMessagesRef.current,
                    ]);
                    return;
                }
                setMessages([
                    ...defaultMessages(scope),
                    ...externalMessagesRef.current,
                ]);
            })
            .catch((error) => {
                if (cancelled || historyRequestVersionRef.current !== requestVersion) return;
                console.error('Failed to load assistant history:', error);
            });
        return () => {
            cancelled = true;
        };
    }, [workspaceId, caseId, scope]);

    useEffect(() => {
        let cancelled = false;
        void fetchProviders()
            .then((providers) => {
                if (cancelled) return;
                const chatProviders = providers;
                const defaultChatProvider = chatProviders.find((provider) => provider.is_default);
                if (defaultChatProvider?.provider === 'no-llm' || defaultChatProvider?.provider_family === 'none') {
                    setAssistantDisabledMessage('Assistant is disabled because LLM setup was skipped. You can still upload, view, and process cases.');
                    setProviderRetryable(false);
                    return;
                }
                if (!defaultChatProvider?.configured) {
                    setAssistantDisabledMessage('Assistant is disabled because no LLM provider is configured. You can still upload, view, and process cases.');
                    setProviderRetryable(false);
                    return;
                }
                if (!defaultChatProvider.reachable) {
                    setAssistantDisabledMessage('The configured model provider is temporarily unreachable. Check the provider and try again.');
                    setProviderRetryable(true);
                    return;
                }
                setAssistantDisabledMessage(null);
                setProviderRetryable(false);
            })
            .catch((error) => {
                if (cancelled) return;
                console.error('Failed to load provider configuration:', error);
                setAssistantDisabledMessage(null);
            });
        return () => {
            cancelled = true;
        };
    }, [providerRefreshKey]);

    useEffect(() => {
        if (isLoading) {
            setLoadingMessage(prev => getRandomStatusMessage(prev));
        }
    }, [isLoading]);

    // Sync with external messages (e.g. system notifications)
    useEffect(() => {
        if (externalMessages.length > 0) {
            setMessages(prev => {
                const newMessages = externalMessages.filter(msg => !prev.includes(msg));
                if (newMessages.length === 0) return prev;
                return [...prev, ...newMessages];
            });
        }
    }, [externalMessages]);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages]);

    const handleClear = useCallback(async () => {
        if (!workspaceId || isClearing) return;
        historyRequestVersionRef.current += 1;
        suppressAbortMessageRef.current = true;
        abortRef.current?.abort();
        abortRef.current = null;
        setIsLoading(false);
        setIsClearing(true);
        try {
            await clearAssistantHistory(workspaceId, scope, caseId);
            setMessages(defaultMessages(scope));
            setInput('');
            setPendingApproval(null);
        } catch (error) {
            console.error('Failed to clear assistant history:', error);
            const errorMsg = (error instanceof Error ? error.message : null) ?? 'Failed to clear chat history.';
            setMessages(prev => [...prev, { role: 'info', content: errorMsg }]);
        } finally {
            setIsClearing(false);
        }
    }, [caseId, isClearing, scope, workspaceId]);

    useEffect(() => {
        onClearStateChange?.(isClearing);
    }, [isClearing, onClearStateChange]);

    useEffect(() => {
        if (clearRequestToken === undefined || clearRequestToken === lastClearRequestTokenRef.current) return;
        lastClearRequestTokenRef.current = clearRequestToken;
        void handleClear();
    }, [clearRequestToken, handleClear]);

    const handleSend = async (approval?: AssistantApprovalRequest) => {
        if ((!approval && !input.trim()) || isLoading || isClearing || assistantDisabledMessage) return;

        const userContent = approval
            ? { content: `I approve the requested action: ${approval.presentation?.title ?? approval.description}.`, error: undefined }
            : buildUserContent(input, currentLocation, getMriSnapshots);
        if (userContent.error) {
            setMessages(prev => [...prev, { role: 'info', content: userContent.error ?? 'Could not prepare message.' }]);
            return;
        }

        const userMsg: ChatMessage = { role: 'user', content: userContent.content };
        setMessages(prev => [...prev, userMsg]);
        setInput('');
        setPendingApproval(null);
        setIsLoading(true);
        const controller = new AbortController();
        const chatRequestId = createChatRequestId();
        const startedAt = performance.now();
        let didTimeout = false;
        const timeoutId = window.setTimeout(() => {
            didTimeout = true;
            controller.abort();
        }, CHAT_REQUEST_TIMEOUT_MS);
        abortRef.current = controller;
        reportChatEvent('info', 'frontend.assistant_turn.started', 'Assistant turn request started', {
            chat_request_id: chatRequestId,
            scope,
            workspace_id: workspaceId,
            case_id: scope === 'case' ? caseId : null,
            timeout_ms: CHAT_REQUEST_TIMEOUT_MS,
        });

        try {
            const response = await appFetch('/assistant/turns', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    messages: [userMsg],
                    workspace_id: workspaceId,
                    case_id: scope === 'case' ? caseId : null,
                    gui_session_id: guiSessionId,
                    gui_state_override: currentLocation ? {
                        current_cursor: {
                            voxel: currentLocation.vox,
                            label_id: currentLocation.labelIndex,
                            label_name: currentLocation.labelName,
                        },
                    } : undefined,
                    scope,
                    tool_approvals: approval ? [{
                        name: approval.name,
                        call_id: approval.call_id,
                        execution_id: approval.execution_id,
                        arguments: approval.arguments,
                        digest: approval.digest,
                    }] : [],
                }),
                signal: controller.signal,
            });
            const responseStartedElapsedMs = Math.round(performance.now() - startedAt);
            reportChatEvent('info', 'frontend.assistant_turn.response_started', 'Assistant turn response stream opened', {
                chat_request_id: chatRequestId,
                elapsed_ms: responseStartedElapsedMs,
                status: response.status,
                ok: response.ok,
            });

            if (!response.ok) {
                const errorMessage = await parseError(response, 'API request failed');
                if (errorMessage.includes('image')) {
                    throw new Error("The current model does not support image capabilities. Please switch to a vision model like Qwen3-VL-32B or gpt-4o.");
                }
                throw new Error(errorMessage);
            }
            if (!response.body) {
                throw new Error('Assistant response stream was empty.');
            }

            // Read the SSE stream — tool calls arrive incrementally
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            const accumulated: ToolCallEntry[] = [];
            const reasoningEntries: ReasoningEntry[] = [];
            let receivedFinalEvent = false;
            let streamedText = '';
            let streamedRound: number | undefined;
            const sseEventCounts: Record<string, number> = {};

            const handleSsePart = (part: string) => {
                const event = parseSsePart(part);
                if (!event) return;
                const { eventType, data } = event;
                sseEventCounts[eventType] = (sseEventCounts[eventType] ?? 0) + 1;

                if (eventType === 'text_delta') {
                    const delta = JSON.parse(data) as AssistantMessageEvent;
                    if (streamedRound !== delta.round) {
                        streamedText = delta.content;
                        streamedRound = delta.round;
                        setMessages(prev => [...prev, { role: 'assistant', content: streamedText }]);
                    } else {
                        streamedText += delta.content;
                        setMessages(prev => {
                            const last = prev[prev.length - 1];
                            return last?.role === 'assistant'
                                ? [...prev.slice(0, -1), { ...last, content: streamedText }]
                                : [...prev, { role: 'assistant', content: streamedText }];
                        });
                    }
                } else if (eventType === 'assistant_message') {
                    const assistantMessage = JSON.parse(data) as AssistantMessageEvent;
                    if (assistantMessage.content.trim()) {
                        setMessages(prev => [...prev, { role: 'assistant', content: assistantMessage.content }]);
                    }
                } else if (eventType === 'reasoning') {
                    const reasoning = JSON.parse(data) as ReasoningEntry;
                    reasoningEntries.push(reasoning);
                    setMessages(prev => upsertToolCallsMessage(prev, accumulated, reasoningEntries));
                } else if (eventType === 'tool_call') {
                    const tc = JSON.parse(data) as ToolCallEntry;
                    accumulated.push(tc);
                    setMessages(prev => upsertToolCallsMessage(prev, accumulated, reasoningEntries));
                } else if (eventType === 'done') {
                    receivedFinalEvent = true;
                    const apiData = JSON.parse(data) as ApiResponse;
                    const assistantContent = apiData.message.content;
                    if (assistantContent !== streamedText) {
                        setMessages(prev => [...prev, { role: 'assistant', content: assistantContent }]);
                    }
                    setPendingApproval(apiData.approval_request ?? null);
                } else if (eventType === 'error') {
                    receivedFinalEvent = true;
                    const errPayload = JSON.parse(data) as { error?: { message?: string } };
                    throw new Error(errPayload.error?.message ?? 'API request failed');
                }
            };

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                // Parse complete SSE events (separated by double newlines)
                const parts = buffer.split('\n\n');
                buffer = parts.pop()!; // last part may be incomplete

                for (const part of parts) {
                    handleSsePart(part);
                }
            }
            if (buffer.trim()) {
                handleSsePart(buffer);
            }
            if (!receivedFinalEvent) {
                reportChatEvent('warning', 'frontend.assistant_turn.incomplete_stream', 'Assistant response stream ended before a final event', {
                    chat_request_id: chatRequestId,
                    elapsed_ms: Math.round(performance.now() - startedAt),
                    sse_event_counts: sseEventCounts,
                });
                setMessages(prev => [...prev, { role: 'info', content: 'Assistant response ended before a final message was received. Please try again.' }]);
            }
        } catch (error: unknown) {
            if (error instanceof DOMException && error.name === 'AbortError') {
                if (didTimeout) {
                    reportChatEvent('warning', 'frontend.assistant_turn.timeout', 'Assistant turn request timed out in the browser', {
                        chat_request_id: chatRequestId,
                        elapsed_ms: Math.round(performance.now() - startedAt),
                        timeout_ms: CHAT_REQUEST_TIMEOUT_MS,
                        scope,
                        workspace_id: workspaceId,
                        case_id: scope === 'case' ? caseId : null,
                    });
                    setMessages(prev => [...prev, { role: 'info', content: `Assistant request timed out after ${CHAT_REQUEST_TIMEOUT_SECONDS} seconds. Please try again or narrow the request.` }]);
                } else if (!suppressAbortMessageRef.current) {
                    reportChatEvent('info', 'frontend.assistant_turn.stopped', 'Assistant turn request stopped by the user', {
                        chat_request_id: chatRequestId,
                        elapsed_ms: Math.round(performance.now() - startedAt),
                    });
                    setMessages(prev => [...prev, { role: 'info', content: 'Request stopped.' }]);
                }
                suppressAbortMessageRef.current = false;
            } else {
                console.error('Chat error:', error);
                const errorMsg = (error instanceof Error ? error.message : null) ?? 'Sorry, I encountered an error connecting to the model server.';
                reportChatEvent('error', 'frontend.assistant_turn.error', errorMsg, {
                    chat_request_id: chatRequestId,
                    elapsed_ms: Math.round(performance.now() - startedAt),
                    error_type: error instanceof Error ? error.name : typeof error,
                });
                setMessages(prev => [...prev, { role: 'info', content: errorMsg }]);
            }
        } finally {
            window.clearTimeout(timeoutId);
            abortRef.current = null;
            setIsLoading(false);
            onAssistantTurnComplete?.();
        }
    };

    // Helper to render message content whether string or array
    const renderContent = (content: string | ChatContentPart[]) => {
        if (typeof content === 'string') return content;
        // Extract text parts from multipart array
        return content
            .filter((item): item is ChatTextPart => item.type === 'text')
            .map(item => item.text)
            .join('\n');
    };

    // Render attached image thumbnails for vision messages
    const renderImages = (content: string | ChatContentPart[]) => {
        if (typeof content === 'string') return null;
        const images = content.filter((item): item is ChatImagePart => item.type === 'image_url');
        if (images.length === 0) return null;
        return (
            <div className="flex gap-2 mt-2 flex-wrap">
                {images.map((img, idx) => (
                    <img key={idx} src={img.image_url.url} alt="MRI View" className="h-16 w-16 object-cover rounded border border-white/20" />
                ))}
            </div>
        );
    };

    return (
        <div className="chat-container" style={style}>
            {!hideHeader && (
                <div className="chat-header">
                    <div className="chat-header-title">
                        {scope === 'workspace' ? 'Workspace Chat' : 'Case Chat'}
                    </div>
                    <button
                        type="button"
                        className="chat-clear-button"
                        onClick={() => void handleClear()}
                        disabled={isClearing}
                        title="Clear chat context"
                        aria-label="Clear chat context"
                    >
                        <span aria-hidden="true">+</span>
                    </button>
                </div>
            )}
            <div className="chat-messages" ref={scrollRef}>
                {messages.map((msg, i) => (
                    <div
                        key={i}
                        className={`chat-message ${msg.role === 'user' ? 'user' :
                            msg.role === 'info' ? 'info' :
                                msg.role === 'system' ? 'system' :
                                    msg.role === 'tool-calls' ? 'tool-calls' : 'assistant'
                            }${msg.severity === 'warning' ? ' chat-message-warning' : ''}`}
                        role={msg.severity === 'warning' ? 'status' : undefined}
                    >
                        {msg.severity === 'warning' && (
                            <div className="chat-message-warning-label">
                                <AlertTriangle size={14} aria-hidden="true" />
                                <span>Warning</span>
                            </div>
                        )}
                        {msg.role === 'tool-calls' && msg.toolCalls ? (
                            <ChatToolCallsContent
                                toolCalls={msg.toolCalls}
                                reasoningEntries={msg.reasoningEntries}
                            />
                        ) : (
                            <>
                                <ReactMarkdown
                                    components={{
                                        code({ inline, className, children, ...props }: CodeProps) {
                                            const match = /language-(\w+)/.exec(className ?? '');
                                            return !inline && match ? (
                                                <pre className={className} {...props}>
                                                    <code>{renderMarkdownCodeChildren(children)}</code>
                                                </pre>
                                            ) : (
                                                <code className={className} {...props}>
                                                    {children}
                                                </code>
                                            );
                                        }
                                    }}
                                >
                                    {renderContent(msg.content)}
                                </ReactMarkdown>
                                {renderImages(msg.content)}
                            </>
                        )}
                    </div>
                ))}
                {isLoading && (
                    <div className="chat-message info italic chat-loading">
                        <span>{loadingMessage}</span>
                        <span className="chat-spinner" aria-hidden="true" />
                    </div>
                )}
                {assistantDisabledMessage && (
                    <div className="chat-message info">
                        {assistantDisabledMessage}
                        {providerRetryable && (
                            <button
                                type="button"
                                className="nc-btn ml-3 px-2 py-1"
                                onClick={() => setProviderRefreshKey((value) => value + 1)}
                            >
                                Retry
                            </button>
                        )}
                    </div>
                )}
            </div>

            {pendingApproval && !isLoading && (
                <div className="chat-approval" role="group" aria-label="Confirm assistant action">
                    <ChatApprovalContent approval={pendingApproval} />
                    <div className="chat-approval-actions flex items-center gap-2">
                        <button
                            type="button"
                            className="nc-btn nc-btn-active px-3"
                            onClick={() => void handleSend(pendingApproval)}
                        >
                            {pendingApproval.presentation?.kind === 'workflow' ? 'Start workflow' : 'Approve'}
                        </button>
                        <button
                            type="button"
                            className="nc-btn px-3"
                            onClick={() => setPendingApproval(null)}
                        >
                            Decline
                        </button>
                    </div>
                </div>
            )}
            <div className="chat-input-container">
                <input
                    className="chat-input"
                    placeholder={scope === 'workspace' ? 'Ask about the workspace...' : 'Ask about the scan...'}
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') void handleSend(); }}
                    disabled={isClearing || Boolean(assistantDisabledMessage)}
                />
                {isLoading ? (
                    <button
                        className="nc-btn nc-btn-danger px-4"
                        onClick={() => abortRef.current?.abort()}
                        disabled={isClearing}
                    >
                        Stop
                    </button>
                ) : (
                    <button
                        className="nc-btn nc-btn-active px-4"
                        onClick={() => void handleSend()}
                        disabled={!input.trim() || isClearing || Boolean(assistantDisabledMessage)}
                    >
                        Send
                    </button>
                )}
            </div>
        </div>
    );
}
