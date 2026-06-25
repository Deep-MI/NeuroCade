import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { DRAG_MODE, Niivue } from '@niivue/niivue';

import type { LocationInfo, Volume } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import {
  installCoverRendering,
  installSafeLocationChange,
  locationFromNiivue,
  sourceKeyOf,
} from './niivueLayers';
import {
  sourceVisibilityKeyOf,
  surfaceAppearanceKeyOf,
  surfaceTransformKeyOf,
  volumeAppearanceKeyOf,
  volumeOrderKeyOf,
  volumeVisibilityKeyOf,
  windowingKeyOf,
  type WindowSetting,
} from './paneSyncKeys';
import { SurfaceContourOverlay } from './SurfaceContourOverlay';
import { useNiivuePaneLayers } from './useNiivuePaneLayers';
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

export type { WindowSetting } from './paneSyncKeys';

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
  onIntensityWindowChange?: (sliceType: ViewerSliceType, loaded: NiivueVolumeInterop) => void;
  onLoadingChange?: (sliceType: ViewerSliceType, loading: boolean) => void;
  onError?: (message: string | null) => void;
  onColormaps?: (colormaps: string[]) => void;
}

function isPlane(sliceType: ViewerSliceType): sliceType is ViewerPlaneSliceType {
  return sliceType <= 2;
}

function selectedDragMode(mode: ViewerDragMode): DRAG_MODE {
  return DRAG_MODE[mode];
}

function primaryDragMode(): DRAG_MODE {
  return DRAG_MODE.crosshair;
}

function applyMouseDragMode(nv: Niivue, mode: ViewerDragMode): void {
  const interop = asNiivueInterop(nv);
  interop.opts.dragMode = selectedDragMode(mode);
  interop.opts.dragModePrimary = primaryDragMode();
  interop.opts.mouseEventConfig = undefined;
  interop.clearActiveDragMode?.();
}

interface OrientationLabels {
  top: string;
  right: string;
  bottom: string;
  left: string;
}

