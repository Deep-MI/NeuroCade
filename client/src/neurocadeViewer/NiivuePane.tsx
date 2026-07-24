import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  DRAG_MODE,
  MULTIPLANAR_TYPE,
  SHOW_RENDER,
  type NiiVueLocation,
  type VolumeUpdatedDetail,
} from '@niivue/niivue';
import Niivue from '@niivue/niivue/webgl2';

import type { LocationInfo, Volume } from '../types';
import { asNiivueInterop, type NiivueVolumeInterop } from '../utils/niivueInterop';
import {
  locationFromNiivue,
  sourceKeyOf,
} from './niivueLayers';
import {
  sourceVisibilityKeyOf,
  surfaceAppearanceKeyOf,
  volumeAppearanceKeyOf,
  volumeOrderKeyOf,
  volumeVisibilityKeyOf,
  windowingKeyOf,
  type WindowSetting,
} from './paneSyncKeys';
import { useNiivuePaneLayers } from './useNiivuePaneLayers';
import {
  inPlaneCrosshairDelta,
  type ViewerDragMode,
  type ViewerPlaneSliceType,
  type ViewerSliceType,
} from './viewerControls';

export type { WindowSetting } from './paneSyncKeys';

interface NiivuePaneProps {
  sliceType: ViewerSliceType;
  showRender?: number;
  volumes: Volume[];
  windowings: Record<string, WindowSetting>;
  manualWindowingIds: React.MutableRefObject<Set<string>>;
  dragMode: ViewerDragMode;
  externalCoordinate?: [number, number, number] | null;
  reportLocation?: boolean;
  showOrientationLabels?: boolean;
  className?: string;
  onReady: (nv: Niivue | null) => void;
  onLocationChange?: (location: LocationInfo, mm: number[]) => void;
  onIntensityWindowChange?: (loaded: NiivueVolumeInterop) => void;
  onLoadingChange?: (loading: boolean) => void;
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
  nv.primaryDragMode = primaryDragMode();
  nv.secondaryDragMode = selectedDragMode(mode);
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
  showRender = SHOW_RENDER.AUTO,
  volumes,
  windowings,
  manualWindowingIds,
  dragMode,
  externalCoordinate,
  reportLocation,
  showOrientationLabels = true,
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

  const scheduleRefresh = useCallback(() => {
    if (glFrameRef.current !== null) return;
    glFrameRef.current = requestAnimationFrame(() => {
      glFrameRef.current = null;
      void nvRef.current?.updateGLVolume();
    });
  }, []);

