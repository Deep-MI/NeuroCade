# NeuroCade Monolith Refactor Plan

**Status:** Decisions locked (§2) — ready to phase
**Motivation:** The current Docker Compose stack runs **six** services (gateway, api-service, api-worker, runtime-runner, postgres, redis) plus an update-checker. For the **desktop** use case this is enormous overkill — it should collapse to **one process**. But the same codebase must also keep serving the **shared/web (multi-user)** use case. So the refactor is not "monolith replaces everything"; it is **one codebase, two deployment profiles** (see §2.1), with the heavy machinery removed from the desktop path and slimmed (not deleted) on the shared path. No HTTP endpoint, database model, auth flow, or LLM integration changes.

**Tools run via Apptainer.** The monolith launches analysis tools (FastSurfer, dcm2niix, the bash image, etc.) through **Apptainer**. Tool images are sourced the way NeuroCade did originally: **download a prebuilt, arch-matched SIF** from object storage (sha256-verified), with **`apptainer pull docker://…` as a fallback** when no prebuilt SIF exists for the host arch. This restores the `containers.py` SIF-download + architecture-check machinery the `tool_registration` branch removed (it was dropped when that branch migrated to Docker), rather than re-pulling/converting multi-GB Docker images on every host. The **app itself** is deployed either **natively** or **as a Docker container** — both launch the tools via Apptainer (see §2.3). A Docker *tool* backend is kept only as an optional, native-only dev convenience; it is not a supported deployment profile.

---

## 1. Current architecture (baseline)

| Service | Role | Why it exists today |
|---|---|---|
| `gateway` (nginx) | Serves built frontend + reverse-proxies `/api/app` → api-service | Single origin for client |
| `api-service` (uvicorn) | FastAPI HTTP API, 1 process | Business logic |
| `api-worker` (celery) | Runs 3 long tasks off queues `api`, `fastsurfer`, `workspace_batch` | Keep API responsive during hour-long jobs |
| `runtime-runner` (uvicorn) | Holds the Docker socket; turns `ContainerRunPayload` → `docker run` | API containers can't reach the Docker socket |
| `postgres` | Database | Persistence + row-level locks |
| `redis` | Celery broker/result backend + 1 health check | Task transport |
| `update-checker` | Periodic version-check script | Background cron |

**Key constraint that shapes everything:** `runtime-runner` exists *only* because containerized services cannot access `/var/run/docker.sock`. The monolith launches tools via **Apptainer** instead, which needs no daemon socket — so the sidecar disappears in both deployment modes. When the app runs **natively**, it calls `apptainer exec` directly. When the app runs **as a container**, it calls `apptainer exec` *inside* the container (a proven pattern — see §2.3), so there is still no socket mount and one fewer service than today because the JobWorker is in-process.

**Celery touchpoints to migrate (precise):**
- App def: [`celery_app.py:11`](api-service/api_service/celery_app.py) — broker/backend = `settings.redis_url`.
- 3 tasks: `run_fastsurfer_task` ([`runtime/fastsurfer_tasks.py:101`](api-service/api_service/runtime/fastsurfer_tasks.py)), `execute_workspace_batch_case_task` + `execute_workspace_command_task` ([`workspace_batch/tasks.py:7,33`](api-service/api_service/workspace_batch/tasks.py)).
- Submit: `apply_async` at [`runtime/execution.py:221`](api-service/api_service/runtime/execution.py) (`submit_runtime_request`).
- Poll: `AsyncResult` at [`runtime/service.py:259-268`](api-service/api_service/runtime/service.py) (`fetch_task_status`).
- Queue status: `inspect()` at [`runtime/service.py:270-281`](api-service/api_service/runtime/service.py).
- Cancel: `control.revoke` at [`runtime/service.py:283-297`](api-service/api_service/runtime/service.py).

**Redis beyond Celery:** only a connectivity probe in [`routers/monitoring.py`](api-service/api_service/routers/monitoring.py). Removable.

