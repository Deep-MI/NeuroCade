# Local PACS for QA

This is an isolated **Orthanc + DICOMweb** archive, not a production PACS. It has
its own Docker container (`neurocade-qa-pacs`) and persistent Docker volume
(`neurocade-qa-pacs-data`). No changes to NeuroCade's single-process architecture,
database, cases, or runtime bridge are required. No Docker Compose is used.

## Data and attribution

The fixture downloads **six distinct ReMIND patients**, one complete MRI series
per patient, from the public NCI Imaging Data Commons `idc-open-data` S3 mirror.
These are six real dataset cases, not duplicated synthetic scans. They are
**subsets of the patients' examinations**, not complete clinical studies. The
selection favors compact series, not a particular sequence: inspect the series
description before selecting an analysis workflow. Not every series is T1.

ReMIND publishes de-identified, defaced brain imaging. Its MRI DICOM was produced
from NRRD, so this fixture does not test every native scanner's private tags,
transfer syntaxes, diffusion metadata, or enhanced multiframe behavior. Only
public sample data belongs here. Do not upload clinical patient data.

- Juvekar, P., et al. (2023). *The Brain Resection Multimodal Imaging Database
  (ReMIND), Version 1*. The Cancer Imaging Archive.
  [Dataset DOI](https://doi.org/10.7937/3RAG-D070).
- Juvekar, P., et al. (2023). *ReMIND: The Brain Resection Multimodal Imaging
  Database*. [Publication DOI](https://doi.org/10.1101/2023.09.14.23295596).
- Clark, K., et al. (2013). *The Cancer Imaging Archive (TCIA): Maintaining and
  Operating a Public Information Repository*. Journal of Digital Imaging, 26,
  1045–1057. [DOI](https://doi.org/10.1007/s10278-013-9622-7).
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  [TCIA collection and usage information](https://www.cancerimagingarchive.net/collection/remind/).
- Series manifest: FNNDSC `sample_dicom_downloader`, pinned to revision
  `8dc029f833331cc1238695a796a3058dfe9fa76c`. The old GCS mirror in that manifest
  is replaced with the current public S3 mirror. Image bytes are unchanged.

The generated `.runtime/pacs-qa/inventory.json` records original patient/study/
series IDs, descriptions, counts, original object keys, and SHA-256 checksums.
Keep this attribution with any copies. Downloads, secrets, and the inventory
are ignored by Git.

## Start and populate

Prerequisites: the project's `.venv` with locked test dependencies, Docker running,
OpenSSL with `req -addext`, and free local ports 8042 and 8443. Allow roughly 2 GB
for the image and sample data (exact size depends on the selected series).

From the repository root:

```sh
.venv/bin/python scripts/pacs_qa.py setup
.venv/bin/python scripts/pacs_qa.py seed
.venv/bin/python scripts/pacs_qa.py start-gateway
.venv/bin/python scripts/pacs_qa.py export-env
.venv/bin/python scripts/pacs_qa.py verify
.venv/bin/python scripts/pacs_qa.py status
```

Setup pins the downloaded Orthanc image digest in `.runtime/pacs-qa/image.txt`.
Seeding is resumable and re-uploading an existing SOP instance is idempotent.
The fixture never deletes the archive or application data.

## Open the archive

Verified fixture inventory (2026-09-09):

| Exact patient ID | MRI series | DICOM images |
| --- | --- | ---: |
| ReMIND-001 | 3D_SAG_T2_SPACE | 192 |
| ReMIND-002 | 3D_AX_T1_postcontrast | 176 |
| ReMIND-003 | 3D_AX_T1_postcontrast | 168 |
| ReMIND-004 | 3D_AX_T2_SPACE | 192 |
| ReMIND-005 | 3D_AX_T2_SPACE | 176 |
| ReMIND-006 | 3D_SAG_T2_FLAIR | 176 |

Total: six patients, six studies, six series, 1,080 instances. Live adapter search,
paginated instance counts, and one WADO retrieval per case passed for all six.
The running Docker application could also reach the gateway with verified TLS.

Open **http://127.0.0.1:8042** in your browser. Sign in with the `username` and
`password` from `.runtime/pacs-qa/credentials.json`. These are randomly generated
local QA credentials, separate from OAuth client credentials. Expand a patient,
study, and series to inspect the loaded files.

Both published endpoints bind to loopback, not the LAN. Orthanc's HTTP admin
interface is for local QA only. There is no exposed DICOM TCP listener. The test
gateway at **https://localhost:8443/dicom-web** permits authenticated GET only.
Its `/token` endpoint implements the client-credentials subset used by NeuroCade
and issues five-minute tokens. It is not a production identity provider.

## Connect NeuroCade

Generated `.runtime/pacs-qa/neurocade.env` contains the connection settings without
printing secrets to the terminal. It starts with `PACS_ENABLED=false` and an empty
workspace allowlist deliberately. Merge only its PACS settings into your chosen
QA application configuration, set `PACS_WORKSPACE_IDS` to the exact QA workspace
ID, and set `PACS_ENABLED=true`. Clear any old QIDO/WADO prefixes.

For a host-run backend, the generated URLs and certificate path work directly.
For the existing Docker launcher on macOS:

1. Replace `localhost` in both endpoint URLs with `host.docker.internal`.
2. Copy **only** `certificate.pem` into `$HOST_DATA_DIR/pacs-qa/certificate.pem`
   and set `PACS_CA_BUNDLE=/data/pacs-qa/certificate.pem`. Do not mount the private
   key or Orthanc configuration into NeuroCade.
3. Rebuild the application image if it predates the PACS implementation, then
   restart using the normal launcher after saving other work.

The certificate includes localhost, 127.0.0.1, and host.docker.internal and expires
after one year. Keep TLS verification enabled; there is no insecure test bypass.
Linux Docker host networking differs; loopback-only host services may need a
separately designed private network. Do not expose this fixture on `0.0.0.0` as
a shortcut.

The setup does **not** silently edit `.env`, enable workspaces, rebuild/restart the
running application, or import research copies into existing cases.

For the explicitly requested QA deployment, `configure-app` creates/reuses the
dedicated `pacs-qa` workspace and writes a protected launcher overlay at
`.runtime/pacs-qa/neurocade-app.env`. It preserves the original `.env`. This action
requires the current local-auth app to be running, and enables PACS only for that
QA workspace. It copies only the public certificate into the existing data mount.
Build and restart this opt-in configuration with:

```sh
.venv/bin/python scripts/pacs_qa.py configure-app
ENV_FILE="$PWD/.runtime/pacs-qa/neurocade-app.env" ./scripts/run.sh build
ENV_FILE="$PWD/.runtime/pacs-qa/neurocade-app.env" ./scripts/run.sh stop
ENV_FILE="$PWD/.runtime/pacs-qa/neurocade-app.env" ./scripts/run.sh start -d
```

Use that same overlay when restarting the QA-enabled app later. The six fixture
series are T2/FLAIR or explicitly postcontrast T1, so the conservative native-T1
analysis policy rejects them; importing and viewing them remains supported.

## QA checklist

`verify` uses the actual NeuroCade PACS adapter to check all six patient searches,
complete paginated instance counts, and one WADO retrieval per series over verified
TLS/OAuth. It also checks rejection of unauthenticated discovery and records
`.runtime/pacs-qa/verification.json`. It does not perform conversion or an analysis.

After connecting the UI:

1. Open **Import from PACS** and search an exact patient ID from the inventory.
2. Select the series, import, and check the resulting volume and provenance.
3. Repeat the import: verify duplicate confirmation before a separate case appears.
4. Cancel a second import while running; confirm raw staging data is removed.
5. Stop the QA archive during retrieval to exercise failure and retry; restart it
   before retrying. Never stop the main NeuroCade container for this scenario.

### Completed QA (2026-09-09)

All six samples completed retrieval and conversion through the running application
and its host runtime bridge. Samples 001–002 were exercised interactively;
003–006 were checked through the live application API. During sample 003 import,
the dummy Orthanc archive was stopped: the attempt failed, then the same import
completed after restarting Orthanc and retrying. The initial seven research copies
(including the earlier confirmed duplicate) were confined to the `pacs-qa` workspace.
After completion, raw import staging was empty and no background jobs remained.
The source archive and downloaded fixture data intentionally remain available.

The analysis UI now preserves the backend failure reason, including after reload,
and provides native-T1 selection guidance for incompatible PACS inputs. Browser
regression coverage checks this alongside duplicate confirmation. These samples
are not suitable for validating successful native-T1 analysis.

Six single-series MRI cases do not cover mixed-modality eligibility or partial
multi-series success; those still have synthetic automated coverage. Representative
vendor interoperability needs separate validation.

## Repeatable live acceptance checks

From the repository root, with the local QA-enabled app and dummy PACS running:

```sh
.venv/bin/python -m scripts.pacs_qa_acceptance --workspace-id <pacs-qa-workspace-id>
.venv/bin/python -m scripts.pacs_qa_acceptance --workspace-id <pacs-qa-workspace-id> --execute --outage
```

The first command performs read-only preflight. The second explicitly creates
six additional research copies, checks full retrieval/conversion and raw staging
cleanup, and stops/restarts only the labelled dummy Orthanc archive during the
first import. Do not run concurrently with other QA users or jobs. The archive is
restored in a `finally` block before retrying. An outage that misses retrieval is
reported as a failure, not a pass. A timeout leaves the import available for
inspection; no cases, volumes, or source data are deleted by this runner.
Per-run reports with import/case IDs are saved under `.runtime/pacs-qa/acceptance-*.json`.
This currently targets the local Docker QA profile; it does not restart or rebuild
the application. `tests/test_pacs_acceptance.py` tests its safety gates offline.

The maintained runner passed a complete six-sample run on 2026-09-09, including
retrieval failure during an archive outage, staging cleanup before retry,
successful retry, six completed conversions, and final publication/cleanup checks.
That run added six more copies to `pacs-qa`; it did not alter personal workspaces.

Before production approval, separately validate a verified native noncontrast T1
through a successful analysis, representative scanner/vendor exports and transfer
syntaxes, and a live mixed-series import with partial success. Record dataset
permission, expected results, converter/runtime versions, and reviewer acceptance.
The six ReMIND series are not substitutes for these outstanding checks.

## Lifecycle

Orthanc restarts with Docker. The host HTTPS gateway survives the invoking shell
but must be started again after a host reboot. Its PID and logs are under
`.runtime/pacs-qa/`. Use `start-gateway` to check/start it again. For foreground
operation use `serve` instead, and stop with Ctrl-C.

To stop Orthanc without losing samples: `docker stop neurocade-qa-pacs`.
Restart it with `setup`. To stop the background gateway, inspect the PID in
`gateway.pid` with `ps -p <PID> -o command=` and send SIGTERM only if it is the
`scripts/pacs_qa.py serve` process. Neither stop operation deletes the data.
