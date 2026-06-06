import {
    DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
    DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
} from '../constants';
import type { SurfaceColorMode, SurfaceLayer } from '../types';
import type { SurfaceAnnotationData } from './SurfaceLoader';

export type SurfaceRgb = [number, number, number];

export function curvatureNegativeThreshold(layer: SurfaceLayer): number {
    return layer.curvatureNegativeThreshold ?? DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD;
}

export function curvaturePositiveThreshold(layer: SurfaceLayer): number {
    return layer.curvaturePositiveThreshold ?? DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD;
}

export function surfaceColor(layer: SurfaceLayer): SurfaceRgb {
    const filename = layer.filename.toLowerCase();
    if (filename.startsWith('lh.')) return [0.47, 0.72, 0.96];
    if (filename.startsWith('rh.')) return [0.96, 0.61, 0.42];
    return [0.78, 0.72, 0.62];
}

export function createColors(vertexCount: number, color: SurfaceRgb): Float32Array {
    const colors = new Float32Array(vertexCount * 3);
    for (let i = 0; i < colors.length; i += 3) {
        colors[i] = color[0];
        colors[i + 1] = color[1];
        colors[i + 2] = color[2];
    }
    return colors;
}

export function curvatureColors(
    values: Float32Array,
    fallbackColor: SurfaceRgb,
    negativeThreshold: number,
    positiveThreshold: number,
): Float32Array {
    const negativeScale = Math.max(negativeThreshold, 0.0001);
    const positiveScale = Math.max(positiveThreshold, 0.0001);
    const colors = new Float32Array(values.length * 3);
    const sulcal: SurfaceRgb = [0.14, 0.145, 0.15];
    const gyral: SurfaceRgb = [0.94, 0.91, 0.82];
    const neutral: SurfaceRgb = [
        fallbackColor[0] * 0.12 + 0.58,
        fallbackColor[1] * 0.1 + 0.58,
        fallbackColor[2] * 0.08 + 0.56,
    ];

    for (let i = 0; i < values.length; i += 1) {
        const value = Number.isFinite(values[i]) ? values[i] : 0;
        const magnitude = Math.min(1, Math.abs(value) / (value < 0 ? negativeScale : positiveScale)) ** 0.45;
        const target = value < 0 ? gyral : sulcal;
        const offset = i * 3;
        colors[offset] = neutral[0] * (1 - magnitude) + target[0] * magnitude;
        colors[offset + 1] = neutral[1] * (1 - magnitude) + target[1] * magnitude;
        colors[offset + 2] = neutral[2] * (1 - magnitude) + target[2] * magnitude;
    }
    return colors;
}

export function annotationColors(annotation: SurfaceAnnotationData, fallbackColor: SurfaceRgb): Float32Array {
    const colors = new Float32Array(annotation.labels.length * 3);
    const missing: SurfaceRgb = [fallbackColor[0] * 0.25 + 0.38, fallbackColor[1] * 0.25 + 0.38, fallbackColor[2] * 0.25 + 0.38];
    for (let i = 0; i < annotation.labels.length; i += 1) {
        const label = annotation.labels[i];
        const offset = i * 3;
        const colorOffset = label * 4;
        if (label >= 0 && colorOffset + 2 < annotation.colorTable.length) {
            colors[offset] = annotation.colorTable[colorOffset] / 255;
            colors[offset + 1] = annotation.colorTable[colorOffset + 1] / 255;
            colors[offset + 2] = annotation.colorTable[colorOffset + 2] / 255;
        } else {
            colors[offset] = missing[0];
            colors[offset + 1] = missing[1];
            colors[offset + 2] = missing[2];
        }
    }
    return colors;
}

export const SURFACE_COLOR_MODE_LABELS: Record<SurfaceColorMode, string> = {
    solid: 'solid',
    curvature: 'curvature',
    annotation: 'parcellation',
};

export function surfaceColorModeAvailable(layer: SurfaceLayer, mode: SurfaceColorMode): boolean {
    if (mode === 'curvature') return !!layer.curvatureUrl;
    if (mode === 'annotation') return !!layer.annotationUrl;
    return true;
}

export function defaultSurfaceColorModeForLayer(layer: SurfaceLayer): SurfaceColorMode {
    if (layer.annotationUrl) return 'annotation';
    if (layer.curvatureUrl) return 'curvature';
    return 'solid';
}

export function resolveSurfaceLayerColorMode(layer: SurfaceLayer): SurfaceColorMode {
    const requested = layer.surfaceColorMode ?? defaultSurfaceColorModeForLayer(layer);
    return surfaceColorModeAvailable(layer, requested) ? requested : defaultSurfaceColorModeForLayer(layer);
}

export function resolveSurfaceColorMode(layer: SurfaceLayer, curvature: Float32Array | null, annotation: SurfaceAnnotationData | null): SurfaceColorMode {
    const requested = resolveSurfaceLayerColorMode(layer);
    if (requested === 'solid') return 'solid';
    if (requested === 'curvature' && curvature) return 'curvature';
    if (requested === 'annotation' && annotation) return 'annotation';
    if (annotation) return 'annotation';
    if (curvature) return 'curvature';
    return 'solid';
}

export function colorsForLayer(
    layer: SurfaceLayer,
    fallbackColor: SurfaceRgb,
    vertexCount: number,
    curvature: Float32Array | null,
    annotation: SurfaceAnnotationData | null,
): Float32Array {
    const colorMode = resolveSurfaceColorMode(layer, curvature, annotation);
    if (colorMode === 'annotation' && annotation) {
        return annotationColors(annotation, fallbackColor);
    }
    if (colorMode === 'curvature' && curvature) {
        return curvatureColors(curvature, fallbackColor, curvatureNegativeThreshold(layer), curvaturePositiveThreshold(layer));
    }
    return createColors(vertexCount, fallbackColor);
}
