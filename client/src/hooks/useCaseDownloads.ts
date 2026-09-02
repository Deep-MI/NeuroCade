import { useCallback, useEffect, useRef, useState } from 'react';

import { isSegmentationLayer, type ArtifactListItem, type Volume } from '../types';
import {
  downloadArtifactFile,
  downloadCaseArchive,
  fetchCaseArtifacts,
} from '../utils/api';

const DOWNLOAD_ARTIFACT_TIMEOUT_MS = 8000;

interface UseCaseDownloadsOptions {
  caseId: string | null;
  caseTitle: string | null;
  volumes: Volume[];
}

export function useCaseDownloads({ caseId, caseTitle, volumes }: UseCaseDownloadsOptions) {
  const [isOpen, setIsOpen] = useState(false);
  const [artifacts, setArtifacts] = useState<ArtifactListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<'volume' | 'case' | null>(null);
  const requestIdRef = useRef(0);

  useEffect(() => {
    requestIdRef.current += 1;
    setIsOpen(false);
    setArtifacts([]);
    setError(null);
    setAction(null);
    setLoading(false);
  }, [caseId]);

  const fallbackArtifacts = useCallback((): ArtifactListItem[] => (
    volumes
      .filter((volume) => Boolean(volume.url))
      .map((volume) => ({
        id: volume.id,
        name: volume.filename,
        kind: 'volume',
        downloadPath: volume.url,
        metadata: {
          volume_role: volume.type === 'segmentation' ? 'segmentation' : 'intensity',
          lut: isSegmentationLayer(volume) ? volume.lut : undefined,
          customLutDownloadUrl: isSegmentationLayer(volume) ? volume.customLutUrl : undefined,
          visible: volume.visible,
        },
      }))
  ), [volumes]);

  const close = useCallback(() => {
    requestIdRef.current += 1;
    setIsOpen(false);
    setLoading(false);
    setAction(null);
    setError(null);
  }, []);

  const open = useCallback(() => {
    if (!caseId) return;
    const fallback = fallbackArtifacts();
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setIsOpen(true);
    setArtifacts(fallback);
    setLoading(true);
    setError(null);
    const timeout = new Promise<ArtifactListItem[]>((_, reject) => {
      window.setTimeout(() => reject(new Error('Timed out while loading the full volume list. You can still download the whole folder or any volume already open.')), DOWNLOAD_ARTIFACT_TIMEOUT_MS);
    });
    void Promise.race([fetchCaseArtifacts(caseId), timeout])
      .then((entries) => {
        if (requestIdRef.current !== requestId) return;
        setArtifacts(entries.length > 0 ? entries : fallback);
      })
      .catch((reason: unknown) => {
        if (requestIdRef.current !== requestId) return;
        const message = reason instanceof Error ? reason.message : String(reason);
        setError(fallback.length > 0 ? `${message} Showing currently loaded volumes.` : message);
      })
      .finally(() => {
        if (requestIdRef.current === requestId) setLoading(false);
      });
  }, [caseId, fallbackArtifacts]);

  const downloadVolume = useCallback(async (artifactId: string) => {
    const artifact = artifacts.find((entry) => entry.id === artifactId);
    if (!artifact) {
      setError('Select a volume to download.');
      return;
    }
    setAction('volume');
    setError(null);
    try {
      await downloadArtifactFile(artifact);
      close();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAction(null);
    }
  }, [artifacts, close]);

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

  return { isOpen, artifacts, loading, error, action, open, close, downloadVolume, downloadArchive };
}
