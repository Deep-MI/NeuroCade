import { useEffect, type MutableRefObject } from 'react';
import type Niivue from '@niivue/niivue';

import type { Volume } from '../types';
import { isSurfaceLayer } from '../types';
import { compileNiivueLabelColorMap } from '../utils/niivueColorMap';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import {
  applyBrightnessContrast,
  resolveVolumeColormap,
  resolveVolumeLabelColorMap,
} from '../utils/volumeColormap';
import {
  addNiivueSurfaceLayer,
  addNiivueVolumeLayers,
  syncNiivueSurfaceDisplay,
} from './niivueLayers';
import {
  effectiveLayerOpacity,
  orderedReferenceCandidate,
  surfaceDisplayKey,
  volumesInRenderOrder,
} from './layerDisplay';
import {
  getCrosshairWorld,
  restoreCrosshairWorld,
  syncLoadedVolumeOpacities,
} from './loadedVolumeDisplay';
import type { WindowSetting } from './paneSyncKeys';
import {
  enforceVolumeRenderOrder,
  ensureFixedNiivueReference,
} from './fixedReferenceRuntime.js';

interface UseNiivuePaneLayersOptions {
  manualWindowingIds: MutableRefObject<Set<string>>;
  layerReconcileKey: string;
  surfaceVisibilityKey: string;
  volumeAppearanceKey: string;
  volumeDisplayKey: string;
  activeWindowingKey: string;
  volumeStackKey: string;
  surfaceAppearanceKey: string;
  nvRef: MutableRefObject<Niivue | null>;
  latestVolumesRef: MutableRefObject<Volume[]>;
  windowingsRef: MutableRefObject<Record<string, WindowSetting>>;
  loadingLayerIdsRef: MutableRefObject<Set<string>>;
  surfaceDisplayControllersRef: MutableRefObject<Map<string, AbortController>>;
  scheduleRefresh: () => void;
  onLoadingChange?: (loading: boolean) => void;
  onError?: (message: string | null) => void;
  onCoordinateSourceChange?: (id: string | null) => void;
}

function isLayerLoaded(nv: Niivue, layer: Volume): boolean {
  const filename = layer.filename || layer.name;
  return isSurfaceLayer(layer)
    ? (asNiivueInterop(nv).meshes ?? []).some((mesh) => mesh.id === layer.id || mesh.name === filename)
    : asNiivueInterop(nv).volumes.some((loaded) => loaded.id === layer.id || loaded.url === layer.url || loaded.name === filename);
}

function syncNiivueVolumeOpacities(nv: Niivue, sources: Volume[]): boolean {
  const opacityById = new Map(
    sources
      .filter((source) => !isSurfaceLayer(source))
      .map((source) => [source.id, effectiveLayerOpacity(source)]),
  );
  return syncLoadedVolumeOpacities(nv, opacityById);
}

