import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { RotateCcw } from 'lucide-react';
import { parseLUT, createBinaryLut, lookupLut, type LutMap } from '../utils/LutParser';
import { sampleNearest, transformVoxel } from '../utils/VolumeLoader';
import { isSegmentationLayer, isSurfaceLayer, type IntensityVolumeLayer, type SegmentationVolumeLayer, type Volume } from '../types';
import { appFetch, appFetchUrl } from '../utils/api';
import { useMriVolumeLoading } from '../hooks/useMriVolumeLoading';
import { clamp, computeIntensityStats, drawMriSlice } from '../utils/mriSliceRenderer';
import { SurfaceViewer } from './SurfaceViewer';

type ViewAxis = 'x' | 'y' | 'z';
type ViewPanelId = ViewAxis | '3d';
type InteractionMode = 'cursor' | 'pan' | 'zoom';

interface PanOffset {
    x: number;
    y: number;
}

type PanOffsets = Record<ViewAxis, PanOffset>;

interface ActiveInteraction {
    axis: ViewAxis;
    mode: InteractionMode;
    startClientX: number;
    startClientY: number;
    startZoom: number;
    startPanX: number;
    startPanY: number;
    zoomAnchorOffsetX?: number;
    zoomAnchorOffsetY?: number;
}

function getVolumeDisplayName(volume: Volume): string {
    return volume.name;
}