**Postgres-specific usage:**
- Row-level locks in [`backend_common/concurrency.py`](backend_common/concurrency.py) — already no-op on SQLite.
- `pg_advisory_xact_lock` in [`backend_common/auth.py`](backend_common/auth.py) bootstrap — needs a SQLite path.
- Partial indexes ([`backend_common/db.py:117-128,167-172`](backend_common/db.py)) — already carry `sqlite_where` variants.

---

## 2. Target architecture & key decisions

### 2.1 Deployment profiles (one codebase, two configs)

The **same app process** is deployed two ways. The difference is purely configuration (auth mode, exposure) — not separate code paths. Either profile can run natively or as a Docker container (§2.3); tools always run via Apptainer.

| Concern | **Desktop profile** | **Shared / web profile** |
|---|---|---|
| Use case | Single user, Electron, local | Multi-user, browser, hosted |
| App process | 1 (native or 1 container) | 1 (native or 1 container) |
| Database | **SQLite (WAL)** | **SQLite (WAL)** — same |
| Auth | Local auth | Clerk |
| Frontend serving | App `StaticFiles` | App `StaticFiles` |
| Background jobs | In-process JobWorker | In-process JobWorker (single app node) |
| Tool runtime | **Apptainer** (§2.3) | **Apptainer** (§2.3) |
| Exposure | Bound to localhost, Electron | Exposed port (operator's own reverse proxy/TLS if any) |
| Services total | **1** (vs 6 today) | **1** (vs 6 today) |

Both profiles drop Redis, Celery, the runtime-runner sidecar, Postgres, the gateway, and the multi-service orchestration. The shared profile differs from desktop only in **auth mode** (Clerk), being **exposed on a port** rather than localhost-only, and that it's not Electron-launched. DB and auth are already configuration-driven today, so one codebase covers both.

**Gateway dropped.** The app serves its own frontend via `StaticFiles` (single origin, no CORS), so nginx is unnecessary for desktop. For shared/web, TLS termination and host allow-listing are delegated to whatever reverse proxy the operator already runs in front of the exposed port — we don't ship one. This removes a service and a Dockerfile rather than maintaining an "optional" one.

> **Single-node assumption (the boundary that makes SQLite sufficient):** both profiles run **one app process** per deployment — concurrency comes from async + the in-process worker thread, not from `uvicorn --workers N` or multiple hosts. SQLite/WAL comfortably serves this: concurrent readers + one serialized writer, low DB write volume (heavy work runs in containers, not the DB), and invariants enforced by partial unique indexes rather than row locks. The moment a deployment needs **multiple app processes/hosts** sharing state, SQLite no longer fits — that is a separate "cluster profile" that re-introduces Postgres **and** a broker + worker fleet, explicitly out of scope here and flagged in §7.

### 2.2 Target shape

A single Python process, runnable natively **or** as one Docker container, that:
1. Serves the API **and** the built frontend from one uvicorn (FastAPI `StaticFiles`).
2. Runs background jobs in an **in-process worker thread** backed by the existing `Run` table.
3. Launches tools through an in-process **`ApptainerBackend`** (`apptainer exec`), running prebuilt arch-matched SIFs (downloaded from object storage) with a `docker://` pull fallback.
4. Persists to **SQLite (WAL)**.

```
  Electron main process (desktop)  /  host service manager (shared)
        │ spawns + health-checks  (native binary OR `docker run` the single image)
        ▼
  ┌────────────────────────────────────────────────────────┐
  │  neurocade (single uvicorn process)                     │
  │   ├─ FastAPI routers (unchanged endpoints)              │
  │   ├─ StaticFiles  → built client assets                 │
  │   ├─ JobWorker thread → polls Run table                 │
  │   │      └─ ApptainerBackend.run(RuntimeContainerRunRequest)
  │   │             └─ apptainer exec (prebuilt SIF / docker:// fallback)  │ ──► tool containers
  │   └─ SQLite (WAL)  via SQLAlchemy                        │     (fastsurfer, dcm2niix, bash)
  └────────────────────────────────────────────────────────┘
   native: apptainer on host PATH
   container: apptainer inside the image (privileged container, see §2.3)
```

### 2.3 App deployment: native or Docker — both launch Apptainer

Two supported deployment configs, both ending in Apptainer. Neither nests forbidden runtimes (no apptainer-in-apptainer, no docker-in-docker):

| App deployment | Tool runtime | Requirements / cost |
|---|---|---|
| **Native host** | `apptainer exec` | `apptainer` on host PATH. No daemon, no socket, no elevation. Clean. |
| **Docker container** | `apptainer exec` *inside* the container | Container must run **`--privileged`** (or the unprivileged-Apptainer trio: `--security-opt seccomp=unconfined --security-opt systempaths=unconfined --device /dev/fuse`), plus `--gpus all` for GPU. Apptainer is baked into the image. |

**Precedent:** this is exactly how Neurodesk's `neurodesktop` runs — `docker run --privileged --user=root … --gpus all vnmd/neurodesktop`, with Apptainer/Singularity launching the tool containers inside. The Apptainer project documents the less-privileged alternative (`seccomp=unconfined` enables `unshare`/user-namespaces, `systempaths=unconfined` enables the PID namespace, `/dev/fuse` enables unprivileged FUSE/SIF mounts). GPU works via `--gpus all` on the outer container plus `apptainer --nv` inside.

**Tradeoff to keep visible:** the Docker deployment requires a privileged (or heavily-capability-relaxed) container, which is a real security step up and is **banned on many locked-down/cluster hosts**. The native deployment has none of that cost. So **native is the recommended default**; the Docker deployment exists for hosts that prefer container packaging and can grant the privilege. This is documented, not engineered around.

### Locked decisions

**D1 — App deployment: native OR Docker container; tools always via Apptainer.** *Locked.*
- **Native** (recommended default): `apptainer exec` directly on the host. No sidecar, no socket, no elevation. Matches the historical `scripts/install.sh` model.
- **Docker container**: one image with Apptainer baked in, run `--privileged` (à la Neurodesk) so it can `apptainer exec` internally. No Docker socket mount, no docker-in-docker.

A Docker *tool* backend (`docker run` from a native app) is kept only as an optional native-only dev convenience behind `NEUROCADE_RUNTIME_BACKEND=docker`; it is not a deployment profile.

**D2 — SQLite/WAL everywhere; Postgres dropped.** *Locked.*
One database for both profiles. Postgres's distinguishing value is multi-node/multi-process shared access, which is out of scope (single-node assumption, §2.1) — so it's removed, not retained. This *deletes* code: the dual-dialect test matrix collapses, and the Postgres-only branches go. **Primary technical risk of the whole refactor:** the API request threads and the JobWorker thread write concurrently. Mitigation: WAL mode, `busy_timeout`, `check_same_thread=False`, short write transactions, JobWorker as the principal writer of run state, and enforcing invariants (e.g. one active run per case) via the existing **partial unique indexes** rather than row locks — catch `IntegrityError` on the racing write. Prototype the workspace-batch write path first to validate this holds under concurrent upload + run-status updates.

**D3 — DB-backed job queue on the existing `Run` table.** *Locked.*
Statuses `queued/running/completed/failed/canceled` already exist; a background worker thread (small `ThreadPoolExecutor` keyed by queue for `API_WORKER_CONCURRENCY` parallelism) claims and runs jobs. Crash-durable, unlike an in-memory `queue.Queue`. On restart, reconcile rows stuck in `running` (requeue or mark failed).

**D-bootstrap — auth advisory lock.** Replace `pg_advisory_xact_lock` ([`backend_common/auth.py`](backend_common/auth.py)) with a module-level `threading.Lock` (single process, bootstrap runs once at startup). The Postgres advisory path can be deleted outright. The row-level locks in [`concurrency.py`](backend_common/concurrency.py) already no-op without Postgres; rely on partial unique indexes for the invariants they guarded, and optionally delete the now-dead lock helpers.

---

## 3. Component-by-component migration

### 3.1 Celery → in-process JobWorker
- **New module** `api_service/jobs/worker.py`: a `JobWorker` started in the FastAPI lifespan ([`main.py:15-24`](api-service/api_service/main.py)). It loops: claim the oldest `queued` Run (atomic `UPDATE ... WHERE status='queued'` → `running`), dispatch to the matching handler, capture result/exception, mark terminal state, run completion hooks.
- **Map the 3 tasks to plain functions.** The task bodies already do the real work; strip the `@celery_app.task` decorator and the `self`/`bind` plumbing. Register them in a `dict[str, Callable]` keyed by the current task name.
- **Replace submission.** `submit_runtime_request` ([`execution.py:194-229`](api-service/api_service/runtime/execution.py)): instead of `celery_task.apply_async`, insert/enqueue a job row and return its id as `task_id`. Keep the function signature so callers (`runtime/service.py:212`, `workspace_batch/service.py`) are untouched.
- **Replace polling.** `fetch_task_status` ([`service.py:259`](api-service/api_service/runtime/service.py)) → read job/Run status instead of `AsyncResult`.
- **Replace queue status.** `fetch_queue_status` ([`service.py:270`](api-service/api_service/runtime/service.py)) → `SELECT count(*) ... GROUP BY status` on `Run` (active=running, queued=queued).
- **Replace cancel.** `cancel` ([`service.py:283`](api-service/api_service/runtime/service.py)) → for a **queued** job, set a cancel flag on the row so the worker skips it; for a **running** job, the JobWorker already owns the `apptainer exec` subprocess handle (`subprocess.Popen`), so terminate the process group (`proc.terminate()` → `kill` after grace). No instance tracking needed — this is simpler than Celery's `revoke` because execution is in-process and foreground. (`apptainer exec` is not an `apptainer instance`, so `instance stop` does not apply.)
- **Concurrency:** preserve per-queue parallelism with a bounded pool. **The GPU/FastSurfer queue should be limited to concurrency 1** (or a separate `FASTSURFER_CONCURRENCY`) to avoid GPU contention/OOM, independent of `API_WORKER_CONCURRENCY` (default 2) for the api/workspace queues. FastSurfer is CPU/GPU-bound and long — must run in worker threads, never on the asyncio event loop.

### 3.2 Redis → removed
- Delete broker/backend usage; remove the redis probe from [`routers/monitoring.py`](api-service/api_service/routers/monitoring.py) (or make it report "n/a"). Drop `redis`/`celery` from dependencies once §3.1 lands.

### 3.3 runtime-runner → in-process Apptainer backend

This replaces the HTTP sidecar with an in-process executor and restores Apptainer as the tool runtime.

**Abstraction.** Keep the existing backend-agnostic `RuntimeContainerRunRequest` (image, command, binds, env, cwd, `network_disabled`, `gpu_enabled`) in [`neurocade_runtime_tools/execution.py`](packages/neurocade-runtime-tools/src/neurocade_runtime_tools/execution.py). Add a `RuntimeBackend` protocol with `build_argv(request) -> list[str]` and `run(request) -> RuntimeExecutionResult`. The supported implementation is:
- **`ApptainerBackend`** — a clean `apptainer exec` builder (the deleted `apptainer_command.py` is the reference): `apptainer exec [--nv] --cleanenv --no-home [--bind src:dst[:ro]] [--pwd …] [--env …] <image.sif> <cmd>`. **Image source: a prebuilt, arch-matched SIF** resolved/downloaded by the restored `containers.py` (default), falling back to `apptainer pull docker://…` from `CORE_SPECS.docker_uri` when no prebuilt SIF exists for the host arch.
- **`DockerBackend`** *(optional, native-only dev convenience)* — inline `runtime_runner.py`'s `_docker_command` builder. Selected only via `NEUROCADE_RUNTIME_BACKEND=docker` on a native host; never used in a deployment profile.

**Backend selection.** Default `apptainer`. `NEUROCADE_RUNTIME_BACKEND=docker|apptainer` overrides (docker = native-only dev). FreeSurfer-license bind/env and GPU flags are expressed once on the request and translated per-backend.

**Image sourcing & cache.** Restore the `containers.py` resolution path: each spec carries an arch-keyed `fileshare_url` (+ `fileshare_sha256`) to a **prebuilt SIF** on object storage; `resolve_core_image` downloads the SIF matching the host arch (`_apptainer_guest_arch` / `_image_build_arch`, amd64 vs arm64) and verifies the checksum. When no prebuilt SIF exists for the host arch, fall back to `apptainer pull docker://…` (from the spec's `docker_uri`). **SIFs are architecture-specific** — ship one per supported arch (amd64 for GPU/server, arm64 for Apple-Silicon CPU); the `docker://` fallback covers arches you didn't prebuild. The **bash image** ships as a prebuilt SIF too (replacing the `neurocade-runtime-bash:local` local Docker tag at `container_specs.py:87`). **Build/publish pipeline (new CI task):** FastSurfer/dcm2niix SIFs come from the neurocontainers/object-storage that already exists, but the **custom bash image** must be built and published by NeuroCade — keep `docker/runtime-bash.Dockerfile` as the build input, then per arch convert to SIF (`apptainer build … docker-daemon://…` or `apptainer pull`) and upload to the `fileshare_url` location, recording the sha256. This per-arch build+publish step (amd64 + arm64) is a prerequisite for the bash tool on any host without the `docker://` fallback. Downloaded SIFs and any `APPTAINER_CACHEDIR` (fallback-pull conversions) must live on a **persistent volume** — multi-GB images, and for the Docker deployment a non-persistent location re-downloads on every restart. Surface first-run download/convert latency in `BackendStartupGate`. (macOS dev: the deleted `containers.py` Lima build path can be restored if needed.)

**Security validation (kept simple).** Revive only the cheap rootless/no-elevation checks the branch deleted from `execution.py` (`assert_rootless_apptainer_execution`): reject `--fakeroot/--writable*` and assert the `gpu_enabled` policy matches `--nv`. **Network isolation (`--net --network none`) is deferred** — it requires a setuid-installed Apptainer in rootless mode, which adds install complexity; it is not a priority now (§7 Q7). Without it Apptainer shares the host network namespace, accepted for now as a future hardening item. The `RuntimeExecutionPolicy` already carries `network_disabled`/`gpu_enabled`; set its `runtime` field to the active backend. (When the *app* runs in a privileged Docker container per §2.3, the privilege is on the **outer** container; the inner `apptainer exec` is still validated no-fakeroot/no-writable.)

**Path handling.** The app knows real host paths, so the `/data`↔host remap in `runtime_runner.py:_remap_host_path` collapses to identity for the native deployment. For the Docker deployment, Apptainer binds resolve *inside* the app container, so paths are the in-container paths under the mounted data volume — no host-daemon remap is needed (unlike the old `docker run` socket model). Keep the allowed-bind-roots validation in both cases.

**Transport.** Drop `RUNTIME_RUNNER_URL`/`RUNTIME_RUNNER_TOKEN` and the `host-runtime-runner` execution mode; `execute_runtime_request` calls the Apptainer backend directly.

### 3.4 gateway/nginx → dropped; in-app StaticFiles
- **Both profiles:** mount the built client (`client/dist`) via `StaticFiles` in `main.py` with an SPA fallback to `index.html`. Single origin → no CORS. (The deleted `scripts/serve_static_client.py` previously did this in-process — capability re-introduced.)
- **Gateway deleted entirely.** Desktop binds to localhost behind Electron. Shared/web exposes the app's port directly; TLS termination and host allow-listing (`APP_ALLOWED_HOSTS` is still honored in-app) are delegated to whatever reverse proxy the operator already runs. We ship no nginx.
- Frontend build step stays; only where it's *served from* changes.

### 3.5 update-checker → in-process scheduled task
- Replace the dedicated service with a lightweight scheduled coroutine/thread (sleep-loop or APScheduler) started in lifespan, running [`scripts/update_checker.py`](scripts/update_checker.py)'s logic.

### 3.6 Electron startup → spawn one process (desktop profile)
- [`client/electron/main.mjs`](client/electron/main.mjs): replace `./scripts/compose/up.sh` (line ~269) with spawning the monolith (`uv run uvicorn api_service.main:app ...`, or `docker run --privileged … ` the single image), keep the existing health-check loop against `/api/app/healthz`, and replace `down.sh` on quit (line ~278) with terminating the child process.
- [`BackendStartupGate.tsx`](client/src/components/BackendStartupGate.tsx): update the recovery instructions (lines ~85-87) away from multi-service Compose commands; surface a clear message if `apptainer` is missing (native) or the container lacks privilege (Docker).
- The **shared/web profile** is not launched by Electron — it's brought up by the host's own service manager (native `uvicorn`, or `docker run --privileged` the single image). Same app entrypoint, different config (Clerk auth, exposed port).

---

## 4. Phased implementation

Each phase should be independently testable and leave the app runnable.

1. **Phase 0 — Safety net.** Capture current behavior in tests: a job submit→poll→complete flow and a dcm2niix conversion. These become the regression oracle.
2. **Phase 1 — `RuntimeBackend` abstraction + Apptainer backend (§3.3).** Add in-process execution to `execution.py` behind the `RuntimeBackend` protocol; implement `ApptainerBackend` + restored (simplified) security validation; **restore the `containers.py` prebuilt-SIF download + arch-check path** (default) with `apptainer pull docker://` fallback; make `runtime-runner` optional. Verify FastSurfer + dcm2niix run via `apptainer exec` from prebuilt SIFs (and the `docker://` fallback). (Optional `DockerBackend` for native dev.)
   > **Phasing note:** `apptainer exec` cannot run inside the existing **non-privileged** containerized Celery worker. So validate this phase by running the app/worker **natively** (`uv run …`) against the still-running Compose Postgres/Redis for now — i.e. some of the native-launch packaging (Phase 6) is effectively pulled forward as the dev harness for Phases 1–4. The Apptainer move is coupled to native (or privileged-container) execution; keep that in mind for the transition order.
3. **Phase 2 — JobWorker (§3.1).** Introduce the DB-backed worker behind a flag; run it *alongside* Celery first, then flip submission to it. Remove Celery once green.
4. **Phase 3 — Drop Redis (§3.2).**
5. **Phase 4 — SQLite/WAL everywhere, drop Postgres (§3.2/D2).** *Prototype the workspace-batch write path first.* Make SQLite/WAL the only DB (`check_same_thread=False`, WAL + `busy_timeout` pragmas); replace the advisory-lock bootstrap with `threading.Lock`; switch race-prone invariants to partial-unique-index + `IntegrityError` handling; delete the Postgres-only branches and the dual-dialect test matrix.
6. **Phase 5 — Static serving (§3.4)** + **update-checker (§3.5).** App serves `StaticFiles`; delete the gateway.
7. **Phase 6 — Profile packaging + Electron launch (§3.6, D1/§2.1).** Native launch (`uv run …`); a single-image build with Apptainer baked in, runnable `--privileged`; document the native vs Docker deployment per §2.3. Electron spawns the desktop config. Replace the 6-service orchestration.
8. **Phase 7 — Teardown.** Delete the 6-service orchestration, the **gateway** (`docker/gateway.Dockerfile` + `nginx.conf`), `runtime_runner.py`, `celery_app.py`, Celery wrappers, Redis/Celery deps, **and Postgres** (image, service, deps, pg-only code). **Keep** `docker/backend.Dockerfile` (now with Apptainer baked in) and a minimal single-service run recipe per profile. Prune dead `.env` keys (`RUNTIME_RUNNER_*`, `REDIS_*`, `POSTGRES_*`); add `NEUROCADE_RUNTIME_BACKEND` + `APPTAINER_CACHEDIR` and document the profile toggles; update `INSTALL.md`/`DOCKERIZATION.md` (incl. the `--privileged` requirement for the Docker deployment).

---

## 5. Risks & tradeoffs

| Risk | Mitigation |
|---|---|
| **Long container subprocess blocks event loop** (`apptainer exec`) | All tool runs happen in JobWorker threads, never in async handlers; use `run_in_threadpool` for any sync exec on the request path (dcm2niix upload). |
| **SQLite write concurrency** (worker thread + API threads + web users) | WAL, `busy_timeout`, short write txns, single-writer discipline (JobWorker is the main writer); invariants via partial unique indexes + `IntegrityError` handling, not row locks. Validate via the Phase-4 workspace-batch prototype. |
| **Crash loses in-flight jobs** | DB-backed queue + startup reconciliation of `running` rows. |
| **Single-node + multi-host scale-out both dropped** | One app process per deployment serves desktop + small shared use. Multiple app processes/hosts is an out-of-scope "cluster profile" (§7) that re-adds Postgres + a broker — flagged, not built. Keep this assumption explicit so nobody reaches for `uvicorn --workers N` against SQLite. |
| **Docker deployment needs a privileged container** (§2.3) | `apptainer exec` inside Docker requires `--privileged` (or `seccomp=unconfined` + `systempaths=unconfined` + `/dev/fuse`). Precedent: Neurodesk. Banned on some locked-down hosts → **native is the recommended default**; Docker deployment is documented as privilege-requiring, not the default. |
| **GPU passthrough** | Native: `apptainer exec --nv`. Docker deployment: `--gpus all` on the outer container + `--nv` inside. Verify each on a CUDA box. |
| **Job cancellation now cooperative** | JobWorker owns the `apptainer exec` `Popen` handle → `terminate()`/kill the process group on cancel (running jobs); cancel flag skips queued jobs. Simpler than Celery `revoke`; no instance tracking. |
| **Apptainer must be present** | Native: probe `apptainer --version` on host PATH at startup. Docker: baked into the image; probe the container can actually create namespaces (privilege check) and surface a clear error in `BackendStartupGate`. |
| **Apptainer rootless security regressions** | Restore the cheap `assert_rootless_apptainer_execution` checks (no `--fakeroot/--writable`, GPU-policy match) — see §3.3. **Network isolation deferred** (needs setuid Apptainer; not a priority now, §7 Q7). The inner `apptainer exec` stays no-fakeroot even when the outer app container is privileged. |
| **Prebuilt SIF is architecture-specific** | One SIF per arch (amd64 GPU/server, arm64 Apple-Silicon CPU); host-arch match enforced via restored `_apptainer_guest_arch`/`_image_build_arch`. `apptainer pull docker://` (multi-arch manifest) is the fallback for any arch not prebuilt. |
| **Prebuilt SIF download + cache** | Arch-matched SIF downloaded (sha256-verified) on first use to a **persistent volume**; multi-GB. For the Docker deployment the SIF store + `APPTAINER_CACHEDIR` must be a mounted volume or every restart re-downloads. Surface latency in `BackendStartupGate`. |

---

## 6. Files touched (summary)

**Add:** `api_service/jobs/worker.py`; `RuntimeBackend` protocol + `ApptainerBackend` (and optional native-only `DockerBackend`) and restored (simplified) Apptainer security validation (in `neurocade_runtime_tools/execution.py`, with an `apptainer_command.py`-style builder); startup runtime/health checks.
**Restore (revert the `tool_registration` deletion):** `neurocade_runtime_tools/containers.py` — the prebuilt-SIF download + arch-resolution (`resolve_core_image`, `_apptainer_guest_arch`, `_image_build_arch`, sha256-verified `fileshare_url` download, optional Lima build for macOS), trimmed of the old runtime-runner routing.
**Modify:** `runtime/execution.py`, `runtime/service.py`, `runtime/fastsurfer_tasks.py`, `workspace_batch/tasks.py` + `service.py`, `main.py` (lifespan + StaticFiles), `routers/monitoring.py`, `backend_common/db.py` (SQLite/WAL only), `backend_common/auth.py` (`threading.Lock` bootstrap), `client/electron/main.mjs`, `BackendStartupGate.tsx`, `container_specs.py` (per-arch `fileshare_url`/`fileshare_sha256` for every core spec incl. the bash image; keep `docker_uri` for the fallback), `docker/backend.Dockerfile` (bake in Apptainer), `.env.example` (+`NEUROCADE_RUNTIME_BACKEND`, `APPTAINER_CACHEDIR`, SIF store path), `INSTALL.md`, `DOCKERIZATION.md` (document native vs `--privileged` Docker deployment).
**Delete:** the **6-service** orchestration in `compose.yaml`, `scripts/compose/` multi-service helpers, the **gateway** (`docker/gateway.Dockerfile` + `nginx.conf`), `api_service/runtime_runner.py`, `api_service/celery_app.py`, Celery decorators, Redis/Celery deps in `pyproject.toml`, the **Postgres** service + deps, the `pg_advisory_xact_lock` path, and (optionally) the now-dead row-lock helpers in `concurrency.py`.
**Keep (slimmed):** `docker/backend.Dockerfile` (single image, Apptainer baked in, runnable `--privileged` for the Docker deployment); `docker/runtime-bash.Dockerfile` (now a **SIF build input** — built then converted+published per arch, not run as a service); a minimal single-service run recipe per profile. Auth stays configuration-driven (local vs Clerk).

**Unchanged (important):** all HTTP routers/endpoints, SQLAlchemy models, auth flow, LLM provider integrations, the tool containers themselves.

---

## 7. Open questions for sign-off
1. ✅ Locked: **D1** (app native **or** Docker container, tools always via Apptainer), **D2** (SQLite/WAL everywhere, Postgres dropped), **D3** (DB-backed queue), **gateway dropped**, and **two preserved profiles** (desktop + shared/web, §2.1) sharing one DB.
2. ✅ Resolved: **tool runtime = Apptainer** (default), `DockerBackend` retained only as a native-only dev convenience via `NEUROCADE_RUNTIME_BACKEND=docker`.
3. ✅ Resolved: **single-node assumption confirmed.** Each deployment (desktop and shared/web) is **one app process** serving its users — concurrency via async + the in-process worker, never `uvicorn --workers N` or multiple hosts. SQLite/WAL is sufficient; multi-process/multi-host is the out-of-scope "cluster profile."
4. ✅ Resolved: host needs **Apptainer** for the native deployment; the Docker deployment bakes Apptainer into the image and must be run **`--privileged`** (or with the `seccomp`/`systempaths`/`/dev/fuse` relaxations). Native is the recommended default.
5. ✅ Resolved: **gateway dropped** — the app serves its own frontend; shared/web TLS + host allow-listing is delegated to the operator's existing reverse proxy.

### Resolved after single-node sign-off
6. ✅ Resolved: **tool images = prebuilt SIFs (default), `docker://` pull as fallback.** Restore the `containers.py` `fileshare_url` download path; the bash image ships as a prebuilt SIF too (replacing the `neurocade-runtime-bash:local` local Docker tag). **SIFs are arch-specific** → ship one per arch (amd64 + arm64); the restored `_apptainer_guest_arch`/`_image_build_arch` enforces host-arch match, and `apptainer pull docker://` covers any arch not prebuilt.
7. ✅ Resolved (deferred): **network isolation not a priority — keep it simple.** Do **not** require a setuid Apptainer or enforce `--net --network none`; Apptainer shares the host network namespace for now. Keep only the cheap no-fakeroot/no-writable checks. Network-namespace isolation is logged as a future hardening item.
8. ✅ Resolved: **no Postgres data migration.** No live deployment holds real data — Phase 4 just makes SQLite the only DB; no export/import step.
9. ✅ Folding in (no decision): downloaded SIFs + any `APPTAINER_CACHEDIR` (fallback conversions) sit on a **persistent volume** (multi-GB; container restarts otherwise re-download) — see §3.3 + risks. First-run download/convert latency surfaced in `BackendStartupGate`. Privileged-container deployment writes tool outputs root-owned into the mounted data dir (uid/userns packaging note).
