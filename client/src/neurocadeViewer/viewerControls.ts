import { DRAG_MODE, SHOW_RENDER } from '@niivue/niivue';

export type NeuroCadeViewMode = 'axial' | 'coronal' | 'sagittal' | 'multi' | 'render';
export type ViewerSliceType = 0 | 1 | 2 | 3 | 4;
export type ViewerPlaneSliceType = 0 | 1 | 2;
export type ViewerDragMode = 'contrast' | 'pan' | 'measurement';

export function niivueDragMode(mode: ViewerDragMode): DRAG_MODE {
  return DRAG_MODE[mode];
}

export function inPlaneCrosshairDelta(
  plane: ViewerPlaneSliceType,
  key: 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight',
): [number, number, number] {
  const direction = key === 'ArrowUp' || key === 'ArrowRight' ? 1 : -1;
  if (key === 'ArrowUp' || key === 'ArrowDown') {
    return plane === 0
      ? [0, direction, 0]
      : [0, 0, direction];
  }
  return plane === 2
    ? [0, direction, 0]
    : [direction, 0, 0];
}

export function throughPlaneCrosshairDelta(
  plane: ViewerPlaneSliceType,
  key: 'ArrowUp' | 'ArrowDown',
): [number, number, number] {
  const direction = key === 'ArrowUp' ? 1 : -1;
  if (plane === 0) return [0, 0, direction];
  if (plane === 1) return [0, direction, 0];
  return [direction, 0, 0];
}

interface ViewerScreenSlice {
  axCorSag: number;
  leftTopWidthHeight?: number[];
}

export function planeAtCanvasPosition(
  screenSlices: readonly ViewerScreenSlice[],
  x: number,
  y: number,
): ViewerPlaneSliceType | null {
  for (const { axCorSag, leftTopWidthHeight } of screenSlices) {
    if (axCorSag < 0 || axCorSag > 2 || !leftTopWidthHeight) continue;
    const [left, top, width, height] = leftTopWidthHeight;
    if (
      x >= left
      && x <= left + width
      && y >= top
      && y <= top + height
    ) {
      return axCorSag as ViewerPlaneSliceType;
    }
  }
  return null;
}

export const VIEW_MODES: { id: NeuroCadeViewMode; label: string; sliceType: ViewerSliceType; showRender: number }[] = [
  { id: 'multi', label: 'Grid', sliceType: 3, showRender: SHOW_RENDER.ALWAYS },
  { id: 'axial', label: 'Ax', sliceType: 0, showRender: SHOW_RENDER.NEVER },
  { id: 'coronal', label: 'Cor', sliceType: 1, showRender: SHOW_RENDER.NEVER },
  { id: 'sagittal', label: 'Sag', sliceType: 2, showRender: SHOW_RENDER.NEVER },
  { id: 'render', label: '3D', sliceType: 4, showRender: SHOW_RENDER.ALWAYS },
];
