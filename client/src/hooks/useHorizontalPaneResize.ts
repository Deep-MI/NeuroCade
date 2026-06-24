import { useCallback, useEffect, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';

interface PaneResizeOptions {
  minWidth?: number;
  maxWidth?: number;
  edge?: 'left' | 'right';
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function useHorizontalPaneResize(initialWidth: number, options: PaneResizeOptions = {}) {
  const { minWidth = 180, maxWidth = 520, edge = 'right' } = options;
  const [width, setWidth] = useState(initialWidth);
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const pendingWidthRef = useRef<number | null>(null);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    const flushWidth = () => {
      frameRef.current = null;
      const nextWidth = pendingWidthRef.current;
      pendingWidthRef.current = null;
      if (nextWidth !== null) {
        setWidth((current) => current === nextWidth ? current : nextWidth);
      }
    };

    const scheduleWidth = (nextWidth: number) => {
      pendingWidthRef.current = nextWidth;
      frameRef.current ??= requestAnimationFrame(flushWidth);
    };

    const handleMouseMove = (event: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const delta = event.clientX - drag.startX;
      const next = edge === 'left' ? drag.startWidth - delta : drag.startWidth + delta;
      scheduleWidth(clamp(next, minWidth, maxWidth));
    };

    const handleMouseUp = () => {
      dragRef.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      const nextWidth = pendingWidthRef.current;
      pendingWidthRef.current = null;
      if (nextWidth !== null) {
        setWidth((current) => current === nextWidth ? current : nextWidth);
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      pendingWidthRef.current = null;
    };
  }, [edge, maxWidth, minWidth]);

  const startResize = useCallback((event: ReactMouseEvent) => {
    event.preventDefault();
    dragRef.current = { startX: event.clientX, startWidth: width };
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
  }, [width]);

  return [width, startResize] as const;
}
