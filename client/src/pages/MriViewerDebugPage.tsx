import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { MriViewer, type LocationInfo } from '../components/MriViewer';
import { createHeaderMismatchDebugFixture } from '../dev/mriViewerDebugData';

export function MriViewerDebugPage() {
    const fixture = useMemo(() => createHeaderMismatchDebugFixture(), []);
    const [location, setLocation] = useState<LocationInfo | null>(null);
    const [externalCoordinate, setExternalCoordinate] = useState<[number, number, number] | null>(fixture.targetCoordinate);

    useEffect(() => fixture.cleanup, [fixture]);

    const alignmentPasses = location?.labelIndex === 1;

    return (
        <div className="nc-app-page px-6 py-6">
            <div className="mx-auto flex max-w-7xl flex-col gap-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="space-y-2">
                        <p className="nc-eyebrow text-[var(--nc-warning)]">Dev Debug Route</p>
                        <h1 className="text-3xl font-semibold text-[var(--nc-tx)]">Segmentation Header Mismatch Repro</h1>
                        <p className="max-w-3xl text-sm leading-6 text-[var(--nc-tx-muted)]">
                            The bright cube in the intensity volume and the orange segmentation cube represent the same
                            object in world space. The segmentation header is translated by
                            {' '}<span className="font-semibold text-[var(--nc-warning)]">
                                [{fixture.segmentationHeaderOffsetMm.join(', ')}] mm
                            </span>{' '}
                            on the RAS axes, so voxel-index sampling will misalign it.
                        </p>
                    </div>
                    <div className="flex items-center gap-3">
                        <button
                            type="button"
                            onClick={() => setExternalCoordinate([...fixture.targetCoordinate])}
                            className="nc-btn nc-btn-warning"
                        >
                            Jump To Target Cube
                        </button>
                        <Link
                            to="/"
                            className="nc-btn"
                        >
                            Back To App
                        </Link>
                    </div>
                </div>

                <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
                    <aside className="nc-card-static space-y-4 p-5">
                        <div>
                            <p className="nc-eyebrow">Expected Result</p>
                            <p className="mt-2 text-sm leading-6 text-[var(--nc-tx-muted)]">
                                The orange overlay should sit directly on top of the bright intensity cube.
                                At the target voxel, the active segmentation label should be <span className="font-semibold text-[var(--nc-tx)]">1</span>.
                            </p>
                        </div>

                        <div className="rounded border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] p-4">
                            <p className="nc-eyebrow">Current Status</p>
                            <p
                                data-testid="debug-alignment-status"
                                className={`nc-chip mt-3 ${
                                    alignmentPasses
                                        ? 'nc-chip-green'
                                        : 'nc-chip-red'
                                }`}
                            >
                                {alignmentPasses ? 'Aligned at target' : 'Misaligned at target'}
                            </p>
                            <dl className="mt-4 space-y-3 text-sm text-[var(--nc-tx-muted)]">
                                <div>
                                    <dt className="text-[var(--nc-tx-dim)]">Target voxel</dt>
                                    <dd data-testid="debug-target-coordinate" className="nc-mono text-[var(--nc-tx)]">
                                        [{fixture.targetCoordinate.join(', ')}]
                                    </dd>
                                </div>
                                <div>
                                    <dt className="text-[var(--nc-tx-dim)]">Current voxel</dt>
                                    <dd data-testid="debug-current-coordinate" className="nc-mono text-[var(--nc-tx)]">
                                        {location ? `[${location.vox.join(', ')}]` : 'loading'}
                                    </dd>
                                </div>
                                <div>
                                    <dt className="text-[var(--nc-tx-dim)]">Current label index</dt>
                                    <dd data-testid="debug-current-label" className="nc-mono text-[var(--nc-tx)]">
                                        {location?.labelIndex ?? 'loading'}
                                    </dd>
                                </div>
                                <div>
                                    <dt className="text-[var(--nc-tx-dim)]">Current label name</dt>
                                    <dd className="font-medium text-[var(--nc-tx)]">{location?.labelName ?? 'loading'}</dd>
                                </div>
                            </dl>
                        </div>
                    </aside>

                    <div className="min-h-[70vh] border border-[var(--nc-border)] bg-[var(--nc-bg-deep)] p-[3px]">
                        <MriViewer
                            volumes={fixture.volumes}
                            externalCoordinate={externalCoordinate}
                            onLocationChange={setLocation}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
}
