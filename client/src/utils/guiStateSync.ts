import type { GuiStateSyncResponse } from '../types';

/** One scoped, serial command exchange; disposed replies are never acknowledged. */
export function startGuiStateSync<T>(options: {
  payload: () => T;
  sync: (payload: T & { acknowledged_command_ids: string[] }) => Promise<GuiStateSyncResponse>;
  onResponse: (response: GuiStateSyncResponse) => void;
  onError?: (error: unknown) => void;
  intervalMs?: number;
}): () => void {
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let acknowledged: string[] = [];
  const exchange = async () => {
    try {
      const response = await options.sync({ ...options.payload(), acknowledged_command_ids: acknowledged });
      if (disposed) return;
      options.onResponse(response);
      acknowledged = response.commands.map(command => command.id);
    } catch (error) {
      if (!disposed) options.onError?.(error);
    } finally {
      if (!disposed) timer = setTimeout(() => { void exchange(); }, options.intervalMs ?? 2000);
    }
  };
  void exchange();
  return () => { disposed = true; clearTimeout(timer); };
}
