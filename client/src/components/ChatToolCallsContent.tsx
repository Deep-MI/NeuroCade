import type { CSSProperties } from 'react';

import type { ReasoningEntry, ToolCallEntry } from '../types';

const preStyle: CSSProperties = {
    margin: 0, padding: '4px 6px', background: '#1e293b',
    color: '#e2e8f0', borderRadius: 4, fontSize: 11,
    lineHeight: 1.4, whiteSpace: 'pre-wrap',
    wordBreak: 'break-word', maxHeight: 140, overflowY: 'auto',
};

const sectionLabelStyle: CSSProperties = {
    fontSize: 10, fontWeight: 600, color: '#64748b',
    textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 2,
};

const codeStyle: CSSProperties = {
    background: 'rgba(51,65,85,0.12)',
    padding: '1px 5px',
    borderRadius: 3,
    fontFamily: "'Courier New', Courier, monospace",
    fontSize: 11,
    color: '#1e40af',
};

export function ChatToolCallsContent({
    toolCalls,
    reasoningEntries = [],
}: {
    toolCalls: ToolCallEntry[];
    reasoningEntries?: ReasoningEntry[];
}) {
    return (
        <div style={{ fontSize: 12 }}>
            {reasoningEntries.length > 0 && (
                <details style={{ marginBottom: toolCalls.length > 0 ? 6 : 0 }}>
                    <summary style={{ cursor: 'pointer', fontWeight: 600, listStyle: 'revert' }}>
                        Working notes
                    </summary>
                    <div style={{ marginTop: 4, paddingLeft: 16 }}>
                        {reasoningEntries.map((entry, index) => (
                            <div key={index} style={{ marginBottom: index < reasoningEntries.length - 1 ? 6 : 0 }}>
                                <div style={sectionLabelStyle}>
                                    {entry.round ? `Round ${entry.round}` : 'Round'}
                                </div>
                                <pre style={preStyle}>{entry.summary}</pre>
                            </div>
                        ))}
                    </div>
                </details>
            )}
            {toolCalls.map((toolCall, index) => {
                const args = typeof toolCall.arguments === 'string'
                    ? toolCall.arguments
                    : JSON.stringify(toolCall.arguments, null, 2);
                const result = toolCall.result || '(no output)';
                return (
                    <details key={index} style={{ marginBottom: index < toolCalls.length - 1 ? 4 : 0 }}>
                        <summary style={{ cursor: 'pointer', fontWeight: 600, listStyle: 'revert' }}>
                            <code style={codeStyle}>{toolCall.name}</code>
                        </summary>
                        <div style={{ marginTop: 4, paddingLeft: 16 }}>
                            <div style={sectionLabelStyle}>Arguments</div>
                            <pre style={preStyle}>{args}</pre>
                            <div style={{ ...sectionLabelStyle, marginTop: 4 }}>Result</div>
                            <pre style={preStyle}>{result}</pre>
                        </div>
                    </details>
                );
            })}
        </div>
    );
}
