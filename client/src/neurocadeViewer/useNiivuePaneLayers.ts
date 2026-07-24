import { useEffect, type MutableRefObject } from 'react';
import Niivue from '@niivue/niivue';

import type { Volume } from '../types';
import { isSurfaceLayer } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import {
  applyBrightnessContrast,
  resolveVolumeColormap,
  resolveVolumeLabelColorMap,
} from '../utils/volumeColormap';
import {
  addNiivueSurfaceLayer,
  addNiivueVolumeLayer,
  effectiveLayerOpacity,
  enforceVolumeRenderOrder,
  setNiivueVolumeOpacity,
  surfaceDisplayKey,
  syncNiivueSurfaceDisplay,
  volumesInRenderOrder,
} from './niivueLayers';
import type { WindowSetting } from './paneSyncKeys';

interface UseNiivuePaneLayersOptions {
  manualWindowingIds: MutableRefObject<Set<string>>;
  sourceKey: string;
  visibleSourceKey: string;
  visibilityKey: string;
  volumeAppearanceKey: string;
  activeWindowingKey: string;
  volumeOrderKey: string;
  surfaceAppearanceKey: string;
  nvRef: MutableRefObject<Niivue | null>;
  latestVolumesRef: MutableRefObject<Volume[]>;
  windowingsRef: MutableRefObject<Record<string, WindowSetting>>;
  loadingLayerIdsRef: MutableRefObject<Set<string>>;
  surfaceDisplayControllersRef: MutableRefObject<Map<string, AbortController>>;
  scheduleRefresh: () => void;
  onLoadingChange?: (loading: boolean) => void;
  onError?: (message: string | null) => void;
}

function isLayerLoaded(nv: Niivue, layer: Volume): boolean {
  const filename = layer.filename || layer.name;
  return isSurfaceLayer(layer)
    ? (asNiivueInterop(nv).meshes ?? []).some((mesh) => mesh.id === layer.id || mesh.name === filename)
    : asNiivueInterop(nv).volumes.some((loaded) => loaded.id === layer.id || loaded.url === layer.url || loaded.name === filename);
}

