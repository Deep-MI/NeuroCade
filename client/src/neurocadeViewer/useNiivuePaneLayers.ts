import { useEffect, type MutableRefObject } from 'react';
import { Niivue } from '@niivue/niivue';

import type { Volume } from '../types';
import { isSurfaceLayer } from '../types';
import { asNiivueInterop } from '../utils/niivueInterop';
import {
  applyBrightnessContrast,
  getCachedFreesurferLabelLut,
  getFreesurferColorMap,
  inferredSegmentationLut,
  maxLabelForVolume,
  resolveCachedVolumeLabelColormap,
  resolveVolumeColormap,
} from '../utils/volumeColormap';
import {
  addNiivueSurfaceLayer,
  addNiivueVolumeLayer,
  applyVoxelExactLabelRendering,
  effectiveLayerOpacity,
  enforceVolumeRenderOrder,
  shouldUseVoxelExactLabelRendering,
  surfaceDisplayKey,
  syncNiivueSurfaceDisplay,
  volumesInRenderOrder,
} from './niivueLayers';
import type { WindowSetting } from './paneSyncKeys';
import { selectLoadedReferenceVolume } from './referenceVolume';
import { syncSurfaceReferenceTransforms } from './surfaceTransforms';
import type { ViewerSliceType } from './viewerControls';

interface UseNiivuePaneLayersOptions {
  sliceType: ViewerSliceType;
  plane: boolean;
  manualWindowingIds: MutableRefObject<Set<string>>;
  sourceKey: string;
  visibleSourceKey: string;
  visibilityKey: string;
  volumeAppearanceKey: string;
  activeWindowingKey: string;
  volumeOrderKey: string;
  surfaceAppearanceKey: string;
  surfaceTransformKey: string;
  nvRef: MutableRefObject<Niivue | null>;
  latestVolumesRef: MutableRefObject<Volume[]>;
  windowingsRef: MutableRefObject<Record<string, WindowSetting>>;
  loadingLayerIdsRef: MutableRefObject<Set<string>>;
  surfaceDisplayControllersRef: MutableRefObject<Map<string, AbortController>>;
  scheduleRefresh: () => void;
  scheduleDraw: () => void;
  onLoadingChange?: (sliceType: ViewerSliceType, loading: boolean) => void;
  onError?: (message: string | null) => void;
}

function isLayerLoaded(nv: Niivue, plane: boolean, layer: Volume): boolean {
  const filename = layer.filename || layer.name;
  if (plane && isSurfaceLayer(layer)) return true;
  return isSurfaceLayer(layer)
    ? (asNiivueInterop(nv).meshes ?? []).some((mesh) => mesh.id === layer.id || mesh.name === filename)
    : asNiivueInterop(nv).volumes.some((loaded) => loaded.id === layer.id || loaded.url === layer.url || loaded.name === filename);
}

