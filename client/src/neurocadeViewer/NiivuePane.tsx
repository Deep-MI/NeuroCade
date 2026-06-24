import React, { useCallback, useEffect, useRef } from 'react';
import { DRAG_MODE, Niivue } from '@niivue/niivue';

import type { LocationInfo, Volume } from '../types';
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
  installCoverRendering,
  installSafeLocationChange,
  locationFromNiivue,
  shouldUseVoxelExactLabelRendering,
  sourceKeyOf,
  syncNiivueSurfaceDisplay,
  volumesInRenderOrder,
} from './niivueLayers';
import { selectLoadedReferenceVolume } from './referenceVolume';
import { syncSurfaceReferenceTransforms } from './surfaceTransforms';
import { SurfaceContourOverlay } from './SurfaceContourOverlay';
import type { ViewerDragMode, ViewerPlaneSliceType, ViewerSliceType } from './viewerControls';

// The Matcap shader has no light/ambient term — mesh brightness is set entirely
// by this texture (final color = matcap × surface color). We keep Fuzzy's soft,
// highlight-free matte look but lift it brighter via MATCAP_BRIGHTNESS below;
// switching to glossy matcaps (Plastic/Shiny) would add unwanted specular spots.
const matcapUrl = new URL('../../node_modules/@niivue/niivue/src/matcaps/Fuzzy.jpg', import.meta.url).href;

// Multiplier applied to the matcap's RGB. 1 = original; higher = brighter while
// preserving the matte shading gradient. Clamped per-channel at white.
const MATCAP_BRIGHTNESS = 1.45;

// Brighten a matcap texture in-place on a canvas and return a data URL that
// loadMatCapTexture() can consume directly.
async function loadBrightenedMatcap(url: string, factor: number): Promise<string> {
  const img = new Image();
  img.src = url;
  await img.decode();
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) return url;
  ctx.drawImage(img, 0, 0);
  const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const px = image.data;
  for (let i = 0; i < px.length; i += 4) {
    px[i] = Math.min(255, px[i] * factor);
    px[i + 1] = Math.min(255, px[i + 1] * factor);
    px[i + 2] = Math.min(255, px[i + 2] * factor);
  }
  ctx.putImageData(image, 0, 0);
  return canvas.toDataURL('image/png');
}

export interface WindowSetting {
  calMin: number;
  calMax: number;
  globalMin: number;
  globalMax: number;
}

interface NiivuePaneProps {
  sliceType: ViewerSliceType;
  volumes: Volume[];
  windowings: Record<string, WindowSetting>;
  manualWindowingIds: React.MutableRefObject<Set<string>>;
  dragMode: ViewerDragMode;
  externalCoordinate?: [number, number, number] | null;
  reportLocation?: boolean;
  hidden?: boolean;
  className?: string;
  onReady: (nv: Niivue | null, sliceType: ViewerSliceType) => void;
  onLocationChange?: (location: LocationInfo, mm: number[]) => void;
  onLoadingChange?: (sliceType: ViewerSliceType, loading: boolean) => void;
  onError?: (message: string | null) => void;
  onColormaps?: (colormaps: string[]) => void;
}

function isPlane(sliceType: ViewerSliceType): sliceType is ViewerPlaneSliceType {
  return sliceType <= 2;
}

