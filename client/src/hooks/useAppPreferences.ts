import { createContext, useContext } from 'react';

export interface AppPreferences { light_mode: boolean; assistant_approval: boolean }
export const AppPreferencesContext = createContext<{
  preferences: AppPreferences;
  save: (key: keyof AppPreferences, value: boolean) => void;
  busy: boolean;
  error: string;
}>({ preferences: { light_mode: false, assistant_approval: true }, save: () => { throw new Error("Preferences provider is missing"); }, busy: true, error: '' });

export function useAppAppearance() {
  const { preferences, save } = useContext(AppPreferencesContext);
  return [preferences.light_mode, (value: boolean) => save('light_mode', value)] as const;
}
export function useAssistantApproval() {
  const { preferences, save } = useContext(AppPreferencesContext);
  return [preferences.assistant_approval, (value: boolean) => save('assistant_approval', value)] as const;
}
export function usePreferenceStatus() {
  const { busy, error } = useContext(AppPreferencesContext);
  return { busy, error };
}
