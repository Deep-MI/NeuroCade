import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import pako from 'pako';
import { useSurfaceInteraction } from '../hooks/useSurfaceInteraction';
import type { SurfaceColorMode, SurfaceLayer } from '../types';
import { appFetchUrl } from '../utils/api';
import { colorsForLayer, curvatureNegativeThreshold, curvaturePositiveThreshold, resolveSurfaceColorMode, surfaceColor, type SurfaceRgb } from '../utils/surfaceColors';
import { parseFreeSurferAnnotation, parseFreeSurferCurvature, parseFreeSurferSurface, type SurfaceAnnotationData, type SurfaceMeshData } from '../utils/SurfaceLoader';
import { SURFACE_LIGHT_DIRECTIONS } from '../utils/surfaceLighting';

type GL = WebGL2RenderingContext | WebGLRenderingContext;

interface SurfaceViewerProps {
    surfaces: SurfaceLayer[];
    resetNonce: number;
}

interface GpuMesh {
    url: string;
    vertexCount: number;
    faceCount: number;
    indexCount: number;
    opacity: number;
    fallbackColor: SurfaceRgb;
    curvature: Float32Array | null;
    annotation: SurfaceAnnotationData | null;
    colorMode: SurfaceColorMode;
    curvatureNegativeThreshold: number;
    curvaturePositiveThreshold: number;
    positionBuffer: WebGLBuffer;
    normalBuffer: WebGLBuffer;
    colorBuffer: WebGLBuffer;
    indexBuffer: WebGLBuffer;
    bounds: Bounds;
}

interface Bounds {
    minX: number;
    minY: number;
    minZ: number;
    maxX: number;
    maxY: number;
    maxZ: number;
}

interface ProgramInfo {
    program: WebGLProgram;
    position: number;
    normal: number;
    color: number;
    center: WebGLUniformLocation | null;
    scale: WebGLUniformLocation | null;
    pan: WebGLUniformLocation | null;
    rotation: WebGLUniformLocation | null;
    zoom: WebGLUniformLocation | null;
    aspect: WebGLUniformLocation | null;
    opacity: WebGLUniformLocation | null;
}

const VERTEX_SHADER = `
attribute vec3 aPosition;
attribute vec3 aNormal;
attribute vec3 aColor;

uniform vec3 uCenter;
uniform float uScale;
uniform vec2 uPan;
uniform mat3 uRotation;
uniform float uZoom;
uniform float uAspect;

varying vec3 vColor;
varying vec3 vNormal;

void main() {
    vec3 p = uRotation * ((aPosition - uCenter) * uScale);
    vec3 n = normalize(uRotation * aNormal);
    vec2 projected = (p.xy + uPan) * uZoom;

    gl_Position = vec4(projected.x / max(uAspect, 0.001), projected.y, p.z * 0.45, 1.0);
    vColor = aColor;
    vNormal = n;
}
`;

const [KEY_LIGHT_DIRECTION, FILL_LIGHT_DIRECTION] = SURFACE_LIGHT_DIRECTIONS;

const FRAGMENT_SHADER = `
precision mediump float;

uniform float uOpacity;

varying vec3 vColor;
varying vec3 vNormal;

void main() {
    vec3 normal = normalize(vNormal);
    vec3 lightA = normalize(vec3(${KEY_LIGHT_DIRECTION.join(', ')}));
    vec3 lightB = normalize(vec3(${FILL_LIGHT_DIRECTION.join(', ')}));
    float diffuse = max(dot(normal, lightA), 0.0) * 0.48 + max(dot(normal, lightB), 0.0) * 0.18;
    float rim = pow(1.0 - abs(normal.z), 2.0) * 0.18;
    vec3 color = vColor * (0.42 + diffuse) + vec3(rim);
    gl_FragColor = vec4(color, uOpacity);
}
`;

