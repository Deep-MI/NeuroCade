import Niivue from '@niivue/niivue';

import { type Volume } from '../types';
import { asNiivueInterop } from '../utils/niivueInterop';
import { effectiveLayerOpacity, setNiivueVolumeOpacity } from './niivueLayers';

export type PaneRenderAction =
  | { kind: 'draw' }
  | { kind: 'refresh' }
  | null;

export function referenceVolumeId(nv: Niivue): string | null {
  return asNiivueInterop(nv).volumes[0]?.id ?? null;
}

export function applyLayerDisplay(nv: Niivue, id: string, next: Volume, updates: Partial<Volume>): PaneRenderAction {
  const interop = asNiivueInterop(nv);
  const loaded = interop.volumes.find((volume) => volume.id === id);
  let needsDraw = false;
  let handledLoadedDisplay = false;
  if (loaded) {
    if (typeof updates.visible === 'boolean' || typeof updates.opacity === 'number') {
      handledLoadedDisplay = true;
      const previousOpacity = loaded.opacity ?? 1;
      const nextOpacity = effectiveLayerOpacity(next);
      const result = setNiivueVolumeOpacity(nv, loaded, effectiveLayerOpacity(next));
      if (result === 'updated') {
        if (typeof updates.visible === 'boolean' || previousOpacity === 0 || nextOpacity === 0) return null;
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
      const meshIndex = interop.meshes.indexOf(mesh);
      if (meshIndex >= 0) {
        void nv.setMesh(meshIndex, { visible: mesh.visible, opacity: mesh.opacity });
      }
      needsDraw = true;
    }
  }
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
    const nextOpacity = effectiveLayerOpacity(next);
    const result = setNiivueVolumeOpacity(nv, loaded, nextOpacity);
    if (result === 'updated') {
      action = null;
    }
  }
  if (mesh) {
    mesh.visible = next.visible;
    mesh.opacity = effectiveLayerOpacity(next);
    const meshIndex = interop.meshes.indexOf(mesh);
    if (meshIndex >= 0) {
      void nv.setMesh(meshIndex, { visible: mesh.visible, opacity: mesh.opacity });
    }
    action = action ?? null;
  }
  return action;
}

export function capturePaneSnapshot(nv: Niivue | null | undefined): string | null {
  const canvas = nv ? (nv as unknown as { canvas?: HTMLCanvasElement }).canvas : undefined;
  return canvas ? canvas.toDataURL('image/jpeg', 0.8) : null;
}