export function useNiivuePaneLayers({
  manualWindowingIds,
  sourceKey,
  visibleSourceKey,
  visibilityKey,
  volumeAppearanceKey,
  activeWindowingKey,
  volumeOrderKey,
  surfaceAppearanceKey,
  nvRef,
  latestVolumesRef,
  windowingsRef,
  loadingLayerIdsRef,
  surfaceDisplayControllersRef,
  scheduleRefresh,
  onLoadingChange,
  onError,
}: UseNiivuePaneLayersOptions): void {
  // Incremental layer reconcile. Hidden volume sources stay available in the
  // layer panel but are removed from NiiVue: its 3D renderer always draws the
  // base volume even at opacity zero. Fetched bytes remain cached, so showing a
  // layer again avoids another network transfer.
  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const controller = new AbortController();
    let cancelled = false;
    const claimedIds: string[] = [];
    const loadingLayerIds = loadingLayerIdsRef.current;

    const claim = (id: string) => { loadingLayerIds.add(id); claimedIds.push(id); };
    const release = (id: string) => loadingLayerIds.delete(id);

    const reconcile = async () => {
      onError?.(null);
      const sources = latestVolumesRef.current;
      const visibleVolumeIds = new Set(
        sources
          .filter((volume) => volume.visible && !isSurfaceLayer(volume))
          .map((volume) => volume.id),
      );

      let removedVolume = false;
      for (const loaded of [...asNiivueInterop(nv).volumes]) {
        if (!loaded.id || !visibleVolumeIds.has(loaded.id)) {
          const index = nv.volumes.indexOf(loaded);
          if (index >= 0) {
            nv.model.removeVolume(index);
            removedVolume = true;
          }
        }
      }
      if (removedVolume) await nv.updateGLVolume();
      const sourceIds = new Set(sources.map((volume) => volume.id));
      for (const mesh of [...(asNiivueInterop(nv).meshes ?? [])]) {
        if (!mesh.id || !sourceIds.has(mesh.id)) {
          const index = nv.meshes.indexOf(mesh);
          if (index >= 0) void nv.removeMesh(index);
        }
      }

      const pending = sources.filter((layer) => (
        layer.visible
        && !isLayerLoaded(nv, layer)
        && !loadingLayerIds.has(layer.id)
      ));
      if (pending.length === 0) {
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
        for (const volume of pendingVolumes) {
          if (cancelled || controller.signal.aborted) break;
          claim(volume.id);
          try {
            await addNiivueVolumeLayer(nv, volume, controller.signal);
          } catch (error) {
            if (!cancelled && !controller.signal.aborted) {
              onError?.(`Failed to load volume ${volume.filename || volume.name}: ${error instanceof Error ? error.message : String(error)}`);
            }
          } finally {
            release(volume.id);
          }
        }
        if (!cancelled && pendingVolumes.length > 0) {
          enforceVolumeRenderOrder(nv, latestVolumesRef.current);
          void nv.updateGLVolume();
        }

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
        if (!cancelled && visibleSurfaces.length > 0) {
          void nv.updateGLVolume();
        }
      } finally {
        if (!cancelled) onLoadingChange?.(false);
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
    sourceKey,
    visibleSourceKey,
  ]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;

    for (const loaded of asNiivueInterop(nv).volumes) {
      const source = sources.find((volume) => volume.id === loaded.id);
      if (!source || isSurfaceLayer(source)) continue;
      const nextOpacity = effectiveLayerOpacity(source);
      setNiivueVolumeOpacity(nv, loaded, nextOpacity);
    }

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
  }, [latestVolumesRef, nvRef, visibilityKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const syncAppearance = async () => {
      const sources = latestVolumesRef.current;
      for (const loaded of asNiivueInterop(nv).volumes) {
        const source = sources.find((volume) => volume.id === loaded.id);
        if (!source || isSurfaceLayer(source)) continue;
        const volumeIndex = nv.volumes.indexOf(loaded);
        if (volumeIndex < 0) continue;

        const colormap = resolveVolumeColormap(source);
        if ((source.type === undefined || source.type === 'intensity') && !manualWindowingIds.current.has(source.id)) {
          applyBrightnessContrast(loaded, source);
        }
        await nv.setVolume(volumeIndex, {
          colormap,
          isColorbarVisible: false,
          calMin: loaded.calMin,
          calMax: loaded.calMax,
        });
        if (source.type === 'segmentation') {
          const labelMap = await resolveVolumeLabelColorMap(source);
          if (labelMap) await nv.setColormapLabel(volumeIndex, labelMap);
        }
      }
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
      for (const loaded of changedVolumes) {
        const volumeIndex = nv.volumes.indexOf(loaded);
        if (volumeIndex >= 0) void nv.setVolume(volumeIndex, { calMin: loaded.calMin, calMax: loaded.calMax });
      }
    }
  }, [activeWindowingKey, latestVolumesRef, manualWindowingIds, nvRef, windowingsRef]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    if (enforceVolumeRenderOrder(nv, latestVolumesRef.current)) {
      scheduleRefresh();
    }
  }, [latestVolumesRef, nvRef, scheduleRefresh, volumeOrderKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;
    for (const mesh of (asNiivueInterop(nv).meshes ?? [])) {
      const source = sources.filter(isSurfaceLayer).find((layer) => layer.id === mesh.id);
      if (!source) continue;
      const existingController = surfaceDisplayControllersRef.current.get(source.id);
      if (existingController && (mesh as { __surfaceDisplayKey?: string }).__surfaceDisplayKey !== surfaceDisplayKey(source)) {
        existingController.abort();
        surfaceDisplayControllersRef.current.delete(source.id);
      }
      const controller = surfaceDisplayControllersRef.current.get(source.id) ?? new AbortController();
      surfaceDisplayControllersRef.current.set(source.id, controller);
      void syncNiivueSurfaceDisplay(nv, mesh, source, controller.signal);
    }
  }, [latestVolumesRef, nvRef, surfaceAppearanceKey, surfaceDisplayControllersRef]);

}
