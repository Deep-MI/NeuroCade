# PACS research imports

Workflow validation failures now carry stable `error_code` values in durable run
results and run-summary responses. Preflight input failures return HTTP 422 with
`detail.code` and `detail.message`. The UI selects corrective guidance by code;
older stored PACS failures retain guidance through an exact legacy-message mapping
on the backend. No additional database migration is required for these codes.
Import-series error codes are translated to actionable messages in the UI.

Case polling invalidates in-flight responses on navigation/unmount and avoids
overlapping requests within each polling stream. Log/output publication also checks
the workspace action generation so older case data cannot enter the current viewer.

For an isolated archive populated with six public MRI cases, see
[Local PACS QA setup](pacs-qa.md).

PACS imports are disabled by default. Configure the `PACS_*` settings in
`.env.example`, providing HTTPS QIDO/WADO and OAuth client-credentials endpoints,
a stable source ID, and an explicit comma-separated workspace-ID allowlist.
Use an upstream account restricted to records appropriate for the research team.
Every member of an enabled workspace can search and import. Credentials stay on
the backend. The application container needs network access to PACS and the token
endpoint; the existing host bridge executes dcm2niix with networking disabled.

The workspace toolbar exposes **Import from PACS**. Search with an exact patient
ID/accession or a date interval of at most 31 days. Select one study, review its
series, and import compatible series as one case. MR, CT and PET image SOP classes
are candidates; successful conversion depends on the scanner data. Unsupported
objects remain visible. Re-import requires confirmation. Recent imports show
partial success, errors, cancellation and retry controls.

## Implementation

The adapter uses requests and python-multipart to impose byte limits before
decoding metadata or buffering DICOM. Pydicom inspects staged headers. No pixel
decoding occurs in the adapter. Import records contain separate protected
identity and per-series manifests in SQLite; case titles are generated neutral
identifiers. Clinical provenance is exposed only by authenticated PACS endpoints.
The application remains a single-process monolith with one dedicated PACS worker.

Cancellation stays nonterminal until the worker finishes cleanup. Every retry has
an attempt ID; stale attempts cannot claim a new job. Conversion uses the shared
host-bridge service. Only explicitly validated NIfTI and sanitized/numeric paired
sidecars are published from a separate staging directory; unexpected converter
files are discarded. Full voxel payloads are read in bounded planes before
publication. Recovery reconciles unfinished output directories in terminal as
well as active imports and refuses startup if cleanup still fails.

Workflow compatibility uses database artifact provenance and file SHA-256, not
editable marker files. Exact copies therefore retain modality/sequence checks;
dimensions are read from the actual image. T1 workflows require explicit native,
noncontrast structural-T1 verification by the importing user. Names alone never
establish suitability. Known incompatible descriptions and contrast-agent headers
reject that verification; the reviewer ID is retained in provenance. Unverified
series can still be imported and viewed, but cannot run T1 workflows.
Legacy imports without verified fingerprints must be re-imported before analysis.

Temporary instances live beneath `HOST_DATA_DIR/.tmp/pacs-imports`. Each series is
converted separately. NIfTI and allowlisted acquisition JSON plus diffusion
gradient sidecars are promoted together. Completed series survive failed sibling
series. Retry downloads unfinished series again; interrupted transfers are not
byte-resumed. Queued durable jobs recover on startup. Interrupted work requires
explicit retry. Never change the source-ID meaning to point at a different archive.

## Validation before use with hospital data

Run `pytest tests/test_pacs.py` and the existing upload/runtime regression tests.
After `npm run build` in `client/`, run `pytest tests/test_pacs_browser.py` for a
Chromium acceptance test using a loopback server and synthetic API responses.
Validate the target PACS token authentication, pagination, supported SOP classes,
multiframe/transfer syntax conversion, identity consistency and geometry against
representative staging studies. The unit suite does not certify a PACS vendor.
No production endpoint or hospital data is needed by automated tests.

Source-file removal does not de-identify the resulting images. Protect the data
directory and its backups as clinical data. PACS remains the source of truth;
NeuroCade does not write back or continuously synchronize a study.
