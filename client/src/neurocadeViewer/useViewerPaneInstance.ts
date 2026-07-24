import { useCallback, useEffect, useRef } from 'react';
import Niivue from '@niivue/niivue';

import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';

export function useViewerPaneInstance() {
  const instanceRef = useRef<Niivue | null>(null);
  const refreshFrameRef = useRef<number | null>(null);
  const layerRefreshFrameRef = useRef<number | null>(null);
  const pendingLayerRefreshesRef = useRef<Set<NiivueVolumeInterop>>(new Set());
  const drawFrameRef = useRef<number | null>(null);

  const scheduleRefresh = useCallback((nv: Niivue) => {
    if (refreshFrameRef.current !== null) return;
    refreshFrameRef.current = window.requestAnimationFrame(() => {
      refreshFrameRef.current = null;
      void nv.updateGLVolume();
    });
  }, []);

  const scheduleLayerRefresh = useCallback((nv: Niivue, loaded: NiivueVolumeInterop) => {
    pendingLayerRefreshesRef.current.add(loaded);
    if (layerRefreshFrameRef.current !== null) return;
    layerRefreshFrameRef.current = window.requestAnimationFrame(() => {
      layerRefreshFrameRef.current = null;
      const pending = [...pendingLayerRefreshesRef.current];
      pendingLayerRefreshesRef.current.clear();
      for (const volume of pending) {
        const volumeIndex = nv.volumes.indexOf(volume);
        if (volumeIndex >= 0) {
          void nv.setVolume(volumeIndex, {
            calMin: volume.calMin,
            calMax: volume.calMax,
            opacity: volume.opacity,
          });
        }
      }
    });
  }, []);

  const scheduleDraw = useCallback((nv: Niivue) => {
    if (drawFrameRef.current !== null) return;
    drawFrameRef.current = window.requestAnimationFrame(() => {
      drawFrameRef.current = null;
      asNiivueInterop(nv).drawScene?.();
    });
  }, []);

  useEffect(() => () => {
    if (refreshFrameRef.current !== null) window.cancelAnimationFrame(refreshFrameRef.current);
    if (layerRefreshFrameRef.current !== null) window.cancelAnimationFrame(layerRefreshFrameRef.current);
    if (drawFrameRef.current !== null) window.cancelAnimationFrame(drawFrameRef.current);
    pendingLayerRefreshesRef.current.clear();
  }, []);

  return {
    instanceRef,
    scheduleRefresh,
    scheduleLayerRefresh,
    scheduleDraw,
  };
}
