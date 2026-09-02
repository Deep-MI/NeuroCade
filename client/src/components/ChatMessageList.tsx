import { Children } from 'react';
import type { RefObject } from 'react';
import { AlertTriangle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

import type { AssistantActivity, ChatContentPart, ChatImagePart, ChatMessage, ChatTextPart } from '../types';
import { assistantActivityMessage, assistantActivityProgress } from './assistantActivity';
import { ChatToolCallsContent } from './ChatToolCallsContent';

interface CodeProps {
  node?: unknown;
  inline?: boolean;
  className?: string;
  children?: React.ReactNode;
}

interface ChatMessageListProps {
  messages: ChatMessage[];
  isTurnActive: boolean;
  isLoading: boolean;
  loadingMessage: string;
  assistantActivity: AssistantActivity | null;
  assistantDisabledMessage: string | null;
  providerRetryable: boolean;
  onRetryProvider: () => void;
  scrollRef: RefObject<HTMLDivElement | null>;
}

function renderMarkdownCodeChildren(children: React.ReactNode): string {
  return Children.toArray(children)
    .map((child) => typeof child === 'string' || typeof child === 'number' ? String(child) : '')
    .join('')
    .replace(/\n$/, '');
}

function renderContent(content: string | ChatContentPart[]): string {
  if (typeof content === 'string') return content;
  return content
    .filter((item): item is ChatTextPart => item.type === 'text')
    .map((item) => item.text)
    .join('\n');
}

function MessageImages({ content }: { content: string | ChatContentPart[] }) {
  if (typeof content === 'string') return null;
  const images = content.filter((item): item is ChatImagePart => item.type === 'image_url');
  if (images.length === 0) return null;
  return (
    <div className="flex gap-2 mt-2 flex-wrap">
      {images.map((image, index) => (
        <img key={index} src={image.image_url.url} alt="MRI View" className="h-16 w-16 object-cover rounded border border-white/20" />
      ))}
    </div>
  );
}

export function ChatMessageList({
  messages,
  isTurnActive,
  isLoading,
  loadingMessage,
  assistantActivity,
  assistantDisabledMessage,
  providerRetryable,
  onRetryProvider,
  scrollRef,
}: ChatMessageListProps) {
  const assistantProgress = assistantActivityProgress(assistantActivity);
  return (
    <div className="chat-messages" ref={scrollRef}>
      {messages.map((message, index) => (
        <div
          key={index}
          className={`chat-message ${message.role === 'user' ? 'user' :
            message.role === 'info' ? 'info' :
              message.role === 'system' ? 'system' :
                message.role === 'tool-calls' ? 'tool-calls' : 'assistant'
          }${message.severity === 'warning' ? ' chat-message-warning' : ''}`}
          role={message.severity === 'warning' ? 'status' : undefined}
        >
          {message.severity === 'warning' && (
            <div className="chat-message-warning-label">
              <AlertTriangle size={14} aria-hidden="true" />
              <span>Warning</span>
            </div>
          )}
          {message.role === 'tool-calls' && message.toolCalls ? (
            <ChatToolCallsContent toolCalls={message.toolCalls} reasoningEntries={message.reasoningEntries} />
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
                      <code className={className} {...props}>{children}</code>
                    );
                  },
                }}
              >
                {renderContent(message.content)}
              </ReactMarkdown>
              <MessageImages content={message.content} />
            </>
          )}
        </div>
      ))}
      {isTurnActive && (
        <div className="chat-message info italic chat-loading">
          <div className="chat-loading-label">
            <span>{assistantActivityMessage(assistantActivity, isLoading, loadingMessage)}</span>
            {assistantProgress == null && <span className="chat-spinner" aria-hidden="true" />}
          </div>
          {assistantActivity?.kind === 'image' && assistantActivity.disk_warning && (
            <div className="chat-download-warning" role="status">
              <AlertTriangle size={14} aria-hidden="true" />
              <div>
                <span>{assistantActivity.disk_warning}</span>
                {assistantActivity.reclaimable_storage
                  && Object.keys(assistantActivity.reclaimable_storage).length > 0 && (
                  <details>
                    <summary>Storage options</summary>
                    <span>
                      Docker reports{' '}
                      {Object.entries(assistantActivity.reclaimable_storage)
                        .map(([type, size]) => `${type}: ${size}`)
                        .join('; ')} reclaimable. Review unused items in Docker Desktop;
                      NeuroCade will not delete them automatically.
                    </span>
                  </details>
                )}
              </div>
            </div>
          )}
          {assistantProgress != null && (
            <div
              className="chat-download-progress"
              role="progressbar"
              aria-label="Container image download progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(assistantProgress * 100)}
            >
              <span style={{ width: `${assistantProgress * 100}%` }} />
            </div>
          )}
        </div>
      )}
      {assistantDisabledMessage && (
        <div className="chat-message info">
          {assistantDisabledMessage}
          {providerRetryable && (
            <button type="button" className="nc-btn ml-3 px-2 py-1" onClick={onRetryProvider}>
              Retry
            </button>
          )}
        </div>
      )}
    </div>
  );
}
