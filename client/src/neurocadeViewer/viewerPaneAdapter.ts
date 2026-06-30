import { Niivue } from '@niivue/niivue';

import { type Volume } from '../types';
import { asNiivueInterop } from '../utils/niivueInterop';
import type { NiivueVolumeInterop } from '../utils/niivueInterop';
import { effectiveLayerOpacity, setNiivueVolumeOpacity } from './niivueLayers';
import type { ViewerSliceType } from './viewerControls';

export type PaneRenderAction =
  | { kind: 'draw' }
  | { kind: 'refresh' }
  | { kind: 'layer-refresh'; loaded: NiivueVolumeInterop }
  | null;

export function referenceVolumeId(nv: Niivue): string | null {
  return asNiivueInterop(nv).volumes[0]?.id ?? null;
}

export function applyLayerDisplay(nv: Niivue, id: string, next: Volume, updates: Partial<Volume>): PaneRenderAction {
  const interop = asNiivueInterop(nv);
  const loaded = interop.volumes.find((volume) => volume.id === id);
  let needsDraw = false;
  let refreshLoaded: NiivueVolumeInterop | null = null;
  let handledLoadedDisplay = false;
  if (loaded) {
    if (typeof updates.visible === 'boolean' || typeof updates.opacity === 'number') {
      handledLoadedDisplay = true;
      const previousOpacity = loaded.opacity ?? 1;
      const nextOpacity = effectiveLayerOpacity(next);
      const result = setNiivueVolumeOpacity(nv, loaded, effectiveLayerOpacity(next));
      if (result === 'mutated') {
        if (typeof updates.visible === 'boolean' || previousOpacity === 0 || nextOpacity === 0) {
          return { kind: 'refresh' };
        }
        refreshLoaded = loaded;
      }
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
  if (refreshLoaded) return { kind: 'layer-refresh', loaded: refreshLoaded };
  if (needsDraw) return { kind: 'draw' };
  if (handledLoadedDisplay) return null;
  return loaded || mesh ? { kind: 'refresh' } : null;
}

export function previewLayerOpacity(nv: Niivue, id: string, next: Volume): PaneRenderAction {
  const interop = asNiivueInterop(nv);
  const loaded = interop.volumes.find((volume) => volume.id === id);
  const mesh = (interop.meshes ?? []).find((item) => item.id === id);
  if (!loaded && !mesh) return null;
  let action: PaneRenderAction = null;
  if (loaded) {
    const previousOpacity = loaded.opacity ?? 1;
    const nextOpacity = effectiveLayerOpacity(next);
    const result = setNiivueVolumeOpacity(nv, loaded, nextOpacity);
    if (result === 'mutated') {
      action = previousOpacity === 0 || nextOpacity === 0
        ? { kind: 'refresh' }
        : { kind: 'layer-refresh', loaded };
    }
  }
  if (mesh) {
    mesh.visible = next.visible;
    mesh.opacity = effectiveLayerOpacity(next);
    action = action ?? { kind: 'draw' };
  }
  return action;
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
