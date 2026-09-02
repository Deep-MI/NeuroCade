import { useEffect, useRef } from 'react';
import { HelpCircle, X } from 'lucide-react';

const HELP_SHORTCUTS = [
  ['Up / Down', 'Move through slices in the active view. In Grid, the last hovered or clicked 2D tile sets the view.'],
  ['Left / Right', 'Move the crosshair left/right within the active view.'],
  ['Ctrl + Arrow keys', 'Move the crosshair left/right or up/down within the active view.'],
  ['H / L', 'Move crosshair left/right in 2D; rotate 3D azimuth in render view.'],
  ['J / K', 'Move crosshair down/up in 2D; rotate 3D elevation in render view.'],
  ['M', 'Cycle Niivue drag mode.'],
  ['V', 'Cycle Niivue view mode.'],
  ['C', 'Cycle clip-plane preset.'],
  ['P', 'Cycle active clip plane.'],
  ['?', 'Show Niivue version.'],
];

const HELP_TOOLS = [
  ['Left-click', 'Set the inspected voxel and update the location readout.'],
  ['Window / Level', 'Right-drag to adjust intensity display.'],
  ['Pan / Zoom', 'Right-drag or scroll to move or zoom the current view.'],
  ['Measure', 'Right-drag on a 2D slice to draw a distance line with millimeter length.'],
  ['Ax / Cor / Sag / 3D / Grid', 'Switch between orthogonal slice views, volume rendering, and multiplanar layout.'],
  ['Reset', 'Restore view scale, pan, clip plane, render angle, and intensity windowing.'],
];

interface ViewerHelpDialogProps {
  onClose: () => void;
}

export function ViewerHelpDialog({ onClose }: ViewerHelpDialogProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div className="nc-viewer-help-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="nc-viewer-help-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="viewer-help-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="nc-viewer-help-header">
          <div className="nc-viewer-help-title">
            <HelpCircle size={15} />
            <h3 id="viewer-help-title">Viewer Help</h3>
          </div>
          <button ref={closeButtonRef} type="button" className="nc-upload-dialog-close" onClick={onClose} aria-label="Close viewer help">
            <X size={16} />
          </button>
        </header>
        <div className="nc-viewer-help-body">
          <section className="nc-viewer-help-section">
            <h4>General</h4>
            <p>
              NeuroCade displays intensity volumes, segmentations, and surfaces in a Niivue MRI viewer.
              Use the layer panel to show, hide, reorder, recolor, and adjust opacity or windowing.
            </p>
            <p>
              Left-click inspects voxels and moves the crosshair. The mouse tools in the bottom toolbar choose the
              right-click drag action.
            </p>
          </section>
          <section className="nc-viewer-help-section">
            <h4>Viewer Tools</h4>
            <dl className="nc-viewer-help-list">
              {HELP_TOOLS.map(([key, description]) => (
                <div key={key} className="nc-viewer-help-row">
                  <dt>{key}</dt>
                  <dd>{description}</dd>
                </div>
              ))}
            </dl>
          </section>
          <section className="nc-viewer-help-section">
            <h4>Keyboard</h4>
            <dl className="nc-viewer-help-list">
              {HELP_SHORTCUTS.map(([key, description]) => (
                <div key={key} className="nc-viewer-help-row">
                  <dt className="nc-mono">{key}</dt>
                  <dd>{description}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>
        <footer className="nc-viewer-help-footer">
          <span>Nearest-neighbour interpolation is used by default for crisp voxel and label display.</span>
          <button type="button" className="nc-btn nc-btn-active" onClick={onClose}>Close</button>
        </footer>
      </section>
    </div>
  );
}