  // --- Instance lifecycle ---------------------------------------------------
  useEffect(() => {
    if (!canvasRef.current || nvRef.current) return;
    const surfaceDisplayControllers = surfaceDisplayControllersRef.current;
    const nv = new Niivue({
      placeholderText: '',
      isDragDropEnabled: false,
      fontMinSize: 14,
      fontScale: 0.5,
      fontColor: [0.82, 0.86, 0.94, 0.55],
      isOrientationTextVisible: false,
      // Dark grey so a volume's extent is visible against the empty canvas.
      backgroundColor: [0.16, 0.16, 0.16, 1],
      // Keep the cursor opaque: translucent crosshairs visually merge with the
      // slice and look as if part of the line is behind bright anatomy.
      crosshairColor: [0.47, 0.66, 1, 1],
      crosshairGap: 3,
      sliceType,
      showRender,
      multiplanarType: MULTIPLANAR_TYPE.GRID,
      isEqualSize: true,
      // 3D pane only: let dark voxels become transparent so the mesh shows
      // through. 2D planes keep dark voxels opaque (the Niivue default).
      volumeIsAlphaClipDark: sliceType === 4,
      isColorbarVisible: false,
      isLegendVisible: false,
      volumeIsNearestInterpolation: true,
      primaryDragMode: primaryDragMode(),
      secondaryDragMode: selectedDragMode(dragMode),
    });
    let disposed = false;
    void nv.attachToCanvas(canvasRef.current).then(() => {
      if (disposed) return;
      nv.sliceType = sliceType;
      onColormaps?.(nv.colormaps);
      void nv.updateGLVolume();
    });
    const handleLocationChange = (event: Event) => {
      const locationObject = (event as CustomEvent<NiiVueLocation>).detail;
      const location = locationFromNiivue(locationObject, nv, latestVolumesRef.current);
      const mm = (locationObject as { mm?: number[] } | null)?.mm ?? null;
      if (location && mm) {
        currentMmRef.current = mm;
        if (reportLocationRef.current) {
          onLocationChangeRef.current?.(location, mm);
        }
      }
    };
    nv.addEventListener('locationChange', handleLocationChange);
    const handleIntensityChange = (event: Event) => {
      const loaded = (event as CustomEvent<VolumeUpdatedDetail>).detail.volume as NiivueVolumeInterop;
      if (loaded?.id && loaded.calMin !== undefined && loaded.calMax !== undefined) {
        onIntensityWindowChangeRef.current?.(loaded);
      }
    };
    nv.addEventListener('volumeUpdated', handleIntensityChange);
    nvRef.current = nv;
    onReady(nv);

    return () => {
      disposed = true;
      if (glFrameRef.current !== null) {
        cancelAnimationFrame(glFrameRef.current);
        glFrameRef.current = null;
      }
      nv.removeEventListener('locationChange', handleLocationChange);
      nv.removeEventListener('volumeUpdated', handleIntensityChange);
      for (const controller of surfaceDisplayControllers.values()) {
        controller.abort();
      }
      surfaceDisplayControllers.clear();
      onReady(null);
      onLoadingChange?.(false);
      nv.destroy();
      nvRef.current = null;
    };
    // The instance persists while its NiiVue 1.0 layout changes dynamically.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    nv.sliceType = sliceType;
    nv.showRender = showRender;
    nv.multiplanarType = MULTIPLANAR_TYPE.GRID;
    nv.isEqualSize = true;
    nv.isOrientationTextVisible = showOrientationLabels && sliceType === 3;
    nv.volumeIsAlphaClipDark = sliceType === 4;
    nv.drawScene();
  }, [showOrientationLabels, showRender, sliceType]);

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
    nv.setCrosshairPos(nv.vox2frac(externalCoordinate));
  }, [externalCoordinate]);

  useNiivuePaneLayers({
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
    const delta = inPlaneCrosshairDelta(axis, key);
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
      if (!loaded.id) continue;
      starts.set(loaded.id, { calMin: loaded.calMin, calMax: loaded.calMax });
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
      if (!loaded.id) continue;
      const start = starts.get(loaded.id);
      if (!start) continue;
      if (loaded.calMin === start.calMin && loaded.calMax === start.calMax) continue;
      onIntensityWindowChangeRef.current?.(loaded);
    }
  }, []);

  const orientationLabels = plane ? PLANE_ORIENTATION_LABELS[sliceType] : null;

  return (
    <div
      className={className ?? 'nc-viewer-pane'}
      tabIndex={0}
      role="application"
      aria-label="MRI viewer — use arrow keys to navigate slices"
      onKeyDownCapture={handleKeyDown}
      onMouseDownCapture={captureWindowingGestureStart}
      onMouseUpCapture={emitWindowingGestureChanges}
      onMouseLeave={emitWindowingGestureChanges}
    >
      <canvas ref={canvasRef} className="nc-viewer-canvas" />
      {showOrientationLabels && orientationLabels && (
        <div className="nc-viewer-orientation-labels" aria-hidden="true">
          <span className="nc-viewer-orientation-label nc-viewer-orientation-label-top">{orientationLabels.top}</span>
          <span className="nc-viewer-orientation-label nc-viewer-orientation-label-right">{orientationLabels.right}</span>
          <span className="nc-viewer-orientation-label nc-viewer-orientation-label-bottom">{orientationLabels.bottom}</span>
          <span className="nc-viewer-orientation-label nc-viewer-orientation-label-left">{orientationLabels.left}</span>
        </div>
      )}
    </div>
  );
}
