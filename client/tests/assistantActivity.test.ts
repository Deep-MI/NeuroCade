import assert from 'node:assert/strict';
import test from 'node:test';

import {
    assistantActivityMessage,
    assistantActivityProgress,
} from '../src/components/assistantActivity.js';

void test('describes blocking synchronous workflows with their device', () => {
    assert.equal(
        assistantActivityMessage(
            {
                kind: 'workflow',
                label: 'SynthSeg',
                blocking: true,
                mode: 'synchronous',
                device: 'cpu',
            },
            true,
            'Assistant is thinking',
        ),
        'Running SynthSeg on CPU — assistant is waiting…',
    );
});

void test('distinguishes background handoff from a disconnected assistant stream', () => {
    assert.equal(
        assistantActivityMessage(
            { kind: 'workflow', label: 'FastSurfer', blocking: false, mode: 'background' },
            false,
            'Assistant is thinking',
        ),
        'FastSurfer is continuing in the background…',
    );
    assert.equal(
        assistantActivityMessage(null, false, 'Assistant is thinking'),
        'Live updates disconnected — assistant is still working…',
    );
});

void test('describes image download progress and exposes a bounded fraction', () => {
    const activity = {
        kind: 'image' as const,
        label: 'vnmd/freesurfer_8.2.0:20260818',
        blocking: true,
        phase: 'downloading' as const,
        progress: 0.625,
        current_bytes: 5 * 1024 ** 3,
        total_bytes: 8 * 1024 ** 3,
    };
    assert.equal(
        assistantActivityMessage(activity, true, 'Assistant is thinking'),
        'Downloading vnmd/freesurfer_8.2.0:20260818 — 5.0 GiB / 8.0 GiB (63%)…',
    );
    assert.equal(assistantActivityProgress(activity), 0.625);
    assert.equal(assistantActivityProgress({ ...activity, progress: 2 }), 1);
});

void test('uses indeterminate progress for preparation and reports a live heartbeat', () => {
    const activity = {
        kind: 'image' as const,
        label: 'vnmd/freesurfer:8.2',
        blocking: true,
        phase: 'preparing' as const,
        progress: 0.9,
        stalled_seconds: 45,
        process_active: true,
    };
    assert.equal(
        assistantActivityMessage(activity, true, 'Assistant is thinking'),
        'Preparing vnmd/freesurfer:8.2… No progress update for 45s; Docker is still active.',
    );
    assert.equal(assistantActivityProgress(activity), null);
});
