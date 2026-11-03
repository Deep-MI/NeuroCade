export interface RunCancellation {
  run_id: string;
  status: string;
  cancellation: 'requested' | 'stopped' | null;
}

export function describeCancellation(result: RunCancellation) {
  const stopped = result.status === 'canceled' && result.cancellation === 'stopped';
  const message = stopped ? 'Run canceled by user.'
    : ['completed', 'failed'].includes(result.status)
      ? 'The run has already finished.'
      : 'Cancellation requested. Waiting for processing to stop.';
  return { stopped, message };
}
