import { useEffect, useRef, useState, type PropsWithChildren } from 'react';
import { AppPreferencesContext, type AppPreferences } from '../hooks/useAppPreferences';
import { appJson, jsonRequest } from '../utils/api';

export function AppPreferencesProvider({ children, enabled }: PropsWithChildren<{ enabled: boolean }>) {
  const [preferences, setPreferences] = useState<AppPreferences>({ light_mode: false, assistant_approval: true });
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const saving = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const refresh = async () => {
      if (saving.current) return;
      const version = ++generation.current;
      try {
        const result = await appJson<AppPreferences>('/preferences', 'Could not load settings');
        if (!cancelled && version === generation.current) { setPreferences(result); setError(''); }
      } catch (err) {
        if (!cancelled && version === generation.current) setError(String(err));
      } finally {
        if (!cancelled && version === generation.current) setBusy(false);
      }
    };
    void refresh();
    const onFocus = () => { void refresh(); };
    window.addEventListener('focus', onFocus);
    return () => { cancelled = true; window.removeEventListener('focus', onFocus); };
  }, [enabled]);
  const save = async (key: keyof AppPreferences, value: boolean) => {
    if (busy || saving.current || !enabled) return;
    saving.current = true;
    ++generation.current;
    setBusy(true);
    try {
      setPreferences(await appJson<AppPreferences>('/preferences', 'Could not save settings', jsonRequest({ [key]: value }, { method: 'PATCH' })));
      setError('');
    } catch (err) { setError(String(err)); }
    finally { saving.current = false; setBusy(false); }
  };
  return <AppPreferencesContext.Provider value={{ preferences, save: (key, value) => { void save(key, value); }, busy, error }}>{children}</AppPreferencesContext.Provider>;
}
