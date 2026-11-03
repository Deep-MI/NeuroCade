/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  neurocadeElectron?: {
    platform: 'aix' | 'darwin' | 'freebsd' | 'linux' | 'openbsd' | 'sunos' | 'win32' | 'cygwin' | 'netbsd';
    setTitlebarTheme: (theme: 'dark' | 'light') => void;
    selectLinkedDirectory: () => Promise<{
      protocol_version: string;
      source_id: string;
      name: string;
      available: boolean;
      created_at?: number;
      summary?: {
        format: string;
        scope: string;
        subject_ids: string[];
        file_count: number;
        truncated: boolean;
      };
    } | null>;
  };
}
