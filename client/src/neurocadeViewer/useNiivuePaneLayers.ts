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
  effectiveLayerOpacity,
  enforceVolumeRenderOrder,
  prepareReferenceGeometry,
  setNiivueVolumeOpacity,
  syncNiivueSurfaceDisplay,
} from './niivueLayers';
import { surfaceDisplayKey, volumesInRenderOrder } from './layerDisplay';
import { getCrosshairWorld, restoreCrosshairWorld } from './loadedVolumeDisplay';
import type { WindowSetting } from './paneSyncKeys';
import {
  applyReferenceGeometry,
  captureReferenceGeometry,
  selectReferenceVolumeSource,
  type ReferenceGeometry,
} from './referenceGeometry';

interface UseNiivuePaneLayersOptions {
  manualWindowingIds: MutableRefObject<Set<string>>;
  layerReconcileKey: string;
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
  referenceGeometryRef: MutableRefObject<ReferenceGeometry | null>;
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
  layerReconcileKey,
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
  referenceGeometryRef,
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
      const crosshairWorld = getCrosshairWorld(nv);
      try {
        onError?.(null);
        const sources = latestVolumesRef.current;
        referenceGeometryRef.current = captureReferenceGeometry(
          nv,
          sources,
          referenceGeometryRef.current,
        );
        const preferredReference = selectReferenceVolumeSource(sources);
        if (
          preferredReference
          && !preferredReference.visible
          && referenceGeometryRef.current?.sourceId !== preferredReference.id
        ) {
          onLoadingChange?.(true);
          try {
            referenceGeometryRef.current = await prepareReferenceGeometry(
              nv,
              preferredReference,
              controller.signal,
            );
          } catch (error) {
            if (!cancelled && !controller.signal.aborted) {
              console.warn('[NiivuePane] Could not prepare reference geometry:', error);
            }
          }
        }
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
        if (removedVolume) {
          applyReferenceGeometry(nv, referenceGeometryRef.current);
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
          for (const volume of pendingVolumes) claim(volume.id);
          try {
            if (!cancelled && !controller.signal.aborted && pendingVolumes.length > 0) {
              await addNiivueVolumeLayers(
                nv,
                pendingVolumes,
                controller.signal,
                latestVolumesRef.current,
              );
              referenceGeometryRef.current = captureReferenceGeometry(
                nv,
                latestVolumesRef.current,
                referenceGeometryRef.current,
              );
            }
          } catch (error) {
            if (!cancelled && !controller.signal.aborted) {
              onError?.(`Failed to load volumes: ${error instanceof Error ? error.message : String(error)}`);
            }
          } finally {
            for (const volume of pendingVolumes) release(volume.id);
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
        } finally {
          if (!cancelled) onLoadingChange?.(false);
        }
      } finally {
        applyReferenceGeometry(nv, referenceGeometryRef.current);
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
    layerReconcileKey,
    referenceGeometryRef,
    surfaceDisplayControllersRef,
  ]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;
    let volumeDisplayChanged = false;

    for (const loaded of asNiivueInterop(nv).volumes) {
      const source = sources.find((volume) => volume.id === loaded.id);
      if (!source || isSurfaceLayer(source)) continue;
      const nextOpacity = effectiveLayerOpacity(source);
      if (setNiivueVolumeOpacity(nv, loaded, nextOpacity) === 'updated') {
        volumeDisplayChanged = true;
      }
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
    if (volumeDisplayChanged) scheduleRefresh();
  }, [latestVolumesRef, nvRef, scheduleRefresh, visibilityKey]);

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
