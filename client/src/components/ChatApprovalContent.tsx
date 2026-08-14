import type { AssistantApprovalRequest } from './chatSupport';

interface ChatApprovalContentProps {
    approval: AssistantApprovalRequest;
}

function executionLabel(mode: string, gpu: boolean): string {
    const scheduling = mode === 'background' ? 'Background workflow' : 'Synchronous workflow';
    return `${scheduling} · ${gpu ? 'GPU enabled' : 'CPU only'}`;
}

export function ChatApprovalContent({ approval }: ChatApprovalContentProps) {
    const presentation = approval.presentation;
    if (presentation?.kind !== 'workflow') {
        return (
            <div>
                <strong>Action requires confirmation</strong>
                <div>{approval.description}</div>
            </div>
        );
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
