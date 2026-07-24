import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import Niivue from '@niivue/niivue';

import { isSurfaceLayer, type Volume } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import type { WindowSetting } from './paneSyncKeys';

interface UseViewerWindowingOptions {
  volumes: Volume[];
  instanceRef: MutableRefObject<Niivue | null>;
  scheduleLayerRefresh: (nv: Niivue, loaded: NiivueVolumeInterop) => void;
}

interface PendingWindowingTransaction {
  id: string;
  calMin?: number;
  calMax?: number;
  globalMin?: number;
  globalMax?: number;
}

export function useViewerWindowing({
  volumes,
  instanceRef,
  scheduleLayerRefresh,
}: UseViewerWindowingOptions) {
  const manualWindowingRef = useRef<Set<string>>(new Set());
  const windowingTransactionFrameRef = useRef<number | null>(null);
  const pendingWindowingTransactionsRef = useRef<Map<string, PendingWindowingTransaction>>(new Map());
  const [windowings, setWindowings] = useState<Record<string, WindowSetting>>({});

  const scheduleWindowingState = useCallback((transaction: PendingWindowingTransaction) => {
    const pending = pendingWindowingTransactionsRef.current;
    const current = pending.get(transaction.id);
    pending.set(transaction.id, {
      id: transaction.id,
      calMin: transaction.calMin ?? current?.calMin,
      calMax: transaction.calMax ?? current?.calMax,
      globalMin: transaction.globalMin ?? current?.globalMin,
      globalMax: transaction.globalMax ?? current?.globalMax,
    });
    if (windowingTransactionFrameRef.current !== null) return;

    windowingTransactionFrameRef.current = window.requestAnimationFrame(() => {
      windowingTransactionFrameRef.current = null;
      const transactions = [...pendingWindowingTransactionsRef.current.values()];
      pendingWindowingTransactionsRef.current.clear();

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
  }, []);

  const ensureWindowingForLayer = useCallback((id: string) => {
    setWindowings((prev) => {
      if (prev[id]) return prev;
      const nv = instanceRef.current;
      if (!nv) return prev;
      const loaded = asNiivueInterop(nv).volumes.find((volume) => volume.id === id);
      if (!loaded) return prev;
      return {
        ...prev,
        [id]: {
          calMin: loaded.calMin ?? loaded.globalMin ?? 0,
          calMax: loaded.calMax ?? loaded.globalMax ?? 1,
          globalMin: loaded.globalMin ?? 0,
          globalMax: loaded.globalMax ?? 1,
        },
      };
    });
  }, [instanceRef]);

  const updateWindowing = useCallback((id: string, field: 'calMin' | 'calMax', value: number) => {
    manualWindowingRef.current.add(id);
    let statePatch: PendingWindowingTransaction = { id, [field]: value };

    const nv = instanceRef.current;
    if (nv) {
      const loaded = asNiivueInterop(nv).volumes.find((volume) => volume.id === id);
      if (loaded) {
        statePatch = {
          ...statePatch,
          globalMin: loaded.globalMin ?? statePatch.globalMin,
          globalMax: loaded.globalMax ?? statePatch.globalMax,
          calMin: field === 'calMin' ? value : loaded.calMin ?? statePatch.calMin,
          calMax: field === 'calMax' ? value : loaded.calMax ?? statePatch.calMax,
        };
        const nextMin = field === 'calMin' ? value : loaded.calMin;
        const nextMax = field === 'calMax' ? value : loaded.calMax;
        if (
          nextMin !== undefined
          && nextMax !== undefined
          && Number.isFinite(nextMin)
          && Number.isFinite(nextMax)
          && nextMin !== nextMax
          && (loaded.calMin !== nextMin || loaded.calMax !== nextMax)
        ) {
          loaded.calMin = nextMin;
          loaded.calMax = nextMax;
          scheduleLayerRefresh(nv, loaded);
        }
      }
    }

    scheduleWindowingState(statePatch);
  }, [instanceRef, scheduleLayerRefresh, scheduleWindowingState]);

  const syncIntensityWindow = useCallback((sourceLoaded: NiivueVolumeInterop) => {
    const id = sourceLoaded.id;
    const calMin = sourceLoaded.calMin;
    const calMax = sourceLoaded.calMax;
    if (!id || calMin === undefined || calMax === undefined) return;
    if (!Number.isFinite(calMin) || !Number.isFinite(calMax) || calMin === calMax) return;
    const source = volumes.find((volume) => volume.id === id);
    if (!source || isSurfaceLayer(source) || source.type === 'segmentation') return;

    manualWindowingRef.current.add(id);
    scheduleWindowingState({
      id,
      calMin,
      calMax,
      globalMin: sourceLoaded.globalMin ?? calMin,
      globalMax: sourceLoaded.globalMax ?? calMax,
    });
  }, [scheduleWindowingState, volumes]);

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
