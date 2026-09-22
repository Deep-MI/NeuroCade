import { useCallback, useEffect, useRef, useState } from 'react';

import type { CaseFileEntry } from '../types';
import {
  downloadCaseFile,
  downloadCaseArchive,
  fetchCaseFiles,
} from '../utils/api';

const DOWNLOAD_ARTIFACT_TIMEOUT_MS = 8000;

interface UseCaseDownloadsOptions {
  caseId: string | null;
  caseTitle: string | null;
}

export function useCaseDownloads({ caseId, caseTitle }: UseCaseDownloadsOptions) {
  const [isOpen, setIsOpen] = useState(false);
  const [files, setFiles] = useState<CaseFileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<'file' | 'case' | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    requestIdRef.current += 1;
    setIsOpen(false);
    setFiles([]);
    setError(null);
    setAction(null);
    setLoading(false);
  }, [caseId]);

  const close = useCallback(() => {
    requestIdRef.current += 1;
    setIsOpen(false);
    setLoading(false);
    setAction(null);
    setError(null);
  }, []);

  const open = useCallback(() => {
    if (!caseId) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setIsOpen(true);
    setFiles([]);
    setLoading(true);
    setError(null);
    const timeout = new Promise<CaseFileEntry[]>((_, reject) => {
      window.setTimeout(() => reject(new Error('Timed out while loading the case file list. You can still download the whole folder.')), DOWNLOAD_ARTIFACT_TIMEOUT_MS);
    });
    void Promise.race([fetchCaseFiles(caseId), timeout])
      .then((entries) => {
        if (requestIdRef.current !== requestId) return;
        setFiles(entries);
      })
      .catch((reason: unknown) => {
        if (requestIdRef.current !== requestId) return;
        const message = reason instanceof Error ? reason.message : String(reason);
        setError(message);
      })
      .finally(() => {
        if (requestIdRef.current === requestId) setLoading(false);
      });
  }, [caseId]);

  const downloadFile = useCallback(async (file: CaseFileEntry) => {
    if (!caseId) {
      setError('No active case selected for download.');
      return;
    }
    setAction('file');
    setError(null);
    try {
      await downloadCaseFile(caseId, file);
      close();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAction(null);
    }
  }, [caseId, close]);

  const downloadArchive = useCallback(async () => {
    if (!caseId) {
      setError('No active case selected for download.');
      return;
    }
    setAction('case');
    setError(null);
    try {
      await downloadCaseArchive(caseId, caseTitle ?? caseId);
      close();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAction(null);
    }
  }, [caseId, caseTitle, close]);

  return { isOpen, files, loading, error, action, open, close, downloadFile, downloadArchive };
}