function compileShader(gl: GL, type: number, source: string): WebGLShader {
    const shader = gl.createShader(type);
    if (!shader) throw new Error('Could not create WebGL shader.');
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const message = gl.getShaderInfoLog(shader) ?? 'Unknown shader compile error';
        gl.deleteShader(shader);
        throw new Error(message);
    }
    return shader;
}

function createProgram(gl: GL): ProgramInfo {
    const vertexShader = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
    const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
    const program = gl.createProgram();
    if (!program) throw new Error('Could not create WebGL program.');
    gl.attachShader(program, vertexShader);
    gl.attachShader(program, fragmentShader);
    gl.linkProgram(program);
    gl.deleteShader(vertexShader);
    gl.deleteShader(fragmentShader);

    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        const message = gl.getProgramInfoLog(program) ?? 'Unknown WebGL link error';
        gl.deleteProgram(program);
        throw new Error(message);
    }

    return {
        program,
        position: gl.getAttribLocation(program, 'aPosition'),
        normal: gl.getAttribLocation(program, 'aNormal'),
        color: gl.getAttribLocation(program, 'aColor'),
        center: gl.getUniformLocation(program, 'uCenter'),
        scale: gl.getUniformLocation(program, 'uScale'),
        pan: gl.getUniformLocation(program, 'uPan'),
        rotation: gl.getUniformLocation(program, 'uRotation'),
        zoom: gl.getUniformLocation(program, 'uZoom'),
        aspect: gl.getUniformLocation(program, 'uAspect'),
        opacity: gl.getUniformLocation(program, 'uOpacity'),
    };
}

function meshBounds(vertices: Float32Array): Bounds {
    const bounds = {
        minX: Number.POSITIVE_INFINITY,
        minY: Number.POSITIVE_INFINITY,
        minZ: Number.POSITIVE_INFINITY,
        maxX: Number.NEGATIVE_INFINITY,
        maxY: Number.NEGATIVE_INFINITY,
        maxZ: Number.NEGATIVE_INFINITY,
    };
    for (let i = 0; i < vertices.length; i += 3) {
        const x = vertices[i];
        const y = vertices[i + 1];
        const z = vertices[i + 2];
        if (x < bounds.minX) bounds.minX = x;
        if (y < bounds.minY) bounds.minY = y;
        if (z < bounds.minZ) bounds.minZ = z;
        if (x > bounds.maxX) bounds.maxX = x;
        if (y > bounds.maxY) bounds.maxY = y;
        if (z > bounds.maxZ) bounds.maxZ = z;
    }
    return bounds;
}

function combineBounds(meshes: GpuMesh[]): Bounds | null {
    if (meshes.length === 0) return null;
    return meshes.reduce((acc, mesh) => ({
        minX: Math.min(acc.minX, mesh.bounds.minX),
        minY: Math.min(acc.minY, mesh.bounds.minY),
        minZ: Math.min(acc.minZ, mesh.bounds.minZ),
        maxX: Math.max(acc.maxX, mesh.bounds.maxX),
        maxY: Math.max(acc.maxY, mesh.bounds.maxY),
        maxZ: Math.max(acc.maxZ, mesh.bounds.maxZ),
    }), meshes[0].bounds);
}

function inflateIfNeeded(buffer: ArrayBuffer): ArrayBuffer {
    const signature = new Uint8Array(buffer.slice(0, 2));
    if (signature[0] !== 0x1F || signature[1] !== 0x8B) return buffer;
    const decompressed = pako.inflate(new Uint8Array(buffer));
    return decompressed.buffer.slice(decompressed.byteOffset, decompressed.byteOffset + decompressed.byteLength);
}

function makeBuffer(gl: GL, target: number, data: Float32Array | Uint32Array): WebGLBuffer {
    const buffer = gl.createBuffer();
    if (!buffer) throw new Error('Could not allocate WebGL buffer.');
    gl.bindBuffer(target, buffer);
    gl.bufferData(target, data as unknown as BufferSource, gl.STATIC_DRAW);
    return buffer;
}

