import { useCallback, useEffect, useState } from 'react';

import { fetchProviders } from '../utils/api';

interface AssistantProviderStatus {
  disabledMessage: string | null;
  retryable: boolean;
  retry: () => void;
}

export function useAssistantProviderStatus(): AssistantProviderStatus {
  const [disabledMessage, setDisabledMessage] = useState<string | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void fetchProviders()
      .then((providers) => {
        if (cancelled) return;
        const provider = providers.find((candidate) => candidate.is_default);
        if (provider?.provider === 'no-llm' || provider?.provider_family === 'none') {
          setDisabledMessage('Assistant is disabled because LLM setup was skipped. You can still upload, view, and process cases.');
          setRetryable(false);
        } else if (!provider?.configured) {
          setDisabledMessage('Assistant is disabled because no LLM provider is configured. You can still upload, view, and process cases.');
          setRetryable(false);
        } else if (!provider.reachable) {
          setDisabledMessage('The configured model provider is temporarily unreachable. Check the provider and try again.');
          setRetryable(true);
        } else {
          setDisabledMessage(null);
          setRetryable(false);
        }
      })
      .catch((error) => {
        if (cancelled) return;
        console.error('Failed to load provider configuration:', error);
        setDisabledMessage(null);
        setRetryable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const retry = useCallback(() => setRefreshKey((value) => value + 1), []);
  return { disabledMessage, retryable, retry };
}
