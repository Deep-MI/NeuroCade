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

interface PendingWindowingTransaction {
  id: string;
  calMin?: number;
  calMax?: number;
  globalMin?: number;
  globalMax?: number;
  sourceSliceType?: ViewerSliceType;
}

export function useViewerWindowing({
  volumes,
  instancesRef,
  anyInstance,
  scheduleInstanceLayerRefresh,
}: UseViewerWindowingOptions) {
  const manualWindowingRef = useRef<Set<string>>(new Set());
  const windowingTransactionFrameRef = useRef<number | null>(null);
  const pendingWindowingTransactionsRef = useRef<Map<string, PendingWindowingTransaction>>(new Map());
  const [windowings, setWindowings] = useState<Record<string, WindowSetting>>({});

  const scheduleWindowingTransaction = useCallback((transaction: PendingWindowingTransaction) => {
    const pending = pendingWindowingTransactionsRef.current;
    const current = pending.get(transaction.id);
    pending.set(transaction.id, {
      id: transaction.id,
      calMin: transaction.calMin ?? current?.calMin,
      calMax: transaction.calMax ?? current?.calMax,
      globalMin: transaction.globalMin ?? current?.globalMin,
      globalMax: transaction.globalMax ?? current?.globalMax,
      sourceSliceType: current?.sourceSliceType ?? transaction.sourceSliceType,
    });
    if (windowingTransactionFrameRef.current !== null) return;

    windowingTransactionFrameRef.current = window.requestAnimationFrame(() => {
      windowingTransactionFrameRef.current = null;
      const transactions = [...pendingWindowingTransactionsRef.current.values()];
      pendingWindowingTransactionsRef.current.clear();

      for (const item of transactions) {
        if (item.calMin !== undefined && item.calMax !== undefined) {
          if (!Number.isFinite(item.calMin) || !Number.isFinite(item.calMax) || item.calMin === item.calMax) continue;
        }
        for (const [sliceType, nv] of instancesRef.current.entries()) {
          if (item.sourceSliceType !== undefined && sliceType === item.sourceSliceType) continue;
          const loaded = asNiivueInterop(nv).volumes.find((volume) => volume.id === item.id);
          if (!loaded) continue;
          const nextMin = item.calMin ?? loaded.cal_min;
          const nextMax = item.calMax ?? loaded.cal_max;
          if (nextMin === undefined || nextMax === undefined) continue;
          if (!Number.isFinite(nextMin) || !Number.isFinite(nextMax) || nextMin === nextMax) continue;
          if (loaded.cal_min === nextMin && loaded.cal_max === nextMax) continue;
          loaded.cal_min = nextMin;
          loaded.cal_max = nextMax;
          scheduleInstanceLayerRefresh(sliceType, nv, loaded);
        }
      }

      setWindowings((prev) => {
        let changed = false;
        const next = { ...prev };
        for (const item of transactions) {
          const current = prev[item.id] ?? { calMin: 0, calMax: 1, globalMin: item.globalMin ?? 0, globalMax: item.globalMax ?? 1 };
          const value = {
            calMin: item.calMin ?? current.calMin,
            calMax: item.calMax ?? current.calMax,
            globalMin: item.globalMin ?? current.globalMin,
            globalMax: item.globalMax ?? current.globalMax,
          };
          if (
            current.calMin === value.calMin &&
            current.calMax === value.calMax &&
            current.globalMin === value.globalMin &&
            current.globalMax === value.globalMax
          ) {
            continue;
          }
          next[item.id] = value;
          changed = true;
        }
        return changed ? next : prev;
      });
    });
  }, [instancesRef, scheduleInstanceLayerRefresh]);

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
    scheduleWindowingTransaction({ id, [field]: value });
  }, [scheduleWindowingTransaction]);

  const syncIntensityWindow = useCallback((sourceSliceType: ViewerSliceType, sourceLoaded: NiivueVolumeInterop) => {
    const id = sourceLoaded.id;
    const calMin = sourceLoaded.cal_min;
    const calMax = sourceLoaded.cal_max;
    if (!id || calMin === undefined || calMax === undefined) return;
    if (!Number.isFinite(calMin) || !Number.isFinite(calMax) || calMin === calMax) return;
    const source = volumes.find((volume) => volume.id === id);
    if (!source || isSurfaceLayer(source) || source.type === 'segmentation') return;

    manualWindowingRef.current.add(id);
    scheduleWindowingTransaction({
      id,
      calMin,
      calMax,
      globalMin: sourceLoaded.global_min ?? calMin,
      globalMax: sourceLoaded.global_max ?? calMax,
      sourceSliceType,
    });
  }, [scheduleWindowingTransaction, volumes]);

  const clearManualWindowing = useCallback(() => {
    manualWindowingRef.current.clear();
  }, []);

  useEffect(() => () => {
    if (windowingTransactionFrameRef.current !== null) {
      window.cancelAnimationFrame(windowingTransactionFrameRef.current);
      windowingTransactionFrameRef.current = null;
    }
    pendingWindowingTransactionsRef.current.clear();
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
