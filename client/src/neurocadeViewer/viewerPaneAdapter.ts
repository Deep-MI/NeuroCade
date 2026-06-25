import { Niivue } from '@niivue/niivue';

import { type Volume } from '../types';
import { asNiivueInterop } from '../utils/niivueInterop';
import { effectiveLayerOpacity } from './niivueLayers';
import type { ViewerSliceType } from './viewerControls';

export type PaneRenderAction = 'draw' | 'refresh' | null;

export function referenceVolumeId(nv: Niivue): string | null {
  return asNiivueInterop(nv).volumes[0]?.id ?? null;
}

export function applyLayerDisplay(nv: Niivue, id: string, next: Volume, updates: Partial<Volume>): PaneRenderAction {
  const interop = asNiivueInterop(nv);
  const loaded = interop.volumes.find((volume) => volume.id === id);
  let needsDraw = false;
  if (loaded) {
    if (typeof updates.visible === 'boolean' || typeof updates.opacity === 'number') {
      loaded.opacity = effectiveLayerOpacity(next);
      needsDraw = true;
    }
  }
  const mesh = (interop.meshes ?? []).find((item) => item.id === id);
  if (mesh) {
    if (typeof updates.visible === 'boolean') {
      mesh.visible = updates.visible;
      needsDraw = true;
    }
    if (typeof updates.opacity === 'number' || typeof updates.visible === 'boolean') {
      mesh.opacity = effectiveLayerOpacity(next);
      needsDraw = true;
    }
  }
  if (needsDraw) return 'draw';
  return loaded || mesh ? 'refresh' : null;
}

export function previewLayerOpacity(nv: Niivue, id: string, next: Volume): boolean {
  const interop = asNiivueInterop(nv);
  const loaded = interop.volumes.find((volume) => volume.id === id);
  const mesh = (interop.meshes ?? []).find((item) => item.id === id);
  if (!loaded && !mesh) return false;
  if (loaded) loaded.opacity = effectiveLayerOpacity(next);
  if (mesh) {
    mesh.visible = next.visible;
    mesh.opacity = effectiveLayerOpacity(next);
  }
  return true;
}

export function capturePaneSnapshot(nv: Niivue | null | undefined): string | null {
  const canvas = nv ? (nv as unknown as { canvas?: HTMLCanvasElement }).canvas : undefined;
  return canvas ? canvas.toDataURL('image/jpeg', 0.8) : null;
}

export function drawingPanesFromInstances(instances: Map<ViewerSliceType, Niivue>): Niivue[] {
  return [...instances.entries()]
    .filter(([sliceType]) => sliceType <= 2)
    .map(([, nv]) => nv);
}
