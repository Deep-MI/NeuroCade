export interface RunCancellation {
  run_id: string;
  status: string;
  cancellation: 'requested' | 'unresolved' | 'stopped' | null;
  output_ownership: 'held' | 'unresolved' | 'released' | null;
}

export function describeCancellation(result: RunCancellation) {
  const stopped = result.status === 'canceled' && result.cancellation === 'stopped';
  const message = stopped ? 'Run canceled by user.'
    : result.cancellation === 'unresolved' || result.output_ownership === 'unresolved'
      ? 'Cancellation could not be confirmed. Processing may still be running; retry cancellation.'
      : ['completed', 'failed'].includes(result.status) && !['held', 'unresolved'].includes(result.output_ownership ?? '')
        ? 'The run has already finished.'
        : 'Cancellation requested. Waiting for processing to stop.';
  return { stopped, message };
}
