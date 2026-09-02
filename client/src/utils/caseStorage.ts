/* ------------------------------------------------------------------ */
/*  localStorage persistence helpers for per-case volume display state */
/* ------------------------------------------------------------------ */
import type { Volume, CaseState, PersistedVolume } from '../types.js';

const CASE_KEY_PREFIX = 'neurocade-case-v1-';
const CASE_CLOSED_KEY_PREFIX = 'neurocade-case-closed-v1-';

/**
 * Persist the current volume display state for a case.
 * Volumes with ephemeral `blob:` URLs (local uploads) are skipped because
 * they cannot be restored after a page reload.
 */
export function saveCaseState(caseId: string, volumes: Volume[]): void {
  const persistable = volumes.filter(v => !v.url.startsWith('blob:'));
  const cs: CaseState = {
    version: 1,
    caseId,
    volumes: persistable.map((v): PersistedVolume => {
      const base = {
        id: v.id,
        filename: v.filename,
        name: v.name,
        url: v.url,
        visible: v.visible,
        opacity: v.opacity,
      };

      if (v.type === 'surface') {
        return {
          ...base,
          type: 'surface',
          surfaceColorMode: v.surfaceColorMode,
          curvatureUrl: v.curvatureUrl,
          annotationUrl: v.annotationUrl,
          curvatureNegativeThreshold: v.curvatureNegativeThreshold,
          curvaturePositiveThreshold: v.curvaturePositiveThreshold,
        };
      }

      if (v.type === 'segmentation') {
        return {
          ...base,
          type: 'segmentation',
          lut: v.lut,
          customLutUrl: v.customLutUrl,
          brightness: v.brightness ?? 0,
          contrast: v.contrast ?? 1.0,
        };
      }

      return {
        ...base,
        type: 'intensity',
        brightness: v.brightness ?? 0,
        contrast: v.contrast ?? 1.0,
      };
    }),
    lastAccessed: Date.now(),
  };
  try {
    localStorage.setItem(CASE_KEY_PREFIX + caseId, JSON.stringify(cs));
  } catch {
    /* quota exceeded – best-effort */
  }
}

/** Load the persisted state for a case, or `null` if none exists. */
export function loadCaseState(caseId: string): CaseState | null {
  try {
    const raw = localStorage.getItem(CASE_KEY_PREFIX + caseId);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      !parsed
      || typeof parsed !== 'object'
      || (parsed as Partial<CaseState>).version !== 1
      || (parsed as Partial<CaseState>).caseId !== caseId
      || !Array.isArray((parsed as Partial<CaseState>).volumes)
    ) {
      localStorage.removeItem(CASE_KEY_PREFIX + caseId);
      return null;
    }
    return parsed as CaseState;
  } catch {
    return null;
  }
}

/** Remove the persisted state for a case (e.g. after deletion). */
export function removeCaseState(caseId: string): void {
  try {
    localStorage.removeItem(CASE_KEY_PREFIX + caseId);
    localStorage.removeItem(CASE_CLOSED_KEY_PREFIX + caseId);
  } catch {
    /* ignore */
  }
}

export function loadClosedCaseVolumes(caseId: string): string[] {
  try {
    const raw = localStorage.getItem(CASE_CLOSED_KEY_PREFIX + caseId);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is string => typeof value === 'string' && value.length > 0);
  } catch {
    return [];
  }
}

function saveClosedCaseVolumes(caseId: string, filenames: string[]): void {
  try {
    if (filenames.length === 0) {
      localStorage.removeItem(CASE_CLOSED_KEY_PREFIX + caseId);
      return;
    }
    localStorage.setItem(CASE_CLOSED_KEY_PREFIX + caseId, JSON.stringify([...new Set(filenames)]));
  } catch {
    /* ignore */
  }
}

export function rememberClosedCaseVolume(caseId: string, filename: string): void {
  if (!filename) return;
  const existing = loadClosedCaseVolumes(caseId);
  if (existing.includes(filename)) return;
  saveClosedCaseVolumes(caseId, [...existing, filename]);
}

export function forgetClosedCaseVolume(caseId: string, filename: string): void {
  if (!filename) return;
  const next = loadClosedCaseVolumes(caseId).filter((value) => value !== filename);
  saveClosedCaseVolumes(caseId, next);
}
