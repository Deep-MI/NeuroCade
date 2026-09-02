import type { ActionApprovalPresentation, AssistantApprovalRequest } from '../types';

interface ChatApprovalContentProps {
    approval: AssistantApprovalRequest;
}

function executionLabel(mode: string, gpu: boolean): string {
    const scheduling = mode === 'background' ? 'Background workflow' : 'Synchronous workflow';
    return `${scheduling} · ${gpu ? 'GPU enabled' : 'CPU only'}`;
}

function ActionApprovalContent({ presentation }: { presentation: ActionApprovalPresentation }) {
    return (
        <div className="chat-approval-content">
            <strong>{presentation.title}</strong>
            <p>{presentation.description}</p>

            {presentation.sections.map((section) => (
                <div className="chat-approval-section" key={section.label}>
                    <span className="chat-approval-label">{section.label}</span>
                    <dl className="chat-approval-rows">
                        {section.rows.map((row, index) => (
                            <div className="chat-approval-row" key={`${row.label}-${index}`}>
                                <dt>{row.label}</dt>
                                <dd>{row.code ? <code>{row.value}</code> : row.value}</dd>
                            </div>
                        ))}
                    </dl>
                </div>
            ))}

            {presentation.details.map((detail, index) => (
                <details className="chat-approval-details" key={`${detail.summary}-${index}`}>
                    <summary>{detail.summary}</summary>
                    {detail.language ? (
                        <pre className="chat-approval-preview"><code>{detail.content}</code></pre>
                    ) : (
                        <p>{detail.content}</p>
                    )}
                </details>
            ))}
        </div>
    );
}

export function ChatApprovalContent({ approval }: ChatApprovalContentProps) {
    const presentation = approval.presentation;
    if (!presentation) {
        return (
            <div>
                <strong>Action requires confirmation</strong>
                <div>{approval.description}</div>
            </div>
        );
    }

    if (presentation.kind === 'action') {
        return <ActionApprovalContent presentation={presentation} />;
    }

    return (
        <div className="chat-approval-content">
            <strong>Run {presentation.title}?</strong>
            <p>{presentation.description}</p>

            {presentation.inputs.length > 0 && (
                <div className="chat-approval-section">
                    <span className="chat-approval-label">Input</span>
                    {presentation.inputs.map((input, index) => (
                        <div className="chat-approval-input" key={`${input.name}-${index}`}>
                            <span>{input.description}</span>
                            <code>{input.path}</code>
                        </div>
                    ))}
                </div>
            )}

            <div className="chat-approval-section">
                <span className="chat-approval-label">Execution</span>
                <div>{executionLabel(presentation.execution.mode, presentation.execution.gpu)}</div>
            </div>

            {(presentation.outputs.length > 0 || presentation.details) && (
                <details className="chat-approval-details">
                    <summary>Workflow details and outputs</summary>
                    {presentation.details && <p>{presentation.details}</p>}
                    {presentation.outputs.length > 0 && (
                        <ul>
                            {presentation.outputs.map((output) => (
                                <li key={`${output.name}-${output.path}`}>
                                    {output.description} <code>{output.path}</code>
                                </li>
                            ))}
                        </ul>
                    )}
                </details>
            )}
        </div>
    );
}
