import { useCallback, useEffect, useRef } from 'react';
import type Niivue from '@niivue/niivue';

import { asNiivueInterop } from '../utils/niivueInterop';

export function useViewerPaneInstance() {
  const instanceRef = useRef<Niivue | null>(null);
  const refreshFrameRef = useRef<number | null>(null);
  const drawFrameRef = useRef<number | null>(null);

  const scheduleRefresh = useCallback((nv: Niivue) => {
    if (refreshFrameRef.current !== null) return;
    refreshFrameRef.current = window.requestAnimationFrame(() => {
      refreshFrameRef.current = null;
      void nv.updateGLVolume();
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
    if (drawFrameRef.current !== null) window.cancelAnimationFrame(drawFrameRef.current);
  }, []);

  return {
    instanceRef,
    scheduleRefresh,
    scheduleDraw,
  };
}
