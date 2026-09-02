import { createContext, useContext } from 'react';

export interface FrontendConfig {
  local_auth_enabled: boolean;
  clerk_publishable_key: string | null;
  clerk_jwt_template: string | null;
}

export const FrontendConfigContext = createContext<FrontendConfig | null>(null);

export function useFrontendConfig(): FrontendConfig {
  const config = useContext(FrontendConfigContext);
  if (!config) {
    throw new Error('useFrontendConfig must be used inside FrontendConfigProvider');
  }
  return config;
}