export function useNiivuePaneLayers({
  sliceType,
  plane,
  manualWindowingIds,
  sourceKey,
  visibleSourceKey,
  visibilityKey,
  volumeAppearanceKey,
  activeWindowingKey,
  volumeOrderKey,
  surfaceAppearanceKey,
  surfaceTransformKey,
  nvRef,
  latestVolumesRef,
  windowingsRef,
  loadingLayerIdsRef,
  surfaceDisplayControllersRef,
  scheduleRefresh,
  scheduleDraw,
  onLoadingChange,
  onError,
}: UseNiivuePaneLayersOptions): void {
  // Incremental layer reconcile (load/preload), per instance.
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
      const sourceIds = new Set(sources.map((volume) => volume.id));

      for (const loaded of [...asNiivueInterop(nv).volumes]) {
        if (!loaded.id || !sourceIds.has(loaded.id)) nv.removeVolume(loaded as never);
      }
      for (const mesh of [...(asNiivueInterop(nv).meshes ?? [])]) {
        if (!mesh.id || !sourceIds.has(mesh.id)) nv.removeMesh(mesh as never);
      }

      const pending = sources.filter((layer) => !isLayerLoaded(nv, plane, layer) && !loadingLayerIds.has(layer.id));
      if (pending.length === 0) {
        if (!cancelled) onLoadingChange?.(sliceType, false);
        return;
      }
      onLoadingChange?.(sliceType, true);
      try {
        const visibleVolumes = volumesInRenderOrder(pending.filter((layer) => layer.visible));
        for (const volume of visibleVolumes) {
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
        if (!cancelled && visibleVolumes.length > 0) {
          enforceVolumeRenderOrder(nv, latestVolumesRef.current);
          if (!plane) {
            const interop = asNiivueInterop(nv);
            const referenceVolume = selectLoadedReferenceVolume(interop.volumes, latestVolumesRef.current);
            if (referenceVolume) {
              syncSurfaceReferenceTransforms(interop.meshes ?? [], latestVolumesRef.current, referenceVolume, interop.gl);
            }
          }
          nv.updateGLVolume();
        }

        if (plane) return;
        const visibleSurfaces = pending.filter(isSurfaceLayer).filter((layer) => layer.visible);
        await Promise.all(visibleSurfaces.map(async (surface) => {
          if (cancelled || controller.signal.aborted) return;
          claim(surface.id);
          try {
            await addNiivueSurfaceLayer(nv, surface, controller.signal, 'Matcap');
          } catch (error) {
            if (!controller.signal.aborted) console.warn(`[NiivuePane] Could not load surface ${surface.name}:`, error);
          } finally {
            release(surface.id);
          }
        }));
        if (!cancelled && visibleSurfaces.length > 0) {
          const interop = asNiivueInterop(nv);
          const referenceVolume = selectLoadedReferenceVolume(interop.volumes, latestVolumesRef.current);
          if (referenceVolume) {
            syncSurfaceReferenceTransforms(interop.meshes ?? [], latestVolumesRef.current, referenceVolume, interop.gl);
          }
          nv.updateGLVolume();
        }
      } finally {
        if (!cancelled) onLoadingChange?.(sliceType, false);
      }
    };

    void reconcile();
    return () => {
      cancelled = true;
      controller.abort();
      for (const id of claimedIds) loadingLayerIds.delete(id);
    };
  }, [latestVolumesRef, loadingLayerIdsRef, nvRef, onError, onLoadingChange, plane, sliceType, sourceKey]);

  // A layer may start hidden and therefore skipped by the source reconciler.
  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const controller = new AbortController();
    let cancelled = false;
    const loadingLayerIds = loadingLayerIdsRef.current;

    const loadVisibleMissing = async () => {
      const pending = latestVolumesRef.current
        .filter((layer) => layer.visible && !isLayerLoaded(nv, plane, layer) && !loadingLayerIds.has(layer.id));
      if (pending.length === 0) return;
      onLoadingChange?.(sliceType, true);
      try {
        const pendingVolumes = volumesInRenderOrder(pending.filter((layer) => !isSurfaceLayer(layer)));
        for (const volume of pendingVolumes) {
          if (cancelled || controller.signal.aborted) break;
          loadingLayerIds.add(volume.id);
          try {
            await addNiivueVolumeLayer(nv, volume, controller.signal);
          } catch (error) {
            if (!cancelled && !controller.signal.aborted) {
              onError?.(`Failed to load volume ${volume.filename || volume.name}: ${error instanceof Error ? error.message : String(error)}`);
            }
          } finally {
            loadingLayerIds.delete(volume.id);
          }
        }
        if (!cancelled && pendingVolumes.length > 0) {
          enforceVolumeRenderOrder(nv, latestVolumesRef.current);
        }

        if (!plane) {
          for (const surface of pending.filter(isSurfaceLayer)) {
            if (cancelled || controller.signal.aborted) break;
            loadingLayerIds.add(surface.id);
            try {
              await addNiivueSurfaceLayer(nv, surface, controller.signal, 'Matcap');
            } catch (error) {
              if (!controller.signal.aborted) console.warn(`[NiivuePane] Could not load surface ${surface.name}:`, error);
            } finally {
              loadingLayerIds.delete(surface.id);
            }
          }
        }

        if (!cancelled) scheduleRefresh();
      } finally {
        if (!cancelled) onLoadingChange?.(sliceType, false);
      }
    };

    void loadVisibleMissing();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [latestVolumesRef, loadingLayerIdsRef, nvRef, onError, onLoadingChange, plane, scheduleRefresh, sliceType, visibleSourceKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;
    let changed = false;

    for (const loaded of asNiivueInterop(nv).volumes) {
      const source = sources.find((volume) => volume.id === loaded.id);
      if (!source || isSurfaceLayer(source)) continue;
      const nextOpacity = effectiveLayerOpacity(source);
      if (loaded.opacity !== nextOpacity) {
        loaded.opacity = nextOpacity;
        changed = true;
      }
    }

    if (!plane) {
      for (const mesh of (asNiivueInterop(nv).meshes ?? [])) {
        const source = sources.filter(isSurfaceLayer).find((layer) => layer.id === mesh.id);
        if (!source) continue;
        const nextOpacity = effectiveLayerOpacity(source);
        if (mesh.visible !== source.visible) {
          mesh.visible = source.visible;
          changed = true;
        }
        if (mesh.opacity !== nextOpacity) {
          mesh.opacity = nextOpacity;
          changed = true;
        }
      }
    }

    if (changed) scheduleDraw();
  }, [latestVolumesRef, nvRef, plane, scheduleDraw, visibilityKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;
    let needsRefresh = false;
    let needsDraw = false;
    let needsFreesurferLabelRefresh = false;

    for (const loaded of asNiivueInterop(nv).volumes) {
      const source = sources.find((volume) => volume.id === loaded.id);
      if (!source || isSurfaceLayer(source)) continue;
      loaded.colorbarVisible = false;
      const colormap = resolveVolumeColormap(source);
      if (loaded.colormap !== colormap) {
        loaded.colormap = colormap;
        needsRefresh = true;
      }
      const colormapLabel = resolveCachedVolumeLabelColormap(source, loaded);
      if (shouldUseVoxelExactLabelRendering(source)) {
        if (colormapLabel) {
          applyVoxelExactLabelRendering(loaded, source, colormapLabel);
          needsRefresh = true;
        } else if (inferredSegmentationLut(source) === 'freesurfer') {
          needsFreesurferLabelRefresh = true;
        }
      } else if (colormapLabel && loaded.colormapLabel !== colormapLabel) {
        loaded.colormapLabel = colormapLabel;
        needsRefresh = true;
      } else if (inferredSegmentationLut(source) === 'freesurfer' && !colormapLabel) {
        needsFreesurferLabelRefresh = true;
      }
      if (source.type === undefined || source.type === 'intensity') {
        const beforeMin = loaded.cal_min;
        const beforeMax = loaded.cal_max;
        if (!manualWindowingIds.current.has(source.id)) {
          applyBrightnessContrast(loaded, source);
        }
        if (loaded.cal_min !== beforeMin || loaded.cal_max !== beforeMax) needsDraw = true;
      }
    }

    if (needsRefresh) scheduleRefresh();
    else if (needsDraw) scheduleRefresh();
    if (needsFreesurferLabelRefresh) {
      void getFreesurferColorMap()
        .then(() => {
          const activeNv = nvRef.current;
          if (!activeNv) return;
          const activeSources = latestVolumesRef.current;
          for (const loaded of asNiivueInterop(activeNv).volumes) {
            const source = activeSources.find((volume) => volume.id === loaded.id);
            if (source && inferredSegmentationLut(source) === 'freesurfer') {
              loaded.colormap = resolveVolumeColormap(source);
              const labelLut = getCachedFreesurferLabelLut(maxLabelForVolume(loaded)) ?? null;
              if (shouldUseVoxelExactLabelRendering(source)) {
                applyVoxelExactLabelRendering(loaded, source, labelLut);
              } else {
                loaded.colormapLabel = labelLut;
              }
              loaded.colorbarVisible = false;
            }
          }
          activeNv.updateGLVolume();
        })
        .catch((error) => console.warn('[NiivuePane] Could not load FreeSurfer LUT:', error));
    }
  }, [latestVolumesRef, manualWindowingIds, nvRef, scheduleRefresh, volumeAppearanceKey]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const sources = latestVolumesRef.current;
    let changed = false;

    for (const loaded of asNiivueInterop(nv).volumes) {
      const source = sources.find((volume) => volume.id === loaded.id);
      if (!source || isSurfaceLayer(source) || (source.type !== undefined && source.type !== 'intensity')) continue;
      if (!manualWindowingIds.current.has(source.id)) continue;
      const win = windowingsRef.current[source.id];
      if (!win) continue;
      if (loaded.cal_min !== win.calMin) {
        loaded.cal_min = win.calMin;
        changed = true;
      }
      if (loaded.cal_max !== win.calMax) {
        loaded.cal_max = win.calMax;
        changed = true;
      }
    }

    if (changed) scheduleRefresh();
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
    if (!nv || plane) return;
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
  }, [latestVolumesRef, nvRef, plane, surfaceAppearanceKey, surfaceDisplayControllersRef]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv || plane) return;
    const interop = asNiivueInterop(nv);
    const sources = latestVolumesRef.current;
    const referenceVolume = selectLoadedReferenceVolume(interop.volumes, sources);
    if (referenceVolume && syncSurfaceReferenceTransforms(interop.meshes ?? [], sources, referenceVolume, interop.gl)) {
      scheduleRefresh();
    }
  }, [latestVolumesRef, nvRef, plane, scheduleRefresh, surfaceTransformKey]);
}
