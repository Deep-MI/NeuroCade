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

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const delta = event.clientX - drag.startX;
      const next = edge === 'left' ? drag.startWidth - delta : drag.startWidth + delta;
      setWidth(clamp(next, minWidth, maxWidth));
    };

    const handleMouseUp = () => {
      dragRef.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
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