const PLANE_ORIENTATION_LABELS: Record<ViewerPlaneSliceType, OrientationLabels> = {
  0: { top: 'A', right: 'R', bottom: 'P', left: 'L' },
  1: { top: 'S', right: 'R', bottom: 'I', left: 'L' },
  2: { top: 'S', right: 'A', bottom: 'I', left: 'P' },
};

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
  onIntensityWindowChange,
  onLoadingChange,
  onError,
  onColormaps,
}: NiivuePaneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nvRef = useRef<Niivue | null>(null);
  const latestVolumesRef = useRef<Volume[]>(volumes);
  const windowingsRef = useRef(windowings);
  const loadingLayerIdsRef = useRef<Set<string>>(new Set());
  const surfaceDisplayControllersRef = useRef<Map<string, AbortController>>(new Map());
  const glFrameRef = useRef<number | null>(null);
  const drawFrameRef = useRef<number | null>(null);
  const currentMmRef = useRef<number[] | null>(null);
  const windowingGestureStartRef = useRef<Map<string, { calMin: number; calMax: number }> | null>(null);
  const onLocationChangeRef = useRef(onLocationChange);
  const onIntensityWindowChangeRef = useRef(onIntensityWindowChange);
  const reportLocationRef = useRef(reportLocation);
  latestVolumesRef.current = volumes;
  windowingsRef.current = windowings;
  onLocationChangeRef.current = onLocationChange;
  onIntensityWindowChangeRef.current = onIntensityWindowChange;
  reportLocationRef.current = reportLocation;

  const plane = isPlane(sliceType);
  const sourceKey = sourceKeyOf(volumes);
  const visibleSourceKey = useMemo(() => sourceVisibilityKeyOf(volumes), [volumes]);
  const visibilityKey = useMemo(() => volumeVisibilityKeyOf(volumes), [volumes]);
  const volumeAppearanceKey = useMemo(() => volumeAppearanceKeyOf(volumes), [volumes]);
  const activeWindowingKey = useMemo(() => windowingKeyOf(windowings), [windowings]);
  const volumeOrderKey = useMemo(() => volumeOrderKeyOf(volumes), [volumes]);
  const surfaceAppearanceKey = useMemo(() => surfaceAppearanceKeyOf(volumes), [volumes]);
  const surfaceTransformKey = useMemo(() => surfaceTransformKeyOf(volumes), [volumes]);

  const scheduleRefresh = useCallback(() => {
    if (glFrameRef.current !== null) return;
    glFrameRef.current = requestAnimationFrame(() => {
      glFrameRef.current = null;
      nvRef.current?.updateGLVolume();
    });
  }, []);

  const scheduleDraw = useCallback(() => {
    if (drawFrameRef.current !== null) return;
    drawFrameRef.current = requestAnimationFrame(() => {
      drawFrameRef.current = null;
      const nv = nvRef.current;
      if (!nv) return;
      asNiivueInterop(nv).drawScene?.();
    });
  }, []);

  // --- Instance lifecycle ---------------------------------------------------
  useEffect(() => {
    if (!canvasRef.current || nvRef.current) return;
    const surfaceDisplayControllers = surfaceDisplayControllersRef.current;
    const nv = new Niivue({
      loadingText: '',
      dragAndDropEnabled: false,
      fontMinPx: 14,
      fontSizeScaling: 0.5,
      fontColor: [0.82, 0.86, 0.94, 0.55],
      isOrientationTextVisible: false,
      showAllOrientationMarkers: false,
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
      dragMode: selectedDragMode(dragMode),
      dragModePrimary: primaryDragMode(),
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
    const handleIntensityChange = (event: Event) => {
      const loaded = (event as CustomEvent<NiivueVolumeInterop>).detail;
      if (loaded?.id && loaded.cal_min !== undefined && loaded.cal_max !== undefined) {
        onIntensityWindowChangeRef.current?.(sliceType, loaded);
      }
    };
    nv.addEventListener('intensityChange', handleIntensityChange);
    installSafeLocationChange(nv);
    nvRef.current = nv;
    onReady(nv, sliceType);

    return () => {
      disposed = true;
      if (glFrameRef.current !== null) {
        cancelAnimationFrame(glFrameRef.current);
        glFrameRef.current = null;
      }
      if (drawFrameRef.current !== null) {
        cancelAnimationFrame(drawFrameRef.current);
        drawFrameRef.current = null;
      }
      nv.removeEventListener('intensityChange', handleIntensityChange);
      for (const controller of surfaceDisplayControllers.values()) {
        controller.abort();
      }
      surfaceDisplayControllers.clear();
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
    applyMouseDragMode(nv, dragMode);
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

  useNiivuePaneLayers({
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
  });

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

  const captureWindowingGestureStart = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    if (event.button !== 2) return;
    const nv = nvRef.current;
    if (!nv) return;
    const starts = new Map<string, { calMin: number; calMax: number }>();
    for (const loaded of asNiivueInterop(nv).volumes) {
      if (!loaded.id || loaded.cal_min === undefined || loaded.cal_max === undefined) continue;
      starts.set(loaded.id, { calMin: loaded.cal_min, calMax: loaded.cal_max });
    }
    windowingGestureStartRef.current = starts;
  }, []);

  const emitWindowingGestureChanges = useCallback(() => {
    const starts = windowingGestureStartRef.current;
    if (!starts) return;
    windowingGestureStartRef.current = null;

    const nv = nvRef.current;
    if (!nv) return;
    for (const loaded of asNiivueInterop(nv).volumes) {
      if (!loaded.id || loaded.cal_min === undefined || loaded.cal_max === undefined) continue;
      const start = starts.get(loaded.id);
      if (!start) continue;
      if (loaded.cal_min === start.calMin && loaded.cal_max === start.calMax) continue;
      onIntensityWindowChangeRef.current?.(sliceType, loaded);
    }
  }, [sliceType]);

  const orientationLabels = plane ? PLANE_ORIENTATION_LABELS[sliceType] : null;

  return (
    <div
      className={className ?? 'nc-viewer-pane'}
      style={hidden ? { display: 'none' } : undefined}
      tabIndex={0}
      role="application"
      aria-label="MRI viewer — use arrow keys to navigate slices"
      onKeyDownCapture={handleKeyDown}
      onMouseDownCapture={captureWindowingGestureStart}
      onMouseUpCapture={emitWindowingGestureChanges}
      onMouseLeave={emitWindowingGestureChanges}
    >
      <canvas ref={canvasRef} className="nc-viewer-canvas" />
      {orientationLabels && (
        <div className="nc-viewer-orientation-labels" aria-hidden="true">
          <span className="nc-viewer-orientation-label nc-viewer-orientation-label-top">{orientationLabels.top}</span>
          <span className="nc-viewer-orientation-label nc-viewer-orientation-label-right">{orientationLabels.right}</span>
          <span className="nc-viewer-orientation-label nc-viewer-orientation-label-bottom">{orientationLabels.bottom}</span>
          <span className="nc-viewer-orientation-label nc-viewer-orientation-label-left">{orientationLabels.left}</span>
        </div>
      )}
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
