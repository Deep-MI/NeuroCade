import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import {
    crossVec3,
    DEFAULT_SURFACE_VIEW,
    dotVec3,
    multiplyMat3,
    projectToTrackball,
    rotationMatrixFromAxisAngle,
    type Mat3,
    type SurfaceViewState,
    type Vec3,
} from '../utils/surfaceMath';

type DragMode = 'rotate' | 'pan';

interface SurfaceWheelEvent {
    deltaY: number;
    preventDefault: () => void;
    stopPropagation: () => void;
}

export function useSurfaceInteraction(resetNonce: number) {
    const dragRef = useRef<{ x: number; y: number; mode: DragMode; lastVector: Vec3 } | null>(null);
    const lastClickRef = useRef(0);
    const [view, setView] = useState<SurfaceViewState>(DEFAULT_SURFACE_VIEW);

    const resetView = useCallback(() => {
        dragRef.current = null;
        setView(DEFAULT_SURFACE_VIEW);
    }, []);

    useEffect(() => {
        dragRef.current = null;
        // Resetting the imperative surface view is the purpose of resetNonce.
        setView(DEFAULT_SURFACE_VIEW);
    }, [resetNonce]);

    const handlePointerDown = useCallback((event: PointerEvent<HTMLCanvasElement>) => {
        event.currentTarget.focus();
        const now = window.performance.now();
        if (event.button === 0 && now - lastClickRef.current < 300) {
            resetView();
        }
        lastClickRef.current = now;
        const mode: DragMode = event.shiftKey || event.button === 1 || event.button === 2 ? 'pan' : 'rotate';
        dragRef.current = {
            x: event.clientX,
            y: event.clientY,
            mode,
            lastVector: projectToTrackball(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect()),
        };
        event.currentTarget.setPointerCapture(event.pointerId);
    }, [resetView]);

    const handlePointerMove = useCallback((event: PointerEvent<HTMLCanvasElement>) => {
        const drag = dragRef.current;
        if (!drag) return;
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        const rect = event.currentTarget.getBoundingClientRect();
        if (drag.mode === 'pan') {
            const scale = 1.25 / Math.max(Math.min(rect.width, rect.height), 1);
            setView((current) => ({
                ...current,
                panX: current.panX + dx * scale / current.zoom,
                panY: current.panY - dy * scale / current.zoom,
            }));
        } else {
            const sensitivity = 2.5;
            const currentVector = projectToTrackball(
                drag.x + dx * sensitivity,
                drag.y + dy * sensitivity,
                rect,
            );
            const axis = crossVec3(currentVector, drag.lastVector);
            const axisLength = Math.hypot(axis[0], axis[1], axis[2]);
            if (axisLength >= 0.0001) {
                const angle = Math.atan2(axisLength, dotVec3(drag.lastVector, currentVector));
                const delta = rotationMatrixFromAxisAngle(axis, angle);
                setView((current) => ({
                    ...current,
                    rotation: multiplyMat3(delta, current.rotation),
                }));
                drag.lastVector = projectToTrackball(event.clientX, event.clientY, rect);
            }
        }
        drag.x = event.clientX;
        drag.y = event.clientY;
    }, []);

    const handlePointerUp = useCallback((event: PointerEvent<HTMLCanvasElement>) => {
        dragRef.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
    }, []);

    const handlePointerCancel = useCallback(() => {
        dragRef.current = null;
    }, []);

    const handleWheel = useCallback((event: SurfaceWheelEvent) => {
        event.preventDefault();
        event.stopPropagation();
        setView((current) => ({
            ...current,
            zoom: Math.max(0.08, Math.min(8, current.zoom * Math.exp(-event.deltaY / 600))),
        }));
    }, []);

    const handleKeyDown = useCallback((event: KeyboardEvent<HTMLCanvasElement>) => {
        const delta = Math.PI / 60;
        let rotation: Mat3 | null = null;
        if (event.key === 'ArrowRight') rotation = rotationMatrixFromAxisAngle([0, 1, 0], -delta);
        if (event.key === 'ArrowLeft') rotation = rotationMatrixFromAxisAngle([0, 1, 0], delta);
        if (event.key === 'ArrowUp') rotation = rotationMatrixFromAxisAngle([1, 0, 0], delta);
        if (event.key === 'ArrowDown') rotation = rotationMatrixFromAxisAngle([1, 0, 0], -delta);
        if (event.key.toLowerCase() === 'r') {
            event.preventDefault();
            resetView();
            return;
        }
        if (!rotation) return;
        event.preventDefault();
        setView((current) => ({ ...current, rotation: multiplyMat3(rotation, current.rotation) }));
    }, [resetView]);

    return {
        view,
        handlePointerDown,
        handlePointerMove,
        handlePointerUp,
        handlePointerCancel,
        handleWheel,
        handleKeyDown,
    };
}