export function useNiivuePaneLayers({
  manualWindowingIds,
  layerReconcileKey,
  surfaceVisibilityKey,
  volumeAppearanceKey,
  volumeDisplayKey,
  activeWindowingKey,
  volumeStackKey,
  surfaceAppearanceKey,
  nvRef,
  latestVolumesRef,
  windowingsRef,
  loadingLayerIdsRef,
  surfaceDisplayControllersRef,
  scheduleRefresh,
  onLoadingChange,
  onError,
  onCoordinateSourceChange,
}: UseNiivuePaneLayersOptions): void {
  // Incremental layer reconcile. Every volume remains loaded regardless of
  // visibility so hide/show cannot replace NiiVue's background/reference grid.
  // Surfaces remain lazy because they do not participate in volume geometry.
  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const controller = new AbortController();
    let cancelled = false;
    const claimedIds: string[] = [];
    const loadingLayerIds = loadingLayerIdsRef.current;

    const claim = (id: string) => { loadingLayerIds.add(id); claimedIds.push(id); };
    const release = (id: string) => loadingLayerIds.delete(id);
    const syncFixedReference = async () => {
      try {
        const coordinateSourceId = await ensureFixedNiivueReference(nv, latestVolumesRef.current);
        if (!cancelled) onCoordinateSourceChange?.(coordinateSourceId);
      } catch (error) {
        if (!cancelled) {
          onError?.(`Could not establish the viewer reference grid: ${error instanceof Error ? error.message : String(error)}`);
        }
      }
    };

    const reconcile = async () => {
      const crosshairWorld = getCrosshairWorld(nv);
      try {
        onError?.(null);
        const sources = latestVolumesRef.current;
        const sourceVolumeIds = new Set(
          sources
            .filter((volume) => !isSurfaceLayer(volume))
            .map((volume) => volume.id),
        );

        let removedVolume = false;
        for (const loaded of [...asNiivueInterop(nv).volumes]) {
          if (loaded.__neurocadeFixedReference) continue;
          if (!loaded.id || !sourceVolumeIds.has(loaded.id)) {
            const index = nv.volumes.indexOf(loaded);
            if (index >= 0) {
              nv.model.removeVolume(index);
              removedVolume = true;
            }
          }
        }
        if (removedVolume) {
          await nv.updateGLVolume();
        }
        const sourceIds = new Set(sources.map((volume) => volume.id));
        for (const mesh of [...(asNiivueInterop(nv).meshes ?? [])]) {
          if (!mesh.id || !sourceIds.has(mesh.id)) {
            if (mesh.id) {
              surfaceDisplayControllersRef.current.get(mesh.id)?.abort();
              surfaceDisplayControllersRef.current.delete(mesh.id);
            }
            const index = nv.meshes.indexOf(mesh);
            if (index >= 0) void nv.removeMesh(index);
          }
        }

        const pending = sources.filter((layer) => (
          (!isSurfaceLayer(layer) || layer.visible)
          && !isLayerLoaded(nv, layer)
          && !loadingLayerIds.has(layer.id)
        ));
        if (pending.length === 0) {
          await syncFixedReference();
          if (!cancelled) onLoadingChange?.(false);
          return;
        }
        const pendingVolumes = volumesInRenderOrder(pending.filter((layer) => !isSurfaceLayer(layer)));
        const visibleSurfaces = pending.filter(isSurfaceLayer).filter((layer) => layer.visible);
        if (pendingVolumes.length === 0 && visibleSurfaces.length === 0) {
          if (!cancelled) onLoadingChange?.(false);
          return;
        }
        onLoadingChange?.(true);
        try {
          for (const volume of pendingVolumes) claim(volume.id);
          try {
            if (!cancelled && !controller.signal.aborted && pendingVolumes.length > 0) {
              await addNiivueVolumeLayers(
                nv,
                pendingVolumes,
                controller.signal,
              );
            }
          } catch (error) {
            if (!cancelled && !controller.signal.aborted) {
              onError?.(`Failed to load volumes: ${error instanceof Error ? error.message : String(error)}`);
            }
          } finally {
            for (const volume of pendingVolumes) release(volume.id);
          }

          if (syncNiivueVolumeOpacities(nv, latestVolumesRef.current)) {
            scheduleRefresh();
          }
          await syncFixedReference();

          await Promise.all(visibleSurfaces.map(async (surface) => {
            if (cancelled || controller.signal.aborted) return;
            claim(surface.id);
            try {
              await addNiivueSurfaceLayer(nv, surface, controller.signal);
            } catch (error) {
              if (!controller.signal.aborted) console.warn(`[NiivuePane] Could not load surface ${surface.name}:`, error);
            } finally {
              release(surface.id);
            }
          }));
        } finally {
          if (!cancelled) onLoadingChange?.(false);
        }
      } finally {
        if (!cancelled) restoreCrosshairWorld(nv, crosshairWorld);
      }
    };

    void reconcile();
    return () => {
      cancelled = true;
      controller.abort();
      for (const id of claimedIds) loadingLayerIds.delete(id);
    };
  }, [
    latestVolumesRef,
    loadingLayerIdsRef,
    nvRef,
    onError,
    onLoadingChange,
    onCoordinateSourceChange,
    layerReconcileKey,
    scheduleRefresh,
    surfaceDisplayControllersRef,
  ]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;

    for (const mesh of (asNiivueInterop(nv).meshes ?? [])) {
      const source = sources.filter(isSurfaceLayer).find((layer) => layer.id === mesh.id);
      if (!source) continue;
      const nextOpacity = effectiveLayerOpacity(source);
      if (mesh.visible !== source.visible) {
        mesh.visible = source.visible;
      }
      if (mesh.opacity !== nextOpacity) {
        mesh.opacity = nextOpacity;
      }
      const meshIndex = nv.meshes.indexOf(mesh);
      if (meshIndex >= 0) {
        void nv.setMesh(meshIndex, { visible: mesh.visible, opacity: mesh.opacity });
      }
    }
  }, [latestVolumesRef, nvRef, surfaceVisibilityKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    if (syncNiivueVolumeOpacities(nv, latestVolumesRef.current)) scheduleRefresh();
  }, [latestVolumesRef, nvRef, scheduleRefresh, volumeDisplayKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const syncAppearance = async () => {
      const sources = latestVolumesRef.current;
      let changed = false;
      for (const loaded of asNiivueInterop(nv).volumes) {
        const source = sources.find((volume) => volume.id === loaded.id);
        if (!source || isSurfaceLayer(source)) continue;

        const colormap = resolveVolumeColormap(source);
        if ((source.type === undefined || source.type === 'intensity') && !manualWindowingIds.current.has(source.id)) {
          applyBrightnessContrast(loaded, source);
        }
        loaded.colormap = colormap;
        loaded.isColorbarVisible = false;
        if (source.type === 'segmentation') {
          const labelMap = await resolveVolumeLabelColorMap(source);
          if (labelMap) loaded.colormapLabel = compileNiivueLabelColorMap(labelMap);
        }
        loaded.isDirty = true;
        changed = true;
      }
      if (changed) await nv.updateGLVolume();
    };
    void syncAppearance().catch((error) => {
      console.warn('[NiivuePane] Could not update volume appearance:', error);
    });
  }, [latestVolumesRef, manualWindowingIds, nvRef, volumeAppearanceKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;
    const changedVolumes: NiivueVolumeInterop[] = [];

    for (const loaded of asNiivueInterop(nv).volumes) {
      const source = sources.find((volume) => volume.id === loaded.id);
      if (!source || isSurfaceLayer(source) || (source.type !== undefined && source.type !== 'intensity')) continue;
      if (!manualWindowingIds.current.has(source.id)) continue;
      const win = windowingsRef.current[source.id];
      if (!win) continue;
      if (!Number.isFinite(win.calMin) || !Number.isFinite(win.calMax) || win.calMin === win.calMax) continue;
      if (loaded.calMin !== win.calMin) {
        loaded.calMin = win.calMin;
        changedVolumes.push(loaded);
      }
      if (loaded.calMax !== win.calMax) {
        loaded.calMax = win.calMax;
        if (!changedVolumes.includes(loaded)) changedVolumes.push(loaded);
      }
    }

    if (changedVolumes.length > 0) {
      // The fields are already current. Avoid setVolume here because it emits
      // volumeUpdated and can create a React -> NiiVue -> React feedback loop.
      scheduleRefresh();
    }
  }, [activeWindowingKey, latestVolumesRef, manualWindowingIds, nvRef, scheduleRefresh, windowingsRef]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;
    const coordinateSourceId = orderedReferenceCandidate(sources)?.id ?? null;
    const orderChanged = enforceVolumeRenderOrder(nv, sources);
    const opacityChanged = syncNiivueVolumeOpacities(nv, sources);
    onCoordinateSourceChange?.(coordinateSourceId);
    if (orderChanged || opacityChanged) scheduleRefresh();
  }, [latestVolumesRef, nvRef, onCoordinateSourceChange, scheduleRefresh, volumeStackKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;
    for (const mesh of (asNiivueInterop(nv).meshes ?? [])) {
      const source = sources.filter(isSurfaceLayer).find((layer) => layer.id === mesh.id);
      if (!source) continue;
      const existingController = surfaceDisplayControllersRef.current.get(source.id);
      if (existingController && mesh.__surfaceDisplayKey !== surfaceDisplayKey(source)) {
        existingController.abort();
        surfaceDisplayControllersRef.current.delete(source.id);
      }
      const controller = surfaceDisplayControllersRef.current.get(source.id) ?? new AbortController();
      surfaceDisplayControllersRef.current.set(source.id, controller);
      void syncNiivueSurfaceDisplay(nv, mesh, source, controller.signal);
    }
  }, [latestVolumesRef, nvRef, surfaceAppearanceKey, surfaceDisplayControllersRef]);

}
