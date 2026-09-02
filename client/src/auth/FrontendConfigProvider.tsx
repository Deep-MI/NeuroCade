import { useEffect, useState, type PropsWithChildren } from 'react';

import { FrontendConfigContext, type FrontendConfig } from './frontendConfigContext';

export function FrontendConfigProvider({ children }: PropsWithChildren) {
  const [config, setConfig] = useState<FrontendConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    fetch('/api/app/frontend-config', {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
      .then(async response => {
        if (!response.ok) {
          throw new Error(`Configuration request failed (${response.status})`);
        }
        return response.json() as Promise<FrontendConfig>;
      })
      .then(setConfig)
      .catch(reason => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });

    return () => controller.abort();
  }, []);

  if (error) {
    return <div className="nc-app-page px-6 py-10 text-sm text-[var(--nc-danger)]">Failed to load application configuration: {error}</div>;
  }
  if (!config) {
    return <div className="nc-app-page px-6 py-10 text-sm text-[var(--nc-tx-muted)]">Loading application...</div>;
  }
  return <FrontendConfigContext.Provider value={config}>{children}</FrontendConfigContext.Provider>;
}