export function NiivuePane({
  sliceType,
  volumes,
  windowings,
  manualWindowingIds,
  dragMode,
  externalCoordinate,
  reportLocation,
  hidden,
  className,
  onReady,
  onLocationChange,
  onLoadingChange,
  onError,
  onColormaps,
}: NiivuePaneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nvRef = useRef<Niivue | null>(null);
  const latestVolumesRef = useRef<Volume[]>(volumes);
  const windowingsRef = useRef(windowings);
  const loadingLayerIdsRef = useRef<Set<string>>(new Set());
  const glFrameRef = useRef<number | null>(null);
  const currentMmRef = useRef<number[] | null>(null);
  const onLocationChangeRef = useRef(onLocationChange);
  const reportLocationRef = useRef(reportLocation);
  latestVolumesRef.current = volumes;
  windowingsRef.current = windowings;
  onLocationChangeRef.current = onLocationChange;
  reportLocationRef.current = reportLocation;

  const plane = isPlane(sliceType);
  const sourceKey = sourceKeyOf(volumes);

  const scheduleRefresh = useCallback(() => {
    if (glFrameRef.current !== null) return;
    glFrameRef.current = requestAnimationFrame(() => {
      glFrameRef.current = null;
      nvRef.current?.updateGLVolume();
    });
  }, []);

  // --- Instance lifecycle ---------------------------------------------------
  useEffect(() => {
    if (!canvasRef.current || nvRef.current) return;
    const nv = new Niivue({
      loadingText: '',
      dragAndDropEnabled: false,
      fontMinPx: 14,
      fontSizeScaling: 0.5,
      fontColor: [0.82, 0.86, 0.94, 0.55],
      showAllOrientationMarkers: true,
      // Dark grey so a volume's extent is visible against the empty canvas.
      backColor: [0.16, 0.16, 0.16, 1],
      crosshairColor: [0.47, 0.66, 1, 0.7],
      crosshairGap: 3,
      sliceType,
      // 3D pane only: let dark voxels become transparent so the mesh shows
      // through. 2D planes keep dark voxels opaque (the Niivue default).
      isAlphaClipDark: !plane,
      isColorbar: false,
      showLegend: false,
      isNearestInterpolation: true,
    });
    let disposed = false;
    void nv.attachToCanvas(canvasRef.current).then(() => {
      if (disposed) return;
      nv.setInterpolation(true);
      void loadBrightenedMatcap(matcapUrl, MATCAP_BRIGHTNESS).then((url) => {
        if (disposed) return;
        return asNiivueInterop(nv).loadMatCapTexture?.(url);
      });
      installCoverRendering(nv);
      nv.setSliceType(sliceType);
      onColormaps?.(nv.colormaps());
      nv.updateGLVolume();
    });
    nv.onLocationChange = (locationObject: unknown) => {
      const location = locationFromNiivue(locationObject, nv, latestVolumesRef.current);
      const mm = (locationObject as { mm?: number[] } | null)?.mm ?? null;
      if (location && mm) {
        currentMmRef.current = mm;
        if (reportLocationRef.current) {
          onLocationChangeRef.current?.(location, mm);
        }
      }
    };
    installSafeLocationChange(nv);
    nvRef.current = nv;
    onReady(nv, sliceType);

    return () => {
      disposed = true;
      if (glFrameRef.current !== null) {
        cancelAnimationFrame(glFrameRef.current);
        glFrameRef.current = null;
      }
      onReady(null, sliceType);
      onLoadingChange?.(sliceType, false);
      (nv as unknown as { cleanup?: () => void }).cleanup?.();
      nvRef.current = null;
    };
    // sliceType/role are fixed for a pane instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Drag mode ------------------------------------------------------------
  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    asNiivueInterop(nv).opts.dragMode = DRAG_MODE[dragMode];
    nv.updateGLVolume();
  }, [dragMode]);

  // --- External crosshair coordinate ---------------------------------------
  useEffect(() => {
    if (!externalCoordinate) return;
    const nv = nvRef.current;
    if (!nv) return;
    const nvInterop = asNiivueInterop(nv);
    if (typeof nvInterop.moveCrosshairInVox === 'function') {
      nvInterop.moveCrosshairInVox(externalCoordinate[0], externalCoordinate[1], externalCoordinate[2]);
    } else if (typeof nvInterop.setCrosshairPosition === 'function') {
      nvInterop.setCrosshairPosition(externalCoordinate);
    }
    nvInterop.updateGLVolume?.();
  }, [externalCoordinate]);

  // --- Incremental layer reconcile (load/preload), per instance ------------
  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    const controller = new AbortController();
    let cancelled = false;
    const claimedIds: string[] = [];
    const loadingLayerIds = loadingLayerIdsRef.current;

    const isLayerLoaded = (layer: Volume) => {
      const filename = layer.filename || layer.name;
      if (plane && isSurfaceLayer(layer)) return true;
      return isSurfaceLayer(layer)
        ? (nv.meshes ?? []).some((mesh) => mesh.id === layer.id || mesh.name === filename)
        : asNiivueInterop(nv).volumes.some((loaded) => loaded.id === layer.id || loaded.url === layer.url || loaded.name === filename);
    };
    const claim = (id: string) => { loadingLayerIds.add(id); claimedIds.push(id); };
    const release = (id: string) => loadingLayerIds.delete(id);

    const reconcile = async () => {
      onError?.(null);
      const sources = latestVolumesRef.current;
      const sourceIds = new Set(sources.map((volume) => volume.id));

      // Remove only layers whose source is gone (close / case switch).
      for (const loaded of [...asNiivueInterop(nv).volumes]) {
        if (!loaded.id || !sourceIds.has(loaded.id)) nv.removeVolume(loaded as never);
      }
      for (const mesh of [...(asNiivueInterop(nv).meshes ?? [])]) {
        if (!mesh.id || !sourceIds.has(mesh.id)) nv.removeMesh(mesh as never);
      }

      const pending = sources.filter((layer) => !isLayerLoaded(layer) && !loadingLayerIds.has(layer.id));
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
              syncSurfaceReferenceTransforms(
                interop.meshes ?? [],
                latestVolumesRef.current,
                referenceVolume,
                interop.gl,
              );
            }
          }
          nv.updateGLVolume();
        }

        if (plane) return;
        for (const surface of pending.filter(isSurfaceLayer).filter((layer) => layer.visible)) {
          if (cancelled || controller.signal.aborted) break;
          claim(surface.id);
          void addNiivueSurfaceLayer(nv, surface, controller.signal, 'Matcap')
            .then(() => {
              const interop = asNiivueInterop(nv);
              const referenceVolume = selectLoadedReferenceVolume(interop.volumes, latestVolumesRef.current);
              if (referenceVolume) {
                syncSurfaceReferenceTransforms(
                  interop.meshes ?? [],
                  latestVolumesRef.current,
                  referenceVolume,
                  interop.gl,
                );
              }
              nv.updateGLVolume();
            })
            .catch((error) => {
              if (!controller.signal.aborted) console.warn(`[NiivuePane] Could not load surface ${surface.name}:`, error);
            })
            .finally(() => { release(surface.id); });
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey]);

  // --- Per-volume display sync (opacity, colormap, windowing, order) --------
  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    let needsFreesurferLabelRefresh = false;
    for (const loaded of asNiivueInterop(nv).volumes) {
      const source = volumes.find((volume) => volume.id === loaded.id);
      if (!source || isSurfaceLayer(source)) continue;
      loaded.opacity = effectiveLayerOpacity(source);
      loaded.colorbarVisible = false;
      const colormap = resolveVolumeColormap(source);
      if (loaded.colormap !== colormap) loaded.colormap = colormap;
      const colormapLabel = resolveCachedVolumeLabelColormap(source, loaded);
      if (shouldUseVoxelExactLabelRendering(source)) {
        if (colormapLabel) {
          applyVoxelExactLabelRendering(loaded, source, colormapLabel);
        } else if (inferredSegmentationLut(source) === 'freesurfer') {
          needsFreesurferLabelRefresh = true;
        }
      } else if (colormapLabel && loaded.colormapLabel !== colormapLabel) {
        loaded.colormapLabel = colormapLabel;
      } else if (inferredSegmentationLut(source) === 'freesurfer' && !colormapLabel) {
        needsFreesurferLabelRefresh = true;
      }
      if (source.type === undefined || source.type === 'intensity') {
        const win = windowingsRef.current[source.id];
        if (manualWindowingIds.current.has(source.id) && win) {
          loaded.cal_min = win.calMin;
          loaded.cal_max = win.calMax;
        } else {
          applyBrightnessContrast(loaded, source);
        }
      }
    }
    if (!plane) {
      for (const mesh of (asNiivueInterop(nv).meshes ?? [])) {
        const source = volumes.filter(isSurfaceLayer).find((layer) => layer.id === mesh.id);
        if (!source) continue;
        mesh.visible = source.visible;
        mesh.opacity = effectiveLayerOpacity(source);
        const controller = new AbortController();
        syncNiivueSurfaceDisplay(nv, mesh, source, controller.signal);
      }
    }
    enforceVolumeRenderOrder(nv, volumes);
    if (!plane) {
      const interop = asNiivueInterop(nv);
      const referenceVolume = selectLoadedReferenceVolume(interop.volumes, volumes);
      if (referenceVolume) {
        syncSurfaceReferenceTransforms(
          interop.meshes ?? [],
          volumes,
          referenceVolume,
          interop.gl,
        );
      }
    }
    scheduleRefresh();
    if (needsFreesurferLabelRefresh) {
      void getFreesurferColorMap()
        .then(() => {
          const activeNv = nvRef.current;
          if (!activeNv) return;
          for (const loaded of asNiivueInterop(activeNv).volumes) {
            const source = volumes.find((volume) => volume.id === loaded.id);
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
  }, [volumes, windowings, manualWindowingIds, plane, scheduleRefresh]);

  // Re-emit the current location when layers change (e.g. a segmentation is
  // toggled) so the label readout refreshes without moving the crosshair. Only
  // the designated reporting pane emits to avoid duplicate updates.
  useEffect(() => {
    if (!reportLocation) return;
    const nv = nvRef.current;
    const mm = currentMmRef.current;
    if (!nv || !mm) return;
    const location = locationFromNiivue({ mm }, nv, volumes);
    if (location) onLocationChangeRef.current?.(location, mm);
  }, [volumes, reportLocation]);

  // Arrow keys move the crosshair through/in this pane's plane (sync broadcasts
  // the change to the other panes).
  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const { key } = event;
    if (key !== 'ArrowUp' && key !== 'ArrowDown' && key !== 'ArrowLeft' && key !== 'ArrowRight') return;
    const nv = nvRef.current;
    const nvInterop = nv ? asNiivueInterop(nv) : null;
    if (!nv || !nvInterop || typeof nvInterop.moveCrosshairInVox !== 'function' || nvInterop.volumes.length === 0) return;
    const axis: ViewerPlaneSliceType = plane ? sliceType : 0;
    let delta: [number, number, number];
    if (key === 'ArrowUp' || key === 'ArrowDown') {
      const into = key === 'ArrowUp' ? -1 : 1;
      delta = axis === 2 ? [into, 0, 0] : axis === 1 ? [0, into, 0] : [0, 0, into];
    } else {
      const right = key === 'ArrowRight' ? 1 : -1;
      delta = axis === 2 ? [0, right, 0] : [right, 0, 0];
    }
    nvInterop.moveCrosshairInVox(delta[0], delta[1], delta[2]);
    event.preventDefault();
    event.stopPropagation();
  }, [plane, sliceType]);

  return (
    <div
      className={className ?? 'nc-viewer-pane'}
      style={hidden ? { display: 'none' } : undefined}
      tabIndex={0}
      role="application"
      aria-label="MRI viewer — use arrow keys to navigate slices"
      onKeyDownCapture={handleKeyDown}
    >
      <canvas ref={canvasRef} className="nc-viewer-canvas" />
      {plane && (
        <SurfaceContourOverlay
          sliceType={sliceType}
          volumes={volumes}
          nvRef={nvRef}
        />
      )}
    </div>
  );
}