function createDefaultPanOffsets(): PanOffsets {
    return {
        x: { x: 0, y: 0 },
        y: { x: 0, y: 0 },
        z: { x: 0, y: 0 },
    };
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 8;
const ZOOM_DRAG_SENSITIVITY = 200;
const BACKGROUND_PRELOAD_VOLUME_LIMIT = 12;

export interface LocationInfo {
    vox: [number, number, number];
    labelIndex: number;
    labelName: string;
    labelColor?: [number, number, number];
}

interface MriViewerProps {
    volumes: Volume[];
    onLocationChange?: (location: LocationInfo) => void;
    externalCoordinate?: [number, number, number] | null;
    /** Fired after a volume is loaded & parsed with a data-based LUT classification. */
    onVolumeLutDetected?: (volumeId: string, detectedLut: 'binary' | 'freesurfer' | undefined) => void;
    showSurfacePlaceholder?: boolean;
}

export interface MriSnapshots {
    sagittal: string;
    coronal: string;
    axial: string;
}

export interface MriViewerRef {
    getSnapshots: () => MriSnapshots | null;
}

export const MriViewer = React.forwardRef<MriViewerRef, MriViewerProps>(({ volumes, onLocationChange, externalCoordinate, onVolumeLutDetected, showSurfacePlaceholder = false }, ref) => {
    const [currentSlices, setCurrentSlices] = useState({ x: 0, y: 0, z: 0 });
    const [hoveredAxis, setHoveredAxis] = useState<'x' | 'y' | 'z' | null>(null);
    const [maximizedView, setMaximizedView] = useState<ViewPanelId | null>(null);
    const [surfaceResetNonce, setSurfaceResetNonce] = useState(0);
    const [lut, setLut] = useState<LutMap | null>(null);
    const [lutFailed, setLutFailed] = useState(false);
    const [customLuts, setCustomLuts] = useState<Map<string, LutMap>>(new Map());
    const binaryLut = useMemo(() => createBinaryLut(), []);
    const [zoom, setZoom] = useState(MIN_ZOOM);
    const [panOffsets, setPanOffsets] = useState<PanOffsets>(() => createDefaultPanOffsets());
    const [activeInteraction, setActiveInteraction] = useState<ActiveInteraction | null>(null);

    const sagittalCanvasRef = useRef<HTMLCanvasElement>(null);
    const coronalCanvasRef = useRef<HTMLCanvasElement>(null);
    const axialCanvasRef = useRef<HTMLCanvasElement>(null);
    const sagittalPanelRef = useRef<HTMLDivElement>(null);
    const coronalPanelRef = useRef<HTMLDivElement>(null);
    const axialPanelRef = useRef<HTMLDivElement>(null);
    const volumeLayers = useMemo(() => volumes.filter(v => !isSurfaceLayer(v)), [volumes]);
    const surfaceLayers = useMemo(() => volumes.filter(isSurfaceLayer), [volumes]);
    const visibleIntensityLayers = useMemo(
        () => volumeLayers.filter((v): v is IntensityVolumeLayer => (v.type ?? 'intensity') === 'intensity' && v.visible),
        [volumeLayers],
    );
    const visibleSegmentationLayers = useMemo(
        () => volumeLayers.filter((v): v is SegmentationVolumeLayer => v.visible && isSegmentationLayer(v)),
        [volumeLayers],
    );
    const showSurfacePanel = showSurfacePlaceholder || surfaceLayers.length > 0;

    useEffect(() => {
        if (maximizedView === '3d' && !showSurfacePanel) {
            setMaximizedView(null);
        }
    }, [maximizedView, showSurfacePanel]);

    const toggleMaximizedView = useCallback((viewId: ViewPanelId) => {
        setMaximizedView((current) => (current === viewId ? null : viewId));
    }, []);

    const panelClassName = useCallback((viewId: ViewPanelId) => {
        if (!maximizedView) return 'view-panel';
        return `view-panel ${maximizedView === viewId ? 'view-panel-maximized' : 'view-panel-hidden'}`;
    }, [maximizedView]);

    React.useImperativeHandle(ref, () => ({
        getSnapshots: () => {
            if (!sagittalCanvasRef.current || !coronalCanvasRef.current || !axialCanvasRef.current) {
                return null;
            }
            return {
                sagittal: sagittalCanvasRef.current.toDataURL('image/jpeg', 0.8),
                coronal: coronalCanvasRef.current.toDataURL('image/jpeg', 0.8),
                axial: axialCanvasRef.current.toDataURL('image/jpeg', 0.8),
            };
        }
    }), []);

    useEffect(() => {
        const needsFreeSurferLut = volumeLayers.some(v =>
            v.visible &&
            v.type === 'segmentation' &&
            !v.customLutUrl &&
            (v.lut === undefined || v.lut === 'freesurfer'),
        );
        if (!needsFreeSurferLut) {
            return;
        }

        // Load LUTs only when a visible segmentation depends on the bundled
        // FreeSurfer lookup table.
        appFetch('/static/luts/freesurfer')
            .then(res => {
                if (!res.ok) throw new Error(`LUT fetch failed: ${res.status}`);
                return res.text();
            })
            .then(text => setLut(parseLUT(text)))
            .catch(err => {
                console.error("Failed to load LUT:", err);
                setLutFailed(true);
            });
    }, [volumeLayers]);

    useEffect(() => {
        const urls = [...new Set(
            volumeLayers
                .filter((v): v is SegmentationVolumeLayer => v.visible && isSegmentationLayer(v))
                .map(v => v.customLutUrl)
                .filter(Boolean) as string[],
        )];
        urls.forEach(url => {
            if (customLuts.has(url)) return;
            appFetchUrl(url)
                .then(res => {
                    if (!res.ok) throw new Error(`Custom LUT fetch failed: ${res.status}`);
                    return res.text();
                })
                .then(text => {
                    setCustomLuts(prev => {
                        const next = new Map(prev);
                        next.set(url, parseLUT(text));
                        return next;
                    });
                })
                .catch(err => console.error("Failed to load custom LUT:", err));
        });
    }, [volumeLayers, customLuts]);

    const baseVolumeUrl = useMemo(() => {
        const visibleBase = visibleIntensityLayers[0];
        if (visibleBase) return visibleBase.url;

        const firstIntensity = volumeLayers.find(v => (v.type === 'intensity' || (v.type ?? 'intensity') === 'intensity'));
        if (firstIntensity) return firstIntensity.url;

        const visibleSegmentation = visibleSegmentationLayers[0];
        if (visibleSegmentation) return visibleSegmentation.url;

        const firstSegmentation = volumeLayers.find(v => v.type === 'segmentation');
        return firstSegmentation?.url ?? null;
    }, [visibleIntensityLayers, visibleSegmentationLayers, volumeLayers]);

    const { loadedVolumes, loadingVolumes } = useMriVolumeLoading({
        volumeLayers,
        baseVolumeUrl,
        backgroundPreloadLimit: BACKGROUND_PRELOAD_VOLUME_LIMIT,
        onVolumeLutDetected,
    });

    const maxSlices = useMemo(() => {
        if (!baseVolumeUrl) return { x: 0, y: 0, z: 0 };
        const data = loadedVolumes.get(baseVolumeUrl);
        if (!data) return { x: 0, y: 0, z: 0 };
        return { x: data.dims[0], y: data.dims[1], z: data.dims[2] };
    }, [baseVolumeUrl, loadedVolumes]);

    const intensityStats = useMemo(() => {
        const stats = new Map<string, { min: number; max: number }>();
        for (const volume of volumeLayers) {
            const type = volume.type ?? 'intensity';
            if (type !== 'intensity') continue;
            const data = loadedVolumes.get(volume.url);
            if (!data) continue;
            stats.set(volume.url, computeIntensityStats(data.data));
        }
        return stats;
    }, [volumeLayers, loadedVolumes]);

    const getCanvasForAxis = useCallback((axis: ViewAxis): HTMLCanvasElement | null => {
        if (axis === 'x') return sagittalCanvasRef.current;
        if (axis === 'y') return coronalCanvasRef.current;
        return axialCanvasRef.current;
    }, []);

    const resetView = useCallback(() => {
        setActiveInteraction(null);
        setZoom(MIN_ZOOM);
        setPanOffsets(createDefaultPanOffsets());
        setSurfaceResetNonce((value) => value + 1);
    }, []);

    useEffect(() => {
        resetView();
    }, [baseVolumeUrl, resetView]);

    const getSliceDimensions = useCallback((axis: ViewAxis): [number, number] | null => {
        if (!baseVolumeUrl) return null;
        const data = loadedVolumes.get(baseVolumeUrl);
        if (!data) return null;
        const [dimX, dimY, dimZ] = data.dims;
        if (axis === 'x') return [dimY, dimZ];
        if (axis === 'y') return [dimX, dimZ];
        return [dimX, dimY];
    }, [baseVolumeUrl, loadedVolumes]);

    const getCanvasDisplayStyle = useCallback((axis: ViewAxis) => {
        const dims = getSliceDimensions(axis);
        const pan = panOffsets[axis];
        const isActiveAxis = activeInteraction?.axis === axis;
        const baseStyle = {
            cursor: isActiveAxis
                ? (activeInteraction?.mode === 'pan' ? 'grabbing' : activeInteraction?.mode === 'zoom' ? 'ns-resize' : 'crosshair')
                : 'crosshair',
            imageRendering: 'pixelated' as const,
            maxWidth: '100%',
            maxHeight: '100%',
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: 'center center',
            willChange: 'transform',
        };
        if (!dims) {
            return {
                ...baseStyle,
                width: '100%',
                height: '100%',
            };
        }
        const [width, height] = dims;
        if (width >= height) {
            return {
                ...baseStyle,
                width: '100%',
                height: 'auto',
            };
        }
        return {
            ...baseStyle,
            width: 'auto',
            height: '100%',
        };
    }, [activeInteraction, getSliceDimensions, panOffsets, zoom]);

    const updateLocationInfo = useCallback((slices: { x: number, y: number, z: number }) => {
        if (!onLocationChange) return;

        let labelIndex = 0;
        let labelName = 'Unknown';
        let labelColor: [number, number, number] | undefined;
        const baseData = baseVolumeUrl ? loadedVolumes.get(baseVolumeUrl) : null;

        if (baseData) {
            for (const activeSeg of visibleSegmentationLayers) {
                const segData = loadedVolumes.get(activeSeg.url);
                if (!segData) continue;

                const segVoxel = transformVoxel(
                    segData.worldToVoxel,
                    transformVoxel(baseData.voxelToWorld, [slices.x, slices.y, slices.z]),
                );
                const candidateLabelIndex = sampleNearest(segData, segVoxel);
                if (candidateLabelIndex <= 0) continue;

                labelIndex = candidateLabelIndex;
                const activeLut = activeSeg.customLutUrl
                    ? (customLuts.get(activeSeg.customLutUrl) ?? lut)
                    : (activeSeg.lut === 'binary' ? binaryLut : lut);
                const entry = lookupLut(activeLut, labelIndex);
                labelName = entry.name;
                labelColor = entry.rgb;
                break;
            }
        }

        // Use setTimeout to avoid side effects during render
        setTimeout(() => {
            if (onLocationChange) {
                onLocationChange({
                    vox: [slices.x, slices.y, slices.z],
                    labelIndex,
                    labelName,
                    labelColor,
                });
            }
        }, 0);
    }, [onLocationChange, visibleSegmentationLayers, loadedVolumes, lut, binaryLut, customLuts, baseVolumeUrl]);

    const changeSlice = useCallback((viewAxis: ViewAxis, delta: number) => {
        setCurrentSlices(prev => {
            const next = { ...prev };
            const axisKey = viewAxis;
            next[axisKey] = Math.max(0, Math.min(prev[axisKey] + delta, maxSlices[viewAxis] - 1));
            return next;
        });
    }, [maxSlices]);

    // Center slices when volumeLayers first load (only once, when still at origin)
    useEffect(() => {
        if (maxSlices.x > 0 && currentSlices.x === 0 && currentSlices.y === 0 && currentSlices.z === 0) {
            setCurrentSlices({
                x: Math.floor(maxSlices.x / 2),
                y: Math.floor(maxSlices.y / 2),
                z: Math.floor(maxSlices.z / 2)
            });
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [maxSlices]);

    // Jump to externally-requested coordinate (e.g. from LLM tool call).
    // Only fires when externalCoordinate actually changes — does NOT
    // depend on currentSlices, so it won't fight user interactions.
    useEffect(() => {
        if (externalCoordinate && maxSlices.x > 0) {
            setCurrentSlices({
                x: Math.max(0, Math.min(Math.round(externalCoordinate[0]), maxSlices.x - 1)),
                y: Math.max(0, Math.min(Math.round(externalCoordinate[1]), maxSlices.y - 1)),
                z: Math.max(0, Math.min(Math.round(externalCoordinate[2]), maxSlices.z - 1))
            });
        }
    }, [externalCoordinate, maxSlices]);

    // Update label info when slices or data change
    useEffect(() => {
        if (maxSlices.x > 0) {
            updateLocationInfo(currentSlices);
        }
    }, [currentSlices, loadedVolumes, lut, maxSlices, updateLocationInfo]);

    // Keyboard navigation
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (!hoveredAxis) return;
            if (e.key === 'ArrowUp' || e.key === 'ArrowRight') {
                e.preventDefault();
                changeSlice(hoveredAxis, 1);
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') {
                e.preventDefault();
                changeSlice(hoveredAxis, -1);
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [hoveredAxis, changeSlice]);

    const updatePositionFromClient = useCallback((clientX: number, clientY: number, canvas: HTMLCanvasElement, axis: ViewAxis) => {
        const rect = canvas.getBoundingClientRect();
        const clientXRel = clientX - rect.left;
        const clientYRel = clientY - rect.top;
        if (clientXRel < 0 || clientXRel > rect.width || clientYRel < 0 || clientYRel > rect.height) {
            return;
        }

        const canvasAspect = canvas.width / canvas.height;
        const rectAspect = rect.width / rect.height;
        const imageWidth = rectAspect > canvasAspect ? rect.height * canvasAspect : rect.width;
        const imageHeight = rectAspect > canvasAspect ? rect.height : rect.width / canvasAspect;
        const imageLeft = (rect.width - imageWidth) / 2;
        const imageTop = (rect.height - imageHeight) / 2;
        const imageXRel = clientXRel - imageLeft;
        const imageYRel = clientYRel - imageTop;
        if (imageXRel < 0 || imageXRel > imageWidth || imageYRel < 0 || imageYRel > imageHeight) {
            return;
        }

        const x = Math.floor(clamp(imageXRel / imageWidth * canvas.width, 0, canvas.width - 1));
        const y = Math.floor(clamp(imageYRel / imageHeight * canvas.height, 0, canvas.height - 1));

        setCurrentSlices(prev => {
            const next = { ...prev };
            if (axis === 'x') { next.y = x; next.z = canvas.height - 1 - y; }
            else if (axis === 'y') { next.x = x; next.z = canvas.height - 1 - y; }
            else { next.x = x; next.y = canvas.height - 1 - y; }
            return next;
        });
    }, []);

    useEffect(() => {
        if (!activeInteraction) return;

        const handleMouseMove = (e: MouseEvent) => {
            if (activeInteraction.mode === 'cursor') {
                const canvas = getCanvasForAxis(activeInteraction.axis);
                if (canvas) {
                    updatePositionFromClient(e.clientX, e.clientY, canvas, activeInteraction.axis);
                }
                return;
            }

            if (activeInteraction.mode === 'pan') {
                const deltaX = e.clientX - activeInteraction.startClientX;
                const deltaY = e.clientY - activeInteraction.startClientY;
                setPanOffsets(prev => {
                    const current = prev[activeInteraction.axis];
                    const nextPan = {
                        x: activeInteraction.startPanX + deltaX,
                        y: activeInteraction.startPanY + deltaY,
                    };
                    if (current.x === nextPan.x && current.y === nextPan.y) {
                        return prev;
                    }
                    return {
                        ...prev,
                        [activeInteraction.axis]: nextPan,
                    };
                });
                return;
            }

            const deltaY = activeInteraction.startClientY - e.clientY;
            const nextZoom = clamp(
                activeInteraction.startZoom * Math.exp(deltaY / ZOOM_DRAG_SENSITIVITY),
                MIN_ZOOM,
                MAX_ZOOM,
            );
            const zoomRatio = nextZoom / activeInteraction.startZoom;
            const anchorOffsetX = activeInteraction.zoomAnchorOffsetX ?? 0;
            const anchorOffsetY = activeInteraction.zoomAnchorOffsetY ?? 0;
            const nextPan = {
                x: activeInteraction.startPanX + anchorOffsetX * (1 - zoomRatio),
                y: activeInteraction.startPanY + anchorOffsetY * (1 - zoomRatio),
            };
            setZoom(prev => (Math.abs(prev - nextZoom) < 0.0001 ? prev : nextZoom));
            setPanOffsets(prev => {
                const current = prev[activeInteraction.axis];
                if (Math.abs(current.x - nextPan.x) < 0.001 && Math.abs(current.y - nextPan.y) < 0.001) {
                    return prev;
                }
                return {
                    ...prev,
                    [activeInteraction.axis]: nextPan,
                };
            });
        };

        const handleMouseUp = () => {
            setActiveInteraction(null);
        };

        window.addEventListener('mousemove', handleMouseMove, { passive: true });
        window.addEventListener('mouseup', handleMouseUp);

        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
        };
    }, [activeInteraction, getCanvasForAxis, updatePositionFromClient]);

    useEffect(() => {
        if (!activeInteraction) {
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            return undefined;
        }

        document.body.style.cursor = activeInteraction.mode === 'pan'
            ? 'grabbing'
            : activeInteraction.mode === 'zoom'
                ? 'ns-resize'
                : 'crosshair';
        document.body.style.userSelect = 'none';

        return () => {
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        };
    }, [activeInteraction]);

    // Wheel event handling with passive: false to allow preventDefault
    useEffect(() => {
        const panels = [
            { ref: sagittalPanelRef, axis: 'x' as const },
            { ref: coronalPanelRef, axis: 'y' as const },
            { ref: axialPanelRef, axis: 'z' as const }
        ];

        const listeners = panels.map(({ ref, axis }) => {
            const element = ref.current;
            if (!element) return null;

            const handleWheel = (e: WheelEvent) => {
                if (e.deltaY !== 0) {
                    e.preventDefault();
                    const delta = e.deltaY < 0 ? 1 : -1;
                    changeSlice(axis, delta);
                }
            };

            element.addEventListener('wheel', handleWheel, { passive: false });
            return { element, handleWheel };
        });

        return () => {
            listeners.forEach(l => {
                if (l) l.element.removeEventListener('wheel', l.handleWheel);
            });
        };
    }, [changeSlice]);

    const handleCanvasMouseDown = useCallback((axis: ViewAxis, canvas: HTMLCanvasElement, e: React.MouseEvent<HTMLCanvasElement>) => {
        if (e.button !== 0 && e.button !== 1 && e.button !== 2) {
            return;
        }

        e.preventDefault();
        setHoveredAxis(axis);

        if (e.button === 0 && !e.shiftKey) {
            setActiveInteraction({
                axis,
                mode: 'cursor',
                startClientX: e.clientX,
                startClientY: e.clientY,
                startZoom: zoom,
                startPanX: panOffsets[axis].x,
                startPanY: panOffsets[axis].y,
            });
            updatePositionFromClient(e.clientX, e.clientY, canvas, axis);
            return;
        }

        if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
            setActiveInteraction({
                axis,
                mode: 'pan',
                startClientX: e.clientX,
                startClientY: e.clientY,
                startZoom: zoom,
                startPanX: panOffsets[axis].x,
                startPanY: panOffsets[axis].y,
            });
            return;
        }

        const rect = canvas.getBoundingClientRect();
        setActiveInteraction({
            axis,
            mode: 'zoom',
            startClientX: e.clientX,
            startClientY: e.clientY,
            startZoom: zoom,
            startPanX: panOffsets[axis].x,
            startPanY: panOffsets[axis].y,
            zoomAnchorOffsetX: e.clientX - (rect.left + rect.width / 2),
            zoomAnchorOffsetY: e.clientY - (rect.top + rect.height / 2),
        });
    }, [panOffsets, updatePositionFromClient, zoom]);

    const pendingVisibleVolumes = useMemo(
        () => volumeLayers.filter(v => v.visible && loadingVolumes.has(v.url) && !loadedVolumes.has(v.url)),
        [volumeLayers, loadingVolumes, loadedVolumes],
    );
    const pendingIntensityVolume = useMemo(
        () => pendingVisibleVolumes.find(v => (v.type ?? 'intensity') === 'intensity') ?? null,
        [pendingVisibleVolumes],
    );
    const pendingSegmentationVolumes = useMemo(
        () => pendingVisibleVolumes.filter(v => v.type === 'segmentation'),
        [pendingVisibleVolumes],
    );
    const isViewReset = zoom === MIN_ZOOM
        && panOffsets.x.x === 0
        && panOffsets.x.y === 0
        && panOffsets.y.x === 0
        && panOffsets.y.y === 0
        && panOffsets.z.x === 0
        && panOffsets.z.y === 0;
    const baseVolumeReady = Boolean(baseVolumeUrl && loadedVolumes.has(baseVolumeUrl));
    const showCenteredLoadingOverlay = pendingIntensityVolume !== null || (!baseVolumeReady && pendingSegmentationVolumes.length > 0);
    const showSegmentationLoadingBadge = baseVolumeReady && pendingSegmentationVolumes.length > 0;
    const overlayTitle = pendingIntensityVolume ? 'Loading intensity volume' : 'Loading label map';
    const overlayDetail = pendingIntensityVolume
        ? getVolumeDisplayName(pendingIntensityVolume)
        : pendingSegmentationVolumes.length === 1
            ? getVolumeDisplayName(pendingSegmentationVolumes[0])
            : `${pendingSegmentationVolumes.length} label maps`;
    const overlaySecondaryDetail = pendingIntensityVolume && pendingSegmentationVolumes.length > 0
        ? (pendingSegmentationVolumes.length === 1
            ? `Waiting for label map: ${getVolumeDisplayName(pendingSegmentationVolumes[0])}`
            : `Waiting for ${pendingSegmentationVolumes.length} label maps`)
        : null;
    const segmentationBadgeText = pendingSegmentationVolumes.length === 1
        ? `Loading label map: ${getVolumeDisplayName(pendingSegmentationVolumes[0])}`
        : `Loading ${pendingSegmentationVolumes.length} label maps`;

    const drawSlice = useCallback((
        canvas: HTMLCanvasElement | null,
        axis: number,
        sliceIdx: number
    ) => {
        drawMriSlice({
            canvas,
            axis,
            sliceIdx,
            baseVolumeUrl,
            loadedVolumes,
            visibleIntensityLayers,
            visibleSegmentationLayers,
            currentSlices,
            lut,
            binaryLut,
            customLuts,
            intensityStats,
        });
    }, [loadedVolumes, visibleIntensityLayers, visibleSegmentationLayers, currentSlices, baseVolumeUrl, lut, binaryLut, customLuts, intensityStats]);

    useEffect(() => {
        drawSlice(sagittalCanvasRef.current, 0, currentSlices.x);
        drawSlice(coronalCanvasRef.current, 1, currentSlices.y);
        drawSlice(axialCanvasRef.current, 2, currentSlices.z);
    }, [drawSlice, currentSlices]);

    return (
        <div className={`views-container ${showSurfacePanel ? 'views-container-4up' : ''} ${maximizedView ? 'views-container-maximized' : ''}`}>
            {lutFailed && (
                <div className="absolute top-0 left-0 right-0 z-50 bg-yellow-700 text-white text-xs text-center py-1 px-2">
                    Region labels unavailable (LUT failed to load)
                </div>
            )}
            <button
                type="button"
                className="mri-reset-view-button"
                onClick={resetView}
                disabled={isViewReset}
                data-testid="mri-reset-view"
                aria-label="Reset view"
                title="Reset view"
            >
                <RotateCcw size={13} />
            </button>
            {showCenteredLoadingOverlay && (
                <div className="mri-loading-overlay" aria-live="polite">
                    <div className="mri-loading-card">
                        <span className="mri-loading-spinner" aria-hidden="true" />
                        <div className="mri-loading-text">
                            <span className="mri-loading-title">{overlayTitle}</span>
                            <span className="mri-loading-detail">{overlayDetail}</span>
                            {overlaySecondaryDetail && (
                                <span className="mri-loading-subdetail">{overlaySecondaryDetail}</span>
                            )}
                        </div>
                    </div>
                </div>
            )}
            {showSegmentationLoadingBadge && (
                <div className="mri-loading-badge" aria-live="polite">
                    <span className="mri-loading-spinner" aria-hidden="true" />
                    <span className="mri-loading-badge-text">{segmentationBadgeText}</span>
                </div>
            )}
            {[
                { name: 'Sagittal', axis: 'x' as const, ref: sagittalCanvasRef, directions: { top: 'S', right: 'A', bottom: 'I', left: 'P' } },
                { name: 'Coronal', axis: 'y' as const, ref: coronalCanvasRef, directions: { top: 'S', right: 'R', bottom: 'I', left: 'L' } },
                { name: 'Axial', axis: 'z' as const, ref: axialCanvasRef, directions: { top: 'A', right: 'R', bottom: 'P', left: 'L' } }
            ].map(view => (
                <div key={view.name} className={panelClassName(view.axis)}
                    data-view-axis={view.axis}
                    ref={view.axis === 'x' ? sagittalPanelRef : view.axis === 'y' ? coronalPanelRef : axialPanelRef}
                    onMouseEnter={() => setHoveredAxis(view.axis)}
                    onMouseLeave={() => setHoveredAxis(null)}>
                    <button
                        type="button"
                        className="view-title"
                        onClick={() => toggleMaximizedView(view.axis)}
                        aria-pressed={maximizedView === view.axis}
                        title={maximizedView === view.axis ? `Restore ${view.name} View` : `Maximize ${view.name} View`}
                    >
                        {view.name} View
                    </button>
                    <div
                        className="view-content"
                        onContextMenu={(e) => e.preventDefault()}
                    >
                        <canvas
                            ref={view.ref}
                            style={getCanvasDisplayStyle(view.axis)}
                            onMouseDown={(e) => {
                                if (!view.ref.current) return;
                                handleCanvasMouseDown(view.axis, view.ref.current, e);
                            }}
                            onAuxClick={(e) => e.preventDefault()}
                        />
                        <span className="view-direction view-direction-top" aria-hidden="true">{view.directions.top}</span>
                        <span className="view-direction view-direction-right" aria-hidden="true">{view.directions.right}</span>
                        <span className="view-direction view-direction-bottom" aria-hidden="true">{view.directions.bottom}</span>
                        <span className="view-direction view-direction-left" aria-hidden="true">{view.directions.left}</span>
                    </div>
                </div>
            ))}
            {showSurfacePanel && (
                <div className={panelClassName('3d')} data-view-axis="3d">
                    <button
                        type="button"
                        className="view-title"
                        onClick={() => toggleMaximizedView('3d')}
                        aria-pressed={maximizedView === '3d'}
                        title={maximizedView === '3d' ? 'Restore 3D Surface' : 'Maximize 3D Surface'}
                    >
                        3D Surface
                    </button>
                    <div className="view-content surface-view-content">
                        <SurfaceViewer surfaces={surfaceLayers} resetNonce={surfaceResetNonce} />
                    </div>
                </div>
            )}
        </div>
    );
});