function uploadMesh(gl: GL, layer: SurfaceLayer, mesh: SurfaceMeshData, colors: Float32Array, curvature: Float32Array | null, annotation: SurfaceAnnotationData | null): GpuMesh {
    const fallbackColor = surfaceColor(layer);
    return {
        url: layer.url,
        vertexCount: mesh.vertexCount,
        faceCount: mesh.faceCount,
        indexCount: mesh.indices.length,
        opacity: layer.opacity ?? 1,
        fallbackColor,
        curvature,
        annotation,
        colorMode: resolveSurfaceColorMode(layer, curvature, annotation),
        curvatureNegativeThreshold: curvatureNegativeThreshold(layer),
        curvaturePositiveThreshold: curvaturePositiveThreshold(layer),
        positionBuffer: makeBuffer(gl, gl.ARRAY_BUFFER, mesh.vertices),
        normalBuffer: makeBuffer(gl, gl.ARRAY_BUFFER, mesh.normals),
        colorBuffer: makeBuffer(gl, gl.ARRAY_BUFFER, colors),
        indexBuffer: makeBuffer(gl, gl.ELEMENT_ARRAY_BUFFER, mesh.indices),
        bounds: meshBounds(mesh.vertices),
    };
}

function deleteMesh(gl: GL, mesh: GpuMesh): void {
    gl.deleteBuffer(mesh.positionBuffer);
    gl.deleteBuffer(mesh.normalBuffer);
    gl.deleteBuffer(mesh.colorBuffer);
    gl.deleteBuffer(mesh.indexBuffer);
}

function updateMeshAppearance(gl: GL, layer: SurfaceLayer, mesh: GpuMesh): void {
    mesh.opacity = layer.opacity ?? 1;
    const nextColorMode = resolveSurfaceColorMode(layer, mesh.curvature, mesh.annotation);
    const nextNegativeThreshold = curvatureNegativeThreshold(layer);
    const nextPositiveThreshold = curvaturePositiveThreshold(layer);
    const colorsChanged = mesh.colorMode !== nextColorMode
        || (nextColorMode === 'curvature'
            && (mesh.curvatureNegativeThreshold !== nextNegativeThreshold
                || mesh.curvaturePositiveThreshold !== nextPositiveThreshold));
    if (!colorsChanged) return;

    mesh.colorMode = nextColorMode;
    mesh.curvatureNegativeThreshold = nextNegativeThreshold;
    mesh.curvaturePositiveThreshold = nextPositiveThreshold;
    gl.bindBuffer(gl.ARRAY_BUFFER, mesh.colorBuffer);
    gl.bufferSubData(
        gl.ARRAY_BUFFER,
        0,
        colorsForLayer(layer, mesh.fallbackColor, mesh.vertexCount, mesh.curvature, mesh.annotation) as unknown as BufferSource,
    );
}

