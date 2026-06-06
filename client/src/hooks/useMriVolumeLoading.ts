import { useCallback, useEffect, useRef, useState } from 'react';

import type { Volume } from '../types';
import { appFetchUrl } from '../utils/api';
import { getVolumeWorkerPool, type VolumeLoadPriority } from '../utils/VolumeWorkerPool';
import type { VolumeData } from '../utils/VolumeLoader';

interface UseMriVolumeLoadingOptions {
  volumeLayers: Volume[];
  baseVolumeUrl: string | null;
  backgroundPreloadLimit: number;
  onVolumeLutDetected?: (volumeId: string, detectedLut: 'binary' | 'freesurfer' | undefined) => void;
}

interface LoadVolumeOptions {
  signal?: AbortSignal;
  priority?: VolumeLoadPriority;
}

function volumeLoadPriority(volume: Volume, baseVolumeUrl: string | null): VolumeLoadPriority {
  if (volume.url === baseVolumeUrl || (volume.type ?? 'intensity') === 'intensity') {
    return 'foreground';
  }
  if (volume.type === 'segmentation') {
    return 'segmentation';
  }
  return 'background';
}

function pushUnique(queue: Volume[], seen: Set<string>, volume: Volume | undefined) {
  if (!volume || seen.has(volume.url)) return;
  seen.add(volume.url);
  queue.push(volume);
}

