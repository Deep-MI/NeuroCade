import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { Niivue } from '@niivue/niivue';

import { isSurfaceLayer, type Volume } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import type { WindowSetting } from './paneSyncKeys';
import type { ViewerSliceType } from './viewerControls';

interface UseViewerWindowingOptions {
  volumes: Volume[];
  instancesRef: MutableRefObject<Map<ViewerSliceType, Niivue>>;
  anyInstance: () => Niivue | null;
  scheduleInstanceLayerRefresh: (sliceType: ViewerSliceType, nv: Niivue, loaded: NiivueVolumeInterop) => void;
}

export function useViewerWindowing({
  volumes,
  instancesRef,
  anyInstance,
  scheduleInstanceLayerRefresh,
}: UseViewerWindowingOptions) {
  const manualWindowingRef = useRef<Set<string>>(new Set());
  const windowingStateFrameRef = useRef<number | null>(null);
  const pendingWindowingStateRef = useRef<Record<string, WindowSetting>>({});
  const [windowings, setWindowings] = useState<Record<string, WindowSetting>>({});

  const scheduleWindowingState = useCallback((id: string, setting: WindowSetting) => {
    pendingWindowingStateRef.current[id] = setting;
    if (windowingStateFrameRef.current !== null) return;
    windowingStateFrameRef.current = window.requestAnimationFrame(() => {
      windowingStateFrameRef.current = null;
      const pending = pendingWindowingStateRef.current;
      pendingWindowingStateRef.current = {};
      setWindowings((prev) => {
        let changed = false;
        const next = { ...prev };
        for (const [volumeId, value] of Object.entries(pending)) {
          const current = prev[volumeId];
          if (
            current?.calMin === value.calMin &&
            current.calMax === value.calMax &&
            current.globalMin === value.globalMin &&
            current.globalMax === value.globalMax
          ) {
            continue;
          }
          next[volumeId] = value;
          changed = true;
        }
        return changed ? next : prev;
      });
    });
  }, []);

  const ensureWindowingForLayer = useCallback((id: string) => {
    setWindowings((prev) => {
      if (prev[id]) return prev;
      const nv = anyInstance();
      if (!nv) return prev;
      const loaded = asNiivueInterop(nv).volumes.find((volume) => volume.id === id);
      if (!loaded) return prev;
      return {
        ...prev,
        [id]: {
          calMin: loaded.cal_min ?? loaded.global_min ?? 0,
          calMax: loaded.cal_max ?? loaded.global_max ?? 1,
          globalMin: loaded.global_min ?? 0,
          globalMax: loaded.global_max ?? 1,
        },
      };
    });
  }, [anyInstance]);

  const updateWindowing = useCallback((id: string, field: 'calMin' | 'calMax', value: number) => {
    manualWindowingRef.current.add(id);
    for (const [sliceType, nv] of instancesRef.current.entries()) {
      const loaded = asNiivueInterop(nv).volumes.find((volume) => volume.id === id);
      if (!loaded) continue;
      const current = field === 'calMin' ? loaded.cal_min : loaded.cal_max;
      if (current === value) continue;
      if (field === 'calMin') loaded.cal_min = value;
      if (field === 'calMax') loaded.cal_max = value;
      scheduleInstanceLayerRefresh(sliceType, nv, loaded);
    }
    setWindowings((prev) => {
      const current = prev[id] ?? { calMin: 0, calMax: 1, globalMin: 0, globalMax: 1 };
      if (current[field] === value) return prev;
      return { ...prev, [id]: { ...current, [field]: value } };
    });
  }, [instancesRef, scheduleInstanceLayerRefresh]);

  const syncIntensityWindow = useCallback((sourceSliceType: ViewerSliceType, sourceLoaded: NiivueVolumeInterop) => {
    const id = sourceLoaded.id;
    const calMin = sourceLoaded.cal_min;
    const calMax = sourceLoaded.cal_max;
    if (!id || calMin === undefined || calMax === undefined) return;
    if (!Number.isFinite(calMin) || !Number.isFinite(calMax) || calMin >= calMax) return;
    const source = volumes.find((volume) => volume.id === id);
    if (!source || isSurfaceLayer(source) || source.type === 'segmentation') return;

    manualWindowingRef.current.add(id);
    for (const [sliceType, nv] of instancesRef.current.entries()) {
      if (sliceType === sourceSliceType) continue;
      const loaded = asNiivueInterop(nv).volumes.find((volume) => volume.id === id);
      if (!loaded || (loaded.cal_min === calMin && loaded.cal_max === calMax)) continue;
      loaded.cal_min = calMin;
      loaded.cal_max = calMax;
      scheduleInstanceLayerRefresh(sliceType, nv, loaded);
    }

    scheduleWindowingState(id, {
      calMin,
      calMax,
      globalMin: sourceLoaded.global_min ?? calMin,
      globalMax: sourceLoaded.global_max ?? calMax,
    });
  }, [instancesRef, scheduleInstanceLayerRefresh, scheduleWindowingState, volumes]);

  const clearManualWindowing = useCallback(() => {
    manualWindowingRef.current.clear();
  }, []);

  useEffect(() => () => {
    if (windowingStateFrameRef.current !== null) {
      window.cancelAnimationFrame(windowingStateFrameRef.current);
      windowingStateFrameRef.current = null;
    }
    pendingWindowingStateRef.current = {};
  }, []);

  return {
    manualWindowingRef,
    windowings,
    setWindowings,
    ensureWindowingForLayer,
    updateWindowing,
    syncIntensityWindow,
    clearManualWindowing,
  };
}
