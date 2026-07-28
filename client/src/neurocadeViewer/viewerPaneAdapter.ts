import type Niivue from '@niivue/niivue';

import { type Volume } from '../types.js';
import { asNiivueInterop } from '../utils/niivueInterop.js';
import { effectiveLayerOpacity } from './layerDisplay.js';
import { setLoadedVolumeOpacity } from './loadedVolumeDisplay.js';

export type PaneRenderAction =
  | { kind: 'draw' }
  | { kind: 'refresh' }
  | null;

export function applyLayerDisplay(nv: Niivue, id: string, next: Volume, updates: Partial<Volume>): PaneRenderAction {
  const interop = asNiivueInterop(nv);
  const loaded = interop.volumes.find((volume) => volume.id === id);
  let needsDraw = false;
  let handledLoadedDisplay = false;
  if (loaded) {
    if (typeof updates.visible === 'boolean' || typeof updates.opacity === 'number') {
      handledLoadedDisplay = true;
      const result = setLoadedVolumeOpacity(nv, loaded, effectiveLayerOpacity(next));
      if (result === 'updated') {
        return { kind: 'refresh' };
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
  let volumeChanged = false;
  if (loaded) {
    const nextOpacity = effectiveLayerOpacity(next);
    const result = setLoadedVolumeOpacity(nv, loaded, nextOpacity);
    if (result === 'updated') {
      volumeChanged = true;
    }
  }
  if (mesh) {
    mesh.visible = next.visible;
    mesh.opacity = effectiveLayerOpacity(next);
    const meshIndex = interop.meshes.indexOf(mesh);
    if (meshIndex >= 0) {
      void nv.setMesh(meshIndex, { visible: mesh.visible, opacity: mesh.opacity });
    }
    if (!volumeChanged) return { kind: 'draw' };
  }
  return volumeChanged ? { kind: 'refresh' } : null;
}

export function capturePaneSnapshot(nv: Niivue | null | undefined): string | null {
  const canvas = nv ? (nv as unknown as { canvas?: HTMLCanvasElement }).canvas : undefined;
  return canvas ? canvas.toDataURL('image/jpeg', 0.8) : null;
}
