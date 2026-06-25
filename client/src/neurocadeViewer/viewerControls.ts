import { SHOW_RENDER } from '@niivue/niivue';

export type NeuroCadeViewMode = 'axial' | 'coronal' | 'sagittal' | 'multi' | 'render';
export type ViewerSliceType = 0 | 1 | 2 | 3 | 4;
export type ViewerPlaneSliceType = 0 | 1 | 2;
export type ViewerDragMode = 'contrast' | 'pan' | 'measurement';

export const VIEW_MODES: { id: NeuroCadeViewMode; label: string; sliceType: ViewerSliceType; showRender: number }[] = [
  { id: 'multi', label: 'Grid', sliceType: 3, showRender: SHOW_RENDER.ALWAYS },
  { id: 'axial', label: 'Ax', sliceType: 0, showRender: SHOW_RENDER.NEVER },
  { id: 'coronal', label: 'Cor', sliceType: 1, showRender: SHOW_RENDER.NEVER },
  { id: 'sagittal', label: 'Sag', sliceType: 2, showRender: SHOW_RENDER.NEVER },
  { id: 'render', label: '3D', sliceType: 4, showRender: SHOW_RENDER.ALWAYS },
];
