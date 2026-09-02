import { useCallback, useState, useRef, useEffect } from 'react';
import type {
    AssistantApprovalRequest,
    AssistantScope,
    ChatMessage,
    LocationInfo,
    MriSnapshots,
} from '../types';
import {
    appFetch,
    clearAssistantHistory,
    fetchAssistantHistory,
    parseError,
} from '../utils/api';
import { useAssistantProviderStatus } from '../hooks/useAssistantProviderStatus';
import { useAssistantTurnMonitor } from '../hooks/useAssistantTurnMonitor';
import { ChatApprovalContent } from './ChatApprovalContent';
import { ChatMessageList } from './ChatMessageList';
import { consumeAssistantTurnStream } from './assistantTurnStream';
import { appendUniqueChatMessages } from './chatMessages';
import {
    approvalButtonClass,
    approvalButtonLabel,
    buildUserContent,
    CHAT_REQUEST_TIMEOUT_MS,
    CHAT_REQUEST_TIMEOUT_SECONDS,
    createChatRequestId,
    defaultMessages,
    getRandomStatusMessage,
    reportChatEvent,
    STATUS_MESSAGES,
    upsertToolCallsMessage,
} from './chatSupport';

export type { ChatMessage };

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
    const [pendingApproval, setPendingApproval] = useState<AssistantApprovalRequest | null>(null);
    const scrollRef = useRef<HTMLDivElement>(null);
    const abortRef = useRef<AbortController | null>(null);
    const historyRequestVersionRef = useRef(0);
    const suppressAbortMessageRef = useRef(false);
    const lastClearRequestTokenRef = useRef(clearRequestToken);
    const externalMessagesRef = useRef(externalMessages);
    externalMessagesRef.current = externalMessages;
    const {
        disabledMessage: assistantDisabledMessage,
        retryable: providerRetryable,
        retry: retryProvider,
    } = useAssistantProviderStatus();

    const loadPersistedChatState = useCallback(async () => {
        if (!workspaceId) return;
        const requestVersion = historyRequestVersionRef.current + 1;
        historyRequestVersionRef.current = requestVersion;
        const history = await fetchAssistantHistory(workspaceId, scope, caseId);
        if (historyRequestVersionRef.current !== requestVersion) return;
        setMessages(appendUniqueChatMessages([
            ...defaultMessages(scope),
            ...history.messages,
        ], externalMessagesRef.current));
        setPendingApproval(history.pending_approval ?? null);
    }, [caseId, scope, workspaceId]);

    const handleBackgroundTurnComplete = useCallback(async () => {
        await loadPersistedChatState();
        onAssistantTurnComplete?.();
    }, [loadPersistedChatState, onAssistantTurnComplete]);

    const {
        activeTurnId,
        activity: assistantActivity,
        isCanceling,
        trackTurn,
        updateActivity: updateAssistantActivity,
        discoverTurn,
        markTurnFinished,
        cancelTurn,
    } = useAssistantTurnMonitor({
        workspaceId,
        scope,
        caseId,
        isStreamConnected: isLoading,
        onTurnComplete: handleBackgroundTurnComplete,
    });
    const isTurnActive = isLoading || activeTurnId !== null;

    useEffect(() => {
        if (!workspaceId) {
            return;
        }
        setPendingApproval(null);
        void loadPersistedChatState()
            .catch((error) => {
                console.error('Failed to load assistant history:', error);
            });
        return () => {
            historyRequestVersionRef.current += 1;
        };
    }, [loadPersistedChatState, workspaceId]);

    useEffect(() => {
        return () => {
            if (abortRef.current) {
                suppressAbortMessageRef.current = true;
                abortRef.current.abort();
            }
        };
    }, []);

    useEffect(() => {
        if (isLoading) {
            setLoadingMessage(prev => getRandomStatusMessage(prev));
        }
    }, [isLoading]);

    // Sync with external messages (e.g. system notifications)
    useEffect(() => {
        if (externalMessages.length > 0) {
            setMessages(prev => appendUniqueChatMessages(prev, externalMessages));
        }
    }, [externalMessages]);

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages]);

    const handleClear = useCallback(async () => {
        if (!workspaceId || isClearing || isTurnActive) return;
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
    }, [caseId, isClearing, isTurnActive, scope, workspaceId]);

    useEffect(() => {
        onClearStateChange?.(isClearing || isTurnActive);
    }, [isClearing, isTurnActive, onClearStateChange]);

    useEffect(() => {
        if (clearRequestToken === undefined || clearRequestToken === lastClearRequestTokenRef.current) return;
        lastClearRequestTokenRef.current = clearRequestToken;
        void handleClear();
    }, [clearRequestToken, handleClear]);

    const handleSend = async (approval?: AssistantApprovalRequest) => {
        if ((!approval && !input.trim()) || isTurnActive || isClearing || assistantDisabledMessage) return;

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
        let managedTurnId: string | null = null;
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
            const responseTurnId = response.headers.get('X-Assistant-Turn-Id');
            if (responseTurnId) {
                managedTurnId = responseTurnId;
                trackTurn(responseTurnId);
            }
            reportChatEvent('info', 'frontend.assistant_turn.response_started', 'Assistant turn response stream opened', {
                chat_request_id: chatRequestId,
                elapsed_ms: responseStartedElapsedMs,
                status: response.status,
                ok: response.ok,
            });

            if (!response.ok) {
                if (response.status === 409 && workspaceId) {
                    const turnId = await discoverTurn().catch(() => null);
                    if (turnId) {
                        trackTurn(turnId);
                        return;
                    }
                }
                const errorMessage = await parseError(response, 'API request failed');
                if (errorMessage.includes('image')) {
                    throw new Error("The current model does not support image capabilities. Please switch to a vision model like Qwen3-VL-32B or gpt-4o.");
                }
                throw new Error(errorMessage);
            }
            if (!response.body) {
                throw new Error('Assistant response stream was empty.');
            }

            const streamResult = await consumeAssistantTurnStream(response.body, {
                onText: (streamedText, startsNewMessage) => {
                    if (startsNewMessage) {
                        setMessages(prev => [...prev, { role: 'assistant', content: streamedText }]);
                        return;
                    }
                    setMessages(prev => {
                        const last = prev[prev.length - 1];
                        return last?.role === 'assistant'
                            ? [...prev.slice(0, -1), { ...last, content: streamedText }]
                            : [...prev, { role: 'assistant', content: streamedText }];
                    });
                },
                onActivity: updateAssistantActivity,
                onAssistantMessage: (content) => {
                    setMessages(prev => [...prev, { role: 'assistant', content }]);
                },
                onToolUpdates: (toolCalls, reasoningEntries) => {
                    setMessages(prev => upsertToolCallsMessage(prev, toolCalls, reasoningEntries));
                },
                onDone: (apiData, streamedText) => {
                    const assistantContent = apiData.message.content;
                    if (assistantContent !== streamedText) {
                        setMessages(prev => [...prev, { role: 'assistant', content: assistantContent }]);
                    }
                    setPendingApproval(apiData.approval_request ?? null);
                },
            });
            if (!streamResult.receivedFinalEvent) {
                reportChatEvent('warning', 'frontend.assistant_turn.incomplete_stream', 'Assistant response stream ended before a final event', {
                    chat_request_id: chatRequestId,
                    elapsed_ms: Math.round(performance.now() - startedAt),
                    sse_event_counts: streamResult.eventCounts,
                });
                setMessages(prev => [...prev, { role: 'info', content: 'Assistant response ended before a final message was received. Please try again.' }]);
            } else {
                markTurnFinished();
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
                    setMessages(prev => [...prev, {
                        role: 'info',
                        content: managedTurnId
                            ? `Live updates timed out after ${CHAT_REQUEST_TIMEOUT_SECONDS} seconds. The assistant is continuing in the background.`
                            : `Assistant request timed out after ${CHAT_REQUEST_TIMEOUT_SECONDS} seconds. Please try again or narrow the request.`,
                    }]);
                } else if (!suppressAbortMessageRef.current) {
                    reportChatEvent('info', 'frontend.assistant_turn.stopped', 'Assistant turn request stopped by the user', {
                        chat_request_id: chatRequestId,
                        elapsed_ms: Math.round(performance.now() - startedAt),
                    });
                    setMessages(prev => [...prev, { role: 'info', content: managedTurnId ? 'Live updates stopped. The assistant is continuing in the background.' : 'Request stopped.' }]);
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

    const handleStop = async () => {
        if (!workspaceId || isCanceling) return;
        try {
            const status = await cancelTurn();
            if (status === 'canceling') {
                setMessages(prev => [...prev, { role: 'info', content: 'Assistant cancellation requested.' }]);
            }
            if (abortRef.current) {
                suppressAbortMessageRef.current = true;
                abortRef.current.abort();
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to stop the assistant turn.';
            setMessages(prev => [...prev, { role: 'info', content: message }]);
        }
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
                        disabled={isClearing || isTurnActive}
                        title="Clear chat context"
                        aria-label="Clear chat context"
                    >
                        <span aria-hidden="true">+</span>
                    </button>
                </div>
            )}
            <ChatMessageList
                messages={messages}
                isTurnActive={isTurnActive}
                isLoading={isLoading}
                loadingMessage={loadingMessage}
                assistantActivity={assistantActivity}
                assistantDisabledMessage={assistantDisabledMessage}
                providerRetryable={providerRetryable}
                onRetryProvider={retryProvider}
                scrollRef={scrollRef}
            />

            {pendingApproval && !isTurnActive && (
                <div className="chat-approval" role="group" aria-label="Confirm assistant action">
                    <ChatApprovalContent approval={pendingApproval} />
                    <div className="chat-approval-actions flex items-center gap-2">
                        <button
                            type="button"
                            className={`nc-btn ${approvalButtonClass(pendingApproval.presentation)} px-3`}
                            onClick={() => void handleSend(pendingApproval)}
                        >
                            {approvalButtonLabel(pendingApproval.presentation)}
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
                    disabled={isClearing || isTurnActive || Boolean(assistantDisabledMessage)}
                />
                {isTurnActive ? (
                    <button
                        className="nc-btn nc-btn-danger px-4"
                        onClick={() => void handleStop()}
                        disabled={isClearing || isCanceling}
                    >
                        {isCanceling ? 'Stopping…' : 'Stop'}
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