export const SurfaceViewer: React.FC<SurfaceViewerProps> = ({ surfaces, resetNonce }) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const glRef = useRef<GL | null>(null);
    const programRef = useRef<ProgramInfo | null>(null);
    const meshesRef = useRef<Map<string, GpuMesh>>(new Map());
    const loadingRef = useRef<Set<string>>(new Set());
    const visibleUrlsRef = useRef<Set<string>>(new Set());
    const renderRef = useRef<() => void>(() => undefined);
    const [sceneMetrics, setSceneMetrics] = useState<{ bounds: Bounds | null; meshCount: number }>({
        bounds: null,
        meshCount: 0,
    });
    const [status, setStatus] = useState<string | null>(null);
    const surfaceInteraction = useSurfaceInteraction(resetNonce);
    const { view, handleWheel: handleSurfaceWheel } = surfaceInteraction;

    const visibleSurfaces = useMemo(() => surfaces.filter((surface) => surface.visible), [surfaces]);

    const refreshSceneMetrics = useCallback(() => {
        const meshes = [...meshesRef.current.values()];
        setSceneMetrics({
            bounds: combineBounds(meshes),
            meshCount: meshes.length,
        });
    }, []);

    const render = useCallback(() => {
        const canvas = canvasRef.current;
        const gl = glRef.current;
        const program = programRef.current;
        const bounds = sceneMetrics.bounds;
        if (!canvas || !gl) return;

        const rect = canvas.getBoundingClientRect();
        const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
        const width = Math.max(1, Math.floor(rect.width * dpr));
        const height = Math.max(1, Math.floor(rect.height * dpr));
        if (canvas.width !== width || canvas.height !== height) {
            canvas.width = width;
            canvas.height = height;
        }

        gl.viewport(0, 0, width, height);
        gl.clearColor(0, 0, 0, 1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        if (!program || !bounds) return;

        const centerX = (bounds.minX + bounds.maxX) / 2;
        const centerY = (bounds.minY + bounds.maxY) / 2;
        const centerZ = (bounds.minZ + bounds.maxZ) / 2;
        const extent = Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, bounds.maxZ - bounds.minZ, 1);

        gl.useProgram(program.program);
        gl.uniform3f(program.center, centerX, centerY, centerZ);
        gl.uniform1f(program.scale, 1.64 / extent);
        gl.uniform2f(program.pan, view.panX, view.panY);
        gl.uniformMatrix3fv(program.rotation, false, new Float32Array(view.rotation));
        gl.uniform1f(program.zoom, view.zoom);
        gl.uniform1f(program.aspect, width / height);

        const drawMeshes = [...visibleSurfaces]
            .reverse()
            .map((surface) => meshesRef.current.get(surface.url))
            .filter((mesh): mesh is GpuMesh => Boolean(mesh));
        for (const mesh of drawMeshes) {
            gl.bindBuffer(gl.ARRAY_BUFFER, mesh.positionBuffer);
            gl.enableVertexAttribArray(program.position);
            gl.vertexAttribPointer(program.position, 3, gl.FLOAT, false, 0, 0);

            gl.bindBuffer(gl.ARRAY_BUFFER, mesh.normalBuffer);
            gl.enableVertexAttribArray(program.normal);
            gl.vertexAttribPointer(program.normal, 3, gl.FLOAT, false, 0, 0);

            gl.bindBuffer(gl.ARRAY_BUFFER, mesh.colorBuffer);
            gl.enableVertexAttribArray(program.color);
            gl.vertexAttribPointer(program.color, 3, gl.FLOAT, false, 0, 0);

            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, mesh.indexBuffer);
            gl.uniform1f(program.opacity, mesh.opacity);
            gl.depthMask(mesh.opacity >= 0.999);
            gl.drawElements(gl.TRIANGLES, mesh.indexCount, gl.UNSIGNED_INT, 0);
        }
        gl.depthMask(true);
    }, [sceneMetrics.bounds, view, visibleSurfaces]);

    useEffect(() => {
        renderRef.current = render;
    }, [render]);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || glRef.current) return;
        const gl = canvas.getContext('webgl2', { antialias: true, alpha: false })
            ?? canvas.getContext('webgl', { antialias: true, alpha: false });
        if (!gl) {
            queueMicrotask(() => setStatus('WebGL is unavailable in this browser.'));
            return;
        }
        const isWebGl2 = typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext;
        if (!isWebGl2 && !gl.getExtension('OES_element_index_uint')) {
            queueMicrotask(() => setStatus('This browser cannot draw large indexed surface meshes.'));
            return;
        }

        gl.enable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        glRef.current = gl;
        try {
            programRef.current = createProgram(gl);
            queueMicrotask(() => setStatus(null));
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            queueMicrotask(() => setStatus(message));
        }

        const meshes = meshesRef.current;
        return () => {
            for (const mesh of meshes.values()) {
                deleteMesh(gl, mesh);
            }
            meshes.clear();
        };
    }, []);

    useEffect(() => {
        const gl = glRef.current;
        if (!gl) return;
        const visibleUrls = new Set(visibleSurfaces.map((surface) => surface.url));
        visibleUrlsRef.current = visibleUrls;

        for (const [url, mesh] of meshesRef.current) {
            if (!visibleUrls.has(url)) {
                deleteMesh(gl, mesh);
                meshesRef.current.delete(url);
                refreshSceneMetrics();
            }
        }

        visibleSurfaces.forEach((surface) => {
            const existingMesh = meshesRef.current.get(surface.url);
            if (existingMesh) {
                updateMeshAppearance(gl, surface, existingMesh);
                renderRef.current();
                return;
            }
            if (loadingRef.current.has(surface.url)) return;
            loadingRef.current.add(surface.url);
            setStatus(`Loading ${surface.name}`);
            void appFetchUrl(surface.url)
                .then((response) => {
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    return response.arrayBuffer();
                })
                .then((buffer) => parseFreeSurferSurface(inflateIfNeeded(buffer)))
                .then(async (meshData) => {
                    let curvature: Float32Array | null = null;
                    let annotation: SurfaceAnnotationData | null = null;
                    const fallbackColor = surfaceColor(surface);
                    if (surface.curvatureUrl) {
                        try {
                            const curvatureResponse = await appFetchUrl(surface.curvatureUrl);
                            if (!curvatureResponse.ok) throw new Error(`HTTP ${curvatureResponse.status}`);
                            const curvatureBuffer = inflateIfNeeded(await curvatureResponse.arrayBuffer());
                            curvature = parseFreeSurferCurvature(curvatureBuffer, meshData.vertexCount);
                        } catch (error) {
                            console.warn(`[SurfaceViewer] Could not load curvature for ${surface.name}:`, error);
                        }
                    }
                    if (surface.annotationUrl) {
                        try {
                            const annotationResponse = await appFetchUrl(surface.annotationUrl);
                            if (!annotationResponse.ok) throw new Error(`HTTP ${annotationResponse.status}`);
                            const annotationBuffer = inflateIfNeeded(await annotationResponse.arrayBuffer());
                            annotation = parseFreeSurferAnnotation(annotationBuffer, meshData.vertexCount);
                        } catch (error) {
                            console.warn(`[SurfaceViewer] Could not load annotation for ${surface.name}:`, error);
                        }
                    }
                    const colors = colorsForLayer(surface, fallbackColor, meshData.vertexCount, curvature, annotation);
                    const currentGl = glRef.current;
                    if (!currentGl || !visibleUrlsRef.current.has(surface.url)) return;
                    meshesRef.current.set(surface.url, uploadMesh(currentGl, surface, meshData, colors, curvature, annotation));
                    setStatus(null);
                    refreshSceneMetrics();
                })
                .catch((error) => {
                    setStatus(`Could not load ${surface.name}: ${error instanceof Error ? error.message : String(error)}`);
                })
                .finally(() => {
                    loadingRef.current.delete(surface.url);
                });
        });
    }, [refreshSceneMetrics, visibleSurfaces]);

    useEffect(() => {
        render();
    }, [render, sceneMetrics]);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const observer = new ResizeObserver(() => render());
        observer.observe(canvas);
        return () => observer.disconnect();
    }, [render]);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const handleWheel = (event: WheelEvent) => {
            handleSurfaceWheel(event);
        };
        canvas.addEventListener('wheel', handleWheel, { passive: false });
        return () => canvas.removeEventListener('wheel', handleWheel);
    }, [handleSurfaceWheel]);

    const hasMeshes = sceneMetrics.meshCount > 0;

    return (
        <div className="surface-viewer">
            <canvas
                ref={canvasRef}
                className="surface-canvas"
                tabIndex={0}
                onPointerDown={surfaceInteraction.handlePointerDown}
                onPointerMove={surfaceInteraction.handlePointerMove}
                onKeyDown={surfaceInteraction.handleKeyDown}
                onPointerUp={surfaceInteraction.handlePointerUp}
                onPointerCancel={surfaceInteraction.handlePointerCancel}
                onContextMenu={(event) => event.preventDefault()}
            />
            {status && !hasMeshes && (
                <div className="surface-message">{status}</div>
            )}
        </div>
    );
};