export function useMriVolumeLoading({
  volumeLayers,
  baseVolumeUrl,
  backgroundPreloadLimit,
  onVolumeLutDetected,
}: UseMriVolumeLoadingOptions) {
  const [loadedVolumes, setLoadedVolumes] = useState<Map<string, VolumeData>>(new Map());
  const [loadingVolumes, setLoadingVolumes] = useState<Set<string>>(new Set());
  const loadedVolumesRef = useRef<Map<string, VolumeData>>(new Map());
  const loadingRef = useRef<Set<string>>(new Set());
  const failedVolumesRef = useRef<Set<string>>(new Set());
  const backgroundSkippedVolumesRef = useRef<Set<string>>(new Set());
  const backgroundLoadRef = useRef<{ url: string; controller: AbortController; signal: AbortSignal } | null>(null);
  const foregroundControllersRef = useRef<Map<string, AbortController>>(new Map());

  useEffect(() => {
    loadedVolumesRef.current = loadedVolumes;
  }, [loadedVolumes]);

  const loadVolume = useCallback(async (volume: Volume, options: LoadVolumeOptions = {}) => {
    const { signal, priority = 'foreground' } = options;
    const controller = signal ? null : new AbortController();
    const loadSignal = signal ?? controller?.signal;
    if (loadSignal?.aborted) return;
    if (loadedVolumesRef.current.has(volume.url) || loadingRef.current.has(volume.url) || failedVolumesRef.current.has(volume.url)) return;
    if (controller) {
      foregroundControllersRef.current.set(volume.url, controller);
    }
    loadingRef.current.add(volume.url);
    setLoadingVolumes(prev => {
      const next = new Set(prev);
      next.add(volume.url);
      return next;
    });

    try {
      const response = await appFetchUrl(volume.url, loadSignal ? { signal: loadSignal } : {});
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

      const arrayBuffer = await response.arrayBuffer();
      if (loadSignal?.aborted) return;

      const parseJob = getVolumeWorkerPool().parse({
        buffer: arrayBuffer,
        detectLut: volume.type === 'segmentation',
        priority,
      });
      const cancelParse = () => parseJob.cancel();
      loadSignal?.addEventListener('abort', cancelParse, { once: true });
      if (loadSignal?.aborted) {
        parseJob.cancel();
      }

      const { volumeData, detectedLut } = await parseJob.promise.finally(() => {
        loadSignal?.removeEventListener('abort', cancelParse);
      });
      if (volumeData) {
        failedVolumesRef.current.delete(volume.url);
        backgroundSkippedVolumesRef.current.delete(volume.url);
        setLoadedVolumes(prev => {
          const next = new Map(prev);
          next.set(volume.url, volumeData);
          return next;
        });

        if (volume.type === 'segmentation') {
          onVolumeLutDetected?.(volume.id, detectedLut);
        }
      }
    } catch (err) {
      const isAbortError = (loadSignal?.aborted ?? false) || (err instanceof Error && err.name === 'AbortError');
      if (!isAbortError) {
        console.error('[MriViewer] Error loading volume:', err);
        if (signal) {
          backgroundSkippedVolumesRef.current.add(volume.url);
        }
        const isPermanentFailure = err instanceof Error && /status:\s*(404|403|410)/.test(err.message);
        if (isPermanentFailure) {
          failedVolumesRef.current.add(volume.url);
        }
      }
    } finally {
      loadingRef.current.delete(volume.url);
      if (foregroundControllersRef.current.get(volume.url) === controller) {
        foregroundControllersRef.current.delete(volume.url);
      }
      if (backgroundLoadRef.current?.url === volume.url && backgroundLoadRef.current.signal === signal) {
        backgroundLoadRef.current = null;
      }
      setLoadingVolumes(prev => {
        if (!prev.has(volume.url)) return prev;
        const next = new Set(prev);
        next.delete(volume.url);
        return next;
      });
    }
  }, [onVolumeLutDetected]);

  useEffect(() => () => {
    for (const controller of foregroundControllersRef.current.values()) {
      controller.abort();
    }
    foregroundControllersRef.current.clear();
    backgroundLoadRef.current?.controller.abort();
    backgroundLoadRef.current = null;
  }, []);

  useEffect(() => {
    const currentUrls = new Set(volumeLayers.map(volume => volume.url));
    if (backgroundLoadRef.current && !currentUrls.has(backgroundLoadRef.current.url)) {
      backgroundLoadRef.current.controller.abort();
      backgroundLoadRef.current = null;
    }
    for (const [url, controller] of foregroundControllersRef.current) {
      if (!currentUrls.has(url)) {
        controller.abort();
        foregroundControllersRef.current.delete(url);
      }
    }
    setLoadedVolumes(prev => {
      let changed = false;
      for (const key of prev.keys()) {
        if (!currentUrls.has(key)) { changed = true; break; }
      }
      if (!changed) return prev;
      const next = new Map<string, VolumeData>();
      for (const [key, val] of prev) {
        if (currentUrls.has(key)) next.set(key, val);
      }
      return next;
    });
    for (const url of loadingRef.current) {
      if (!currentUrls.has(url)) loadingRef.current.delete(url);
    }
    failedVolumesRef.current = new Set(
      [...failedVolumesRef.current].filter(url => currentUrls.has(url)),
    );
    backgroundSkippedVolumesRef.current = new Set(
      [...backgroundSkippedVolumesRef.current].filter(url => currentUrls.has(url)),
    );
    setLoadingVolumes(prev => {
      let changed = false;
      const next = new Set<string>();
      for (const url of prev) {
        if (currentUrls.has(url)) {
          next.add(url);
        } else {
          changed = true;
        }
      }
      return changed ? next : prev;
    });

    const foregroundQueue: Volume[] = [];
    const foregroundUrls = new Set<string>();
    const baseVolume = baseVolumeUrl ? volumeLayers.find(volume => volume.url === baseVolumeUrl) : undefined;
    pushUnique(foregroundQueue, foregroundUrls, baseVolume);
    volumeLayers.forEach(volume => {
      if (volume.visible) pushUnique(foregroundQueue, foregroundUrls, volume);
    });

    const foregroundOutstanding = foregroundQueue.filter(volume => (
      !loadedVolumesRef.current.has(volume.url) && !failedVolumesRef.current.has(volume.url)
    ));
    const activeBackground = backgroundLoadRef.current;
    if (activeBackground && foregroundOutstanding.some(volume => volume.url !== activeBackground.url)) {
      activeBackground.controller.abort();
      backgroundLoadRef.current = null;
    }

    foregroundQueue.forEach(volume => {
      void loadVolume(volume, { priority: volumeLoadPriority(volume, baseVolumeUrl) });
    });

    const hasForegroundWork = foregroundQueue.some(volume => (
      !loadedVolumesRef.current.has(volume.url) && !failedVolumesRef.current.has(volume.url)
    ));
    if (hasForegroundWork || volumeLayers.length >= backgroundPreloadLimit || backgroundLoadRef.current) {
      return;
    }

    const preloadVolume = volumeLayers.find(volume => (
      !foregroundUrls.has(volume.url)
      && !loadedVolumesRef.current.has(volume.url)
      && !loadingRef.current.has(volume.url)
      && !failedVolumesRef.current.has(volume.url)
      && !backgroundSkippedVolumesRef.current.has(volume.url)
    ));
    if (!preloadVolume) return;

    const controller = new AbortController();
    backgroundLoadRef.current = {
      url: preloadVolume.url,
      controller,
      signal: controller.signal,
    };
    void loadVolume(preloadVolume, { signal: controller.signal, priority: 'background' });
  }, [backgroundPreloadLimit, baseVolumeUrl, loadedVolumes, loadingVolumes, loadVolume, volumeLayers]);

  return { loadedVolumes, loadingVolumes };
}
