import { Component, type ErrorInfo, type ReactNode } from 'react';

import { reportFrontendError } from '../utils/monitoring';

interface Props {
  children: ReactNode;
  label?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[ErrorBoundary:${this.props.label ?? 'unknown'}]`, error, info.componentStack);
    reportFrontendError('frontend.error_boundary', error, {
      boundary: this.props.label ?? 'unknown',
      component_stack: info.componentStack,
    });
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full min-h-[120px] w-full items-center justify-center rounded border border-[var(--nc-danger-border)] bg-[var(--nc-danger-bg)] p-4 text-sm text-[var(--nc-danger)]">
          <div>
            <div className="font-semibold mb-1">{this.props.label ?? 'Component'} crashed</div>
            <div className="text-xs opacity-70">{this.state.error.message}</div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
