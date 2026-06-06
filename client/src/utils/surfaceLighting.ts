export type SurfaceLightDirection = readonly [number, number, number];

export const SURFACE_LIGHT_DIRECTIONS = [
  [0.35, 0.72, -0.58],
  [-0.5, -0.15, -0.85],
] as const satisfies readonly SurfaceLightDirection[];

