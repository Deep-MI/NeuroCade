import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { Niivue } from '@niivue/niivue';

import { isSurfaceLayer, type SurfaceLayer, type Volume } from '../types';
import { asNiivueInterop } from '../utils/niivueInterop';
import { surfaceColor } from '../utils/surfaceColors';
import {
  contourAxisForSliceType,
  nearestSliceIndex,
  planeCoordinatePair,
  volumeContourGeometry,
  type SurfaceContourSet,
  type VolumeContourGeometry,
} from './surfaceContours';
import { contoursForSurface } from './surfaceContourLoading';
import { effectiveLayerOpacity } from './niivueLayers';
import type { ViewerPlaneSliceType } from './viewerControls';

interface SurfaceContourEntry {
  surfaceId: string;
  contours: SurfaceContourSet;
}

interface NiivueScreenSlice {
  leftTopWidthHeight: number[];
  axCorSag: number;
  AxyzMxy?: number[];
  leftTopMM: number[];
  fovMM: number[];
}

interface ContourNiivueInterop {
  screenSlices?: NiivueScreenSlice[];
}

interface SurfaceContourOverlayProps {
  sliceType: ViewerPlaneSliceType;
  volumes: Volume[];
  nvRef: React.MutableRefObject<Niivue | null>;
}

const CONTOUR_LINE_WIDTH_CSS_PX = 2.25;

function surfaceKey(surface: SurfaceLayer): string {
  return [
    surface.id,
    surface.url,
    surface.filename,
    JSON.stringify(surface.surfaceReferenceAffine ?? null),
  ].join('|');
}

function isSliceRenderedSurface(volume: Volume): volume is SurfaceLayer {
  return isSurfaceLayer(volume) && (volume.renderInSlices ?? true);
}

function surfacesKey(volumes: Volume[]): string {
  return volumes.filter(isSliceRenderedSurface).map(surfaceKey).join('\n');
}

function currentPaneGeometry(nvRef: React.MutableRefObject<Niivue | null>): { gl: WebGL2RenderingContext; geometry: VolumeContourGeometry } | null {
  const nv = nvRef.current;
  const interop = nv ? asNiivueInterop(nv) : null;
  const gl = interop?.gl;
  const referenceVolume = interop?.back ?? interop?.volumes[0];
  if (referenceVolume && interop?.volumes[0] && referenceVolume !== interop.volumes[0]) return null;
  if (referenceVolume && !referenceVolume.dims && !referenceVolume.dimsRAS) return null;
  const geometry = volumeContourGeometry(referenceVolume, Boolean(interop?.opts.isSliceMM));
  return gl && geometry ? { gl, geometry } : null;
}

function waitForPaneGeometry(
  nvRef: React.MutableRefObject<Niivue | null>,
  expectedGeometryKey: string,
  signal: AbortSignal,
): Promise<{ gl: WebGL2RenderingContext; geometry: VolumeContourGeometry }> {
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (signal.aborted) {
        reject(new DOMException('Surface contour geometry wait aborted', 'AbortError'));
        return;
      }
      const paneGeometry = currentPaneGeometry(nvRef);
      if (paneGeometry?.geometry.key === expectedGeometryKey) {
        resolve(paneGeometry);
        return;
      }
      window.setTimeout(poll, 100);
    };
    poll();
  });
}

function rgbaCss(surface: SurfaceLayer): string {
  const [red, green, blue] = surfaceColor(surface).map((channel) => Math.round(channel * 255));
  return `rgb(${red} ${green} ${blue})`;
}

function currentScreenSlice(nv: Niivue | null, sliceType: ViewerPlaneSliceType): NiivueScreenSlice | null {
  const slices = (nv as unknown as ContourNiivueInterop | null)?.screenSlices;
  return slices?.find((slice) => slice.axCorSag === sliceType && slice.leftTopMM?.length >= 2 && slice.fovMM?.length >= 2) ?? null;
}

function projectPointWithTile(
  pointA: number,
  pointB: number,
  tile: NiivueScreenSlice,
  dpr: number,
): [number, number] {
  const [left, top, width, height] = tile.leftTopWidthHeight.map((value) => value / dpr);
  return [
    left + ((pointA - tile.leftTopMM[0]) / tile.fovMM[0]) * width,
    top + height - ((pointB - tile.leftTopMM[1]) / tile.fovMM[1]) * height,
  ];
}

