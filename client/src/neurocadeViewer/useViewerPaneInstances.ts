import { useCallback, useEffect, useRef, useState } from 'react';
import { Niivue } from '@niivue/niivue';

import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import { logNiivueWindowingPerf, refreshNiivueWindowingOrLayerStack } from './niivueWindowingRefresh';
import type { ViewerSliceType } from './viewerControls';

interface LayerRefreshBatch {
  frame: number;
  volumes: Set<NiivueVolumeInterop>;
}

export function useViewerPaneInstances() {
  const instancesRef = useRef<Map<ViewerSliceType, Niivue>>(new Map());
  const refreshFrameRefs = useRef<Map<ViewerSliceType, number>>(new Map());
  const layerRefreshFrameRefs = useRef<Map<ViewerSliceType, LayerRefreshBatch>>(new Map());
  const drawFrameRefs = useRef<Map<ViewerSliceType, number>>(new Map());
  const [instancesVersion, setInstancesVersion] = useState(0);

  const anyInstance = useCallback((): Niivue | null => {
    return instancesRef.current.get(0) ?? [...instancesRef.current.values()][0] ?? null;
  }, []);

  const bumpInstancesVersion = useCallback(() => {
    setInstancesVersion((version) => version + 1);
  }, []);

  const scheduleInstanceRefresh = useCallback((sliceType: ViewerSliceType, nv: Niivue) => {
    if (refreshFrameRefs.current.has(sliceType)) return;
    const frame = window.requestAnimationFrame(() => {
      refreshFrameRefs.current.delete(sliceType);
      nv.updateGLVolume();
    });
    refreshFrameRefs.current.set(sliceType, frame);
  }, []);

  const scheduleInstanceLayerRefresh = useCallback((sliceType: ViewerSliceType, nv: Niivue, loaded: NiivueVolumeInterop) => {
    const existing = layerRefreshFrameRefs.current.get(sliceType);
    if (existing) {
      existing.volumes.add(loaded);
      return;
    }
    const volumes = new Set<NiivueVolumeInterop>([loaded]);
    const frame = window.requestAnimationFrame(() => {
      const batchStart = performance.now();
      layerRefreshFrameRefs.current.delete(sliceType);
      const interop = asNiivueInterop(nv);
      if (typeof interop.refreshLayers !== 'function') {
        nv.updateGLVolume();
        return;
      }
      refreshNiivueWindowingOrLayerStack(nv, volumes, { sliceType, source: 'scheduled-layer-refresh' });
      const drawStart = performance.now();
      interop.drawScene?.();
      logNiivueWindowingPerf('drawScene', performance.now() - drawStart, { sliceType });
      logNiivueWindowingPerf('batch', performance.now() - batchStart, {
        sliceType,
        volumes: volumes.size,
      });
    });
    layerRefreshFrameRefs.current.set(sliceType, { frame, volumes });
  }, []);

  const scheduleInstanceDraw = useCallback((sliceType: ViewerSliceType, nv: Niivue) => {
    if (drawFrameRefs.current.has(sliceType)) return;
    const frame = window.requestAnimationFrame(() => {
      drawFrameRefs.current.delete(sliceType);
      asNiivueInterop(nv).drawScene?.();
    });
    drawFrameRefs.current.set(sliceType, frame);
  }, []);

  useEffect(() => {
    const instances = [...instancesRef.current.values()];
    if (instances.length < 2) return;
    for (const instance of instances) {
      const others = instances.filter((other) => other !== instance);
      (instance as unknown as { broadcastTo?: (others: Niivue[], opts?: object) => void })
        .broadcastTo?.(others, { '2d': true, '3d': true });
    }
  }, [instancesVersion]);

  useEffect(() => () => {
    for (const frame of refreshFrameRefs.current.values()) {
      window.cancelAnimationFrame(frame);
    }
    refreshFrameRefs.current.clear();
    for (const batch of layerRefreshFrameRefs.current.values()) {
      window.cancelAnimationFrame(batch.frame);
    }
    layerRefreshFrameRefs.current.clear();
    for (const frame of drawFrameRefs.current.values()) {
      window.cancelAnimationFrame(frame);
    }
    drawFrameRefs.current.clear();
  }, []);

  return {
    instancesRef,
    anyInstance,
    bumpInstancesVersion,
    scheduleInstanceRefresh,
    scheduleInstanceLayerRefresh,
    scheduleInstanceDraw,
  };
}
