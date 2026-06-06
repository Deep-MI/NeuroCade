import { reportClientError } from './api';


let installed = false;


function messageFromUnknown(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return String(error);
}


function stackFromUnknown(error: unknown): string | undefined {
  return error instanceof Error ? error.stack : undefined;
}


export function reportFrontendError(
  eventType: string,
  error: unknown,
  details: Record<string, unknown> = {},
) {
  void reportClientError({
    level: 'error',
    event_type: eventType,
    message: messageFromUnknown(error),
    path: globalThis.location?.pathname ?? null,
    details: {
      ...details,
      stack: stackFromUnknown(error),
      user_agent: globalThis.navigator?.userAgent,
    },
  }).catch(() => {
    // Reporting must never create a secondary user-visible failure.
  });
}


export function installGlobalErrorReporting() {
  if (installed || typeof window === 'undefined') {
    return;
  }
  installed = true;

  window.addEventListener('error', (event) => {
    reportFrontendError('frontend.window_error', event.error ?? event.message, {
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
    });
  });

  window.addEventListener('unhandledrejection', (event) => {
    reportFrontendError('frontend.unhandled_rejection', event.reason);
  });
}