function drawSurfaceContours(
  ctx: CanvasRenderingContext2D,
  canvas: HTMLCanvasElement,
  sliceType: ViewerPlaneSliceType,
  nv: Niivue | null,
  surfaces: SurfaceLayer[],
  entries: SurfaceContourEntry[],
): void {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  ctx.clearRect(0, 0, width, height);
  if (width <= 0 || height <= 0) return;

  const dpr = window.devicePixelRatio || 1;
  const tile = currentScreenSlice(nv, sliceType);
  if (!tile) return;

  const contourAxis = contourAxisForSliceType(sliceType);
  const [axisA, axisB] = planeCoordinatePair(sliceType);
  const surfacesById = new Map(surfaces.map((surface) => [surface.id, surface]));

  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.lineWidth = CONTOUR_LINE_WIDTH_CSS_PX;

  for (const entry of entries) {
    const surface = surfacesById.get(entry.surfaceId);
    if (!surface?.visible || !(surface.renderInSlices ?? true)) continue;
    const axisContours = entry.contours.axes[contourAxis];
    const tileSliceCoordinate = tile.AxyzMxy?.[2];
    const fallbackCoordinate = (entry.contours.bounds[0][contourAxis] + entry.contours.bounds[1][contourAxis]) / 2;
    const currentSliceCoordinate = typeof tileSliceCoordinate === 'number' && Number.isFinite(tileSliceCoordinate)
      ? tileSliceCoordinate
      : fallbackCoordinate;
    const sliceIndex = nearestSliceIndex(axisContours, currentSliceCoordinate);
    if (sliceIndex < 0) continue;
    const segments = axisContours.segmentsBySlice[sliceIndex];
    if (!segments || segments.length < 6) continue;

    ctx.globalAlpha = effectiveLayerOpacity(surface);
    ctx.strokeStyle = rgbaCss(surface);
    ctx.beginPath();
    for (let offset = 0; offset + 5 < segments.length; offset += 6) {
      const start = projectPointWithTile(segments[offset + axisA], segments[offset + axisB], tile, dpr);
      const end = projectPointWithTile(segments[offset + 3 + axisA], segments[offset + 3 + axisB], tile, dpr);
      ctx.moveTo(start[0], start[1]);
      ctx.lineTo(end[0], end[1]);
    }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

export function SurfaceContourOverlay({ sliceType, volumes, nvRef }: SurfaceContourOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const entriesRef = useRef<SurfaceContourEntry[]>([]);
  const volumesRef = useRef(volumes);
  const [entries, setEntries] = useState<SurfaceContourEntry[]>([]);
  const [paneGeometryKey, setPaneGeometryKey] = useState('');
  const surfaceSourceKey = useMemo(() => surfacesKey(volumes), [volumes]);
  volumesRef.current = volumes;
  entriesRef.current = entries;

  useEffect(() => {
    const pollGeometryKey = () => {
      const key = currentPaneGeometry(nvRef)?.geometry.key ?? '';
      setPaneGeometryKey((current) => (current === key ? current : key));
    };

    pollGeometryKey();
    const timer = window.setInterval(pollGeometryKey, 100);
    return () => window.clearInterval(timer);
  }, [nvRef]);

  useEffect(() => {
    if (!paneGeometryKey) {
      setEntries([]);
      return undefined;
    }

    const controller = new AbortController();
    let cancelled = false;

    const load = async () => {
      try {
        setEntries([]);
        const { gl, geometry } = await waitForPaneGeometry(nvRef, paneGeometryKey, controller.signal);
        const surfaces = volumesRef.current.filter(isSliceRenderedSurface);
        const nextEntries = await Promise.all(surfaces.map(async (surface) => ({
          surfaceId: surface.id,
          contours: await contoursForSurface(surface, gl, geometry, controller.signal),
        })));
        if (!cancelled) setEntries(nextEntries);
      } catch (error) {
        if (!controller.signal.aborted) {
          console.warn('[SurfaceContourOverlay] Could not build surface contours:', error);
        }
      }
    };

    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [nvRef, paneGeometryKey, surfaceSourceKey]);

  useEffect(() => {
    let frame = 0;
    const draw = () => {
      const canvas = canvasRef.current;
      const context = canvas?.getContext('2d');
      if (canvas && context) {
        const dpr = window.devicePixelRatio || 1;
        const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
        const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
        if (canvas.width !== width || canvas.height !== height) {
          canvas.width = width;
          canvas.height = height;
        }
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
        drawSurfaceContours(
          context,
          canvas,
          sliceType,
          nvRef.current,
          volumesRef.current.filter(isSliceRenderedSurface),
          entriesRef.current,
        );
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [nvRef, sliceType]);

  return <canvas ref={canvasRef} className="nc-viewer-surface-contours" aria-hidden="true" />;
}
