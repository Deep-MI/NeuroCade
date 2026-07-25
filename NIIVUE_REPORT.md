# Private NiiVue Upstream Audit

This is a private NeuroCade working document. It is not an upstream issue or PR
and should not be published without a separate review.

Audit date: 2026-07-25

Upstream repository: [`niivue/mono`](https://github.com/niivue/mono)

Upstream revision inspected:
[`685c74bb68b4e05bb9eb039a664d6a0059abc7a4`](https://github.com/niivue/mono/commit/685c74bb68b4e05bb9eb039a664d6a0059abc7a4)
(`main`, 2026-07-23)

NeuroCade version inspected: `@niivue/niivue@1.0.0-rc.10`

## Verification scope

For every item below, the audit checked:

- the implementation at the pinned upstream `main` revision;
- all eight open upstream PRs: #62, #63, #72, #74, #76, #80, #82, and #84;
- every branch currently visible in the upstream repository:
  `20260707-more-events`, `brain2print`,
  `chore/add-niivue-unit-tests`, `fix4d`, `mrsi-core`, and
  `ohif-viewer-integration`;
- open upstream issues for matching reports.

“No pending implementation” means no matching change was found in those public
PRs or branches. It cannot rule out unpublished work or branches in private
forks. The repository has no `dev` branch; active development is on `main`.

## Summary

| ID | Finding | Upstream status | Existing work | Recommendation |
| --- | --- | --- | --- | --- |
| NV-1 | Multiple overlays are combined with additive RGB and maximum alpha, producing over-bright colors | Present on `main` | No issue, PR, or feature branch found | Strong standalone bug-fix PR candidate |
| NV-2 | Meshes render after the 2D crosshair and can cover it | Present in WebGL2 and WebGPU | No matching issue, PR, or feature branch found | Strong standalone bug-fix PR candidate |
| NV-3 | Only left and top orientation labels are drawn | Present in WebGL2 and WebGPU | No matching issue, PR, or feature branch found | Small standalone enhancement PR candidate |
| NV-4 | 2D zoom does not keep the crosshair fixed and does not use the mouse position | Present on `main` | Bug tracked by [issue #68](https://github.com/niivue/mono/issues/68); no implementation PR or branch found | Coordinate with #68; separate correctness fix from pointer-centered enhancement |
| NV-5 | Layers cannot be independently visible in slice and render tiles of one canvas | Capability still missing | Mesh portion overlaps [issue #60](https://github.com/niivue/mono/issues/60); no implementation PR or branch found | Discuss API first, then implement both mesh and volume support |
| NV-6 | 3D wheel zoom cannot pivot around the object under the mouse | Capability still missing | No matching issue, PR, or feature branch found | Lower-priority enhancement; existing `renderPivotMM` is a partial building block |
| NV-7 | Built-in FreeSurfer label LUT is unexpectedly translucent | Present on `main` | Fix pending in [PR #74](https://github.com/niivue/mono/pull/74) | Do not duplicate |
| NV-8 | Volume-load worker falls back because a header cannot be structured-cloned | Fixed on `main` | Fixed by [`f2a0e3f`](https://github.com/niivue/mono/commit/f2a0e3f5ab5b630306cd6f07550b33e070dc8eb1) | Upgrade instead of proposing another fix |

## Confirmed upstream PR candidates

### NV-1: use correct source-over compositing for multiple overlays

Observed behavior:

- two visible segmentations become too bright where they overlap;
- a FreeSurfer segmentation changes color when an intensity volume is enabled;
- increasing label opacity can make the result look severely oversaturated;
- the same label colors look correct when the intensity volume is disabled and
  in the 3D surface view.

Both current compositors accumulate every premultiplied RGB value additively but
retain only the maximum alpha. They then divide the accumulated RGB by that
maximum alpha:

- [WebGL2/CPU `NVMeshView.ts#L103-L136`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/view/NVMeshView.ts#L103-L136)
- [WebGPU `orient.ts#L948-L986`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/wgpu/orient.ts#L948-L986)

For two 50%-opaque overlays, RGB contributions from both layers are added while
the stored alpha remains 50%. Un-premultiplication therefore makes the result
roughly twice as bright. This is not a FreeSurfer RGB-table error; the presence
of another volume merely exposes the compositing error.

Proposed fix:

- composite each overlay in render order with Porter-Duff source-over;
- update both accumulated premultiplied RGB and accumulated alpha:
  `out = src + dst * (1 - srcAlpha)`;
- un-premultiply only once when producing the final RGBA texture.
- make equivalent changes to the CPU and WebGPU implementations.

Suggested tests:

- two identical 50%-opaque pixels produce the source-over result, not doubled
  RGB;
- two differently colored overlays respect layer order;
- a transparent overlay is a no-op;
- a fully opaque top overlay replaces lower layers;
- three overlays remain bounded and do not saturate prematurely.

Pending-work check:

- no matching open issue;
- none of the eight open PRs changes `blendOverlayData` or the WebGPU blend
  compute shader;
- none of the six non-main upstream branches changes either compositor.

### NV-2: render the crosshair as top-level 2D UI

Observed behavior:

- the crosshair looks three-dimensional;
- with a surface visible, portions of the crosshair disappear “behind” the
  surface or slice intersection.

Both renderers currently draw the normal crosshair before meshes:

- [WebGL2 `NVViewGL.ts#L984-L1055`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/gl/NVViewGL.ts#L984-L1055)
- [WebGPU `NVViewGPU.ts#L1285-L1377`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/wgpu/NVViewGPU.ts#L1285-L1377)

Crosscut mesh fragments are then able to cover the crosshair. For a 2D viewer,
the crosshair is interaction chrome and should remain legible independently of
scene depth.

Proposed fix:

- draw the normal 2D crosshair after the normal mesh pass;
- retain the existing x-ray behavior as a separate pass;
- make the same ordering change in WebGL2 and WebGPU.

Suggested tests:

- a crosscut surface intersecting a slice cannot hide the crosshair;
- crosshair gap and opacity still behave as before;
- x-ray mode remains correct;
- 3D render and mosaic behavior is unchanged.

Pending-work check:

- no matching open issue;
- [PR #62](https://github.com/niivue/mono/pull/62) changes crosshair colors,
  not normal-pass ordering;
- the `brain2print` branch changes crosshair x-ray handling, not this ordering;
- no other open PR or feature branch contains a matching fix.

### NV-3: render all four anatomical direction labels

Observed behavior:

- right and bottom direction labels are absent in multiplanar/grid view;
- for example, a tile can show `L` and `A` but omit `R` and `P`.

Both backends currently enqueue only a left-center and center-top label:

- [WebGL2 `NVViewGL.ts#L1192-L1263`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/gl/NVViewGL.ts#L1192-L1263)
- [WebGPU `NVViewGPU.ts#L1541-L1614`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/wgpu/NVViewGPU.ts#L1541-L1614)

Proposed fix:

- add the complementary right-center and center-bottom labels;
- derive all four labels from one shared orientation helper to prevent WebGL2
  and WebGPU from drifting;
- preserve radiological/neurological convention handling.

Suggested tests:

- axial, coronal, and sagittal label tuples in neurological mode;
- the same tuples in radiological mode;
- positions stay inside small grid tiles;
- WebGL2 and WebGPU use the same label values and anchors.

Pending-work check:

- no matching open issue, PR, or upstream feature branch found.

## Existing upstream work that needs coordination

### NV-4: correct 2D zoom anchoring, then consider pointer-centered zoom

Upstream already tracks the correctness bug as
[`#68`](https://github.com/niivue/mono/issues/68): zoom is intended to stay
centered on the crosshair, but the crosshair visibly drifts.

The wheel and drag paths update pan by multiplying the zoom delta by absolute
world coordinates:

- [wheel zoom in `interactions.ts#L1986-L2008`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/control/interactions.ts#L1986-L2008)
- [drag zoom in `dragModes.ts#L414-L432`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/control/dragModes.ts#L414-L432)

That transform is not invariant under the viewer’s actual pan/zoom projection,
so it does not keep the crosshair at the same screen coordinate.

There are two separate desired behaviors:

1. Fix #68 so the current crosshair-centered contract is mathematically correct.
2. Add an optional pointer-centered mode that preserves the world point under
   the wheel event. The current code never uses the event’s tile-local cursor
   coordinate when calculating zoom.

These should not be conflated in one behavioral change without maintainer
agreement. A safe first PR would fix #68 and add a screen-invariance test. A
second enhancement could introduce an explicit zoom-anchor option such as
`'crosshair' | 'pointer' | 'view-center'`.

Pending-work check:

- no open PR implements #68;
- [PR #80](https://github.com/niivue/mono/pull/80) adds pan/zoom change
  events but leaves the existing math intact;
- upstream commit `850ac32` implements crosshair-centered behavior in one demo,
  not in the core interaction code.

### NV-5: add per-view layer visibility in a single canvas

NeuroCade needs independent defaults and controls per layer:

- surfaces: visible in 3D, hidden in 2D by default;
- intensity volumes: visible in 2D, hidden in 3D by default;
- segmentations: normally visible in both.

NiiVue uses one global volume collection and one global mesh collection for all
tiles. Meshes have a global `visible` option and can select a different
`sliceShaderType`, but `sliceShaderType: ''` means “inherit”; there is no “do not
draw in slices” state:

- [`MeshFromUrlOptions` in `NVTypes.ts#L1094-L1126`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/NVTypes.ts#L1094-L1126)
- [`sliceShaderType` validation in `NVControlBase.ts#L2746-L2767`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/NVControlBase.ts#L2746-L2767)

Issue [#60](https://github.com/niivue/mono/issues/60) explicitly mentions a
three-state mesh slice shader, including no shader. The independent
`sliceShaderType` portion has since landed, but the “none” state has not.
Per-view volume visibility is not covered by the current API.

Potential API:

- `visibleInSlices?: boolean`
- `visibleInRender?: boolean`

These should exist consistently on volumes and meshes and be serializable in an
`NVDocument`. Renderers can filter layers per tile without unloading them, so a
visibility toggle remains fast and does not duplicate CPU/GPU resources.

Questions to settle before implementation:

- should the new flags combine with global `visible` using logical AND;
- whether hidden render volumes should still establish geometry, transforms, or
  the reference grid;
- whether hidden layers should appear in legends/colorbars;
- whether the flags should affect picking and location readouts;
- backward-compatible defaults should be `true` for both views.

Pending-work check:

- #60 is open;
- no open PR or visible feature branch implements the missing “none” state or
  per-view volume visibility.

### NV-6: pointer-centered 3D zoom

The 3D wheel path changes only `scaleMultiplier`; it does not use cursor
position:

[`interactions.ts#L2029-L2050`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/control/interactions.ts#L2029-L2050)

NiiVue already exposes `renderPivotMM`; `null` means the volume center and an
application can set it to the crosshair:

[`NVControlBase.ts#L621-L632`](https://github.com/niivue/mono/blob/685c74bb68b4e05bb9eb039a664d6a0059abc7a4/packages/niivue/src/NVControlBase.ts#L621-L632)

That supports crosshair-centered orbit/zoom but not automatic zoom toward the
surface or volume sample beneath the pointer. True pointer-centered 3D zoom
would require a depth pick or ray/volume intersection, followed by coordinated
updates to pivot and pan so the picked point remains under the cursor.

No matching open issue, PR, or visible feature branch was found. This is a
larger enhancement and should follow the 2D correctness work.

## Already pending or fixed upstream

### NV-7: FreeSurfer LUT alpha is already covered by PR #74

The built-in `freesurfer.json` currently assigns alpha `64` to non-zero labels.
NiiVue mono honors that alpha, while classic NiiVue ignored it for label maps.
This makes the built-in labels unexpectedly faint or transparent.

[PR #74](https://github.com/niivue/mono/pull/74) changes those entries to
opaque alpha (`255`) and also allows `setColormapLabel()` to accept a built-in
colormap name. We should not create a duplicate proposal.

This alpha issue is separate from NV-1:

- PR #74 fixes the opacity encoded in the built-in LUT;
- NV-1 fixes incorrect color math when more than one overlay is composed.

### NV-8: the volume worker clone failure is fixed on `main`

The observed warning was:

> volumeLoad worker failed, falling back to main thread: ... function ... could
> not be cloned

The root cause was a NIfTI header with own function properties crossing
`postMessage`. Upstream commit
[`f2a0e3f`](https://github.com/niivue/mono/commit/f2a0e3f5ab5b630306cd6f07550b33e070dc8eb1)
now sends a data-only header snapshot and reconstructs the header instance on
the main thread. It includes unit and browser tests.

This fix is present at the audited `main` revision. NeuroCade’s pinned
`1.0.0-rc.10` predates it, so the right action is to upgrade when a suitable
release is available.

## Findings that belong in NeuroCade, not an upstream PR

| Encountered behavior | Classification and reason |
| --- | --- |
| Surface and volume axes were swapped or the cortical surface disagreed with 3D indicators | NeuroCade surface coordinate/import conversion |
| Surface parcellation looked identical to solid mode | NeuroCade companion-layer loading and display synchronization |
| Surface outline changed while using an “in-plane” keyboard action | NeuroCade’s old key-to-voxel-axis mapping moved through the slice for some planes; upstream `crosscutMM` masks in-plane coordinates correctly |
| Window sliders were capped at 255 for MGZ label values above 255 | NeuroCade UI used a fixed range instead of loaded-volume bounds |
| Window sliders oscillated after release | NeuroCade React-to-NiiVue event feedback loop |
| Showing a hidden volume caused a reload | NeuroCade removed hidden layers instead of retaining and toggling loaded state |
| Case-open loading, byte caching, batching, and MGZ preparation performance | NeuroCade loading/reconciliation architecture; the separate NiiVue worker fallback itself is fixed by NV-8 |
| The complete 2×2 grid looked square | NeuroCade set `isEqualSize: true`; using `false` preserves anatomical plane aspect ratios |
| The 3D tile inside the built-in grid is square | Intentional current layout behavior; NiiVue exposes `customLayout` when an application needs a rectangular render tile |
| Manual FreeSurfer-style labeling workflow | NeuroCade product/UI work built on NiiVue drawing APIs |
| Curvature colors did not match the requested Freeview-like green-to-red scale | NeuroCade colormap choice |

## Suggested upstream order

If we later decide to contribute, the cleanest sequence is:

1. NV-1 overlay source-over compositing, with pure unit tests.
2. NV-2 crosshair draw order, kept backend-symmetric.
3. NV-3 four-sided orientation labels with a shared label helper.
4. Coordinate with issue #68 on NV-4 before writing the zoom fix.
5. Discuss the public API for NV-5 before implementation.
6. Treat NV-6 as a separate, lower-priority interaction enhancement.

Each should remain a focused PR. NV-7 should be left to existing PR #74, and
NV-8 only requires consuming a newer NiiVue release.
