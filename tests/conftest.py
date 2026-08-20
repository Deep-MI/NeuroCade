"""
Shared pytest fixtures for NeuroCade end-to-end tests.

These fixtures talk to a running local NeuroCade app on port 8000.
Start the app before running tests:

    ./scripts/run.sh start -d
    pytest tests/ -v
"""

import base64
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import requests

# ─── Constants ────────────────────────────────────────────────────────────────

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
API_TOKEN = os.environ.get("API_TOKEN", "static-token-12345")
DEFAULT_STORAGE_STATE_PATH = Path(
    os.environ.get(
        "PLAYWRIGHT_STORAGE_STATE",
        Path(__file__).resolve().parent.parent / "playwright" / ".clerk" / "user.json",
    )
)
DEFAULT_GUI_SESSION_ID = os.environ.get("TEST_GUI_SESSION_ID", "pytest-default-session")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "api-service"))
from backend_common.case_storage import (  # noqa: E402
    UPLOAD_SUFFIXES,
    case_id_from_storage_dir,
    case_storage_dir_from_root,
    upload_extension,
)

_host_data_dir = os.environ.get("HOST_DATA_DIR")
if not _host_data_dir or _host_data_dir == "/data":
    _host_data_dir = str(REPO_ROOT / "neurocade-data")
DATA_ROOT = Path(_host_data_dir)
UPLOAD_FIXTURES_DIR = Path(
    os.environ.get("NEUROCADE_UPLOAD_FIXTURES_DIR", REPO_ROOT / "tests" / "fixtures" / "uploads")
)
OUTPUTS_DIR = Path(os.environ.get("NEUROCADE_TEST_OUTPUTS_DIR", DATA_ROOT / "output"))
_LAST_GUI_SCOPE: dict[str, str | None] = {
    "workspace_id": None,
    "case_id": None,
    "gui_session_id": DEFAULT_GUI_SESSION_ID,
}
_GUI_PROCESSED_CASE_CACHE: dict[str, dict] = {}


@pytest.fixture(autouse=True)
def _disable_host_startup_services_for_in_process_tests():
    """Keep unit-test app lifespans local without a production-only pytest branch."""
    from api_service.main import app

    previous = getattr(app.state, "skip_host_startup_services", None)
    app.state.skip_host_startup_services = True
    try:
        yield
    finally:
        if previous is None:
            del app.state.skip_host_startup_services
        else:
            app.state.skip_host_startup_services = previous


def _case_id_from_upload(upload_path: Path) -> str:
    """Derive the case id from an upload filename."""
    if upload_path.name.endswith(".nii.gz"):
        return upload_path.name[:-7]
    return upload_path.stem


def _find_run_demo_case() -> tuple[str, str]:
    """Pick a case with an upload that can be safely used for rerun-style tests."""
    if not UPLOAD_FIXTURES_DIR.exists():
        raise RuntimeError(f"Upload fixture directory not found: {UPLOAD_FIXTURES_DIR}")
    for upload_path in sorted(UPLOAD_FIXTURES_DIR.iterdir()):
        if upload_path.is_file() and upload_extension(upload_path.name) in UPLOAD_SUFFIXES:
            return _case_id_from_upload(upload_path), upload_path.name
    raise RuntimeError("No upload file found for demo run tests.")


def _case_file_exists(case_dir: Path, *relative_parts: str) -> bool:
    """Check the case root and mri/ subdirectory for a relative output path."""
    candidate = Path(*relative_parts)
    return (case_dir / candidate).exists() or (case_dir / "mri" / candidate).exists()


def _loaded_volumes_for_case(case_dir: Path) -> list[str]:
    """List preferred MRI volumes that exist for a processed case."""
    preferred = [
        "mask.mgz",
        "aseg.auto_noCCseg.mgz",
        "aparc.DKTatlas+aseg.deep.mgz",
        "aparc.DKTatlas+aseg.mgz",
        "orig.mgz",
    ]
    return [name for name in preferred if _case_file_exists(case_dir, name)]


def _gui_layers_for_volumes(filenames: list[str], *, visible: bool = True) -> list[dict]:
    """Build typed GUI layer snapshots for test state seeding."""
    layers = []
    for index, filename in enumerate(filenames):
        is_segmentation = "aseg" in filename.lower() or "seg" in filename.lower()
        layer_type = "segmentation" if is_segmentation else "intensity"
        layers.append(
            {
                "id": f"{layer_type}:{index}:{filename}",
                "filename": filename,
                "type": layer_type,
                "role": layer_type,
                "visible": visible,
                "opacity": 0.7 if is_segmentation else 1.0,
                "display": {},
            }
        )
    return layers


def _case_has_processed_outputs(case_dir: Path) -> bool:
    """Return whether a case has the minimum processed outputs for GUI tests."""
    has_orig = _case_file_exists(case_dir, "orig.mgz")
    has_seg = _case_file_exists(case_dir, "aparc.DKTatlas+aseg.deep.mgz") or _case_file_exists(case_dir, "aparc.DKTatlas+aseg.mgz")
    return has_orig and has_seg


def _candidate_case_dirs(case_key: str) -> list[Path]:
    """Find possible readable-slug output directories for a case key."""
    if not OUTPUTS_DIR.exists():
        return []

    candidates: list[Path] = []
    workspaces_dir = OUTPUTS_DIR / "workspaces"
    if not workspaces_dir.exists():
        return candidates
    for workspace_dir in sorted(workspaces_dir.iterdir()):
        cases_dir = workspace_dir / "cases"
        if not cases_dir.is_dir():
            continue
        nested = cases_dir / case_key
        if nested.exists() and nested not in candidates:
            candidates.append(nested)
    return candidates


def _resolve_case_dir(case_key: str) -> Path:
    """Resolve the first existing output directory for a case key."""
    for candidate in _candidate_case_dirs(case_key):
        if candidate.is_dir():
            return candidate
    return OUTPUTS_DIR / "workspaces" / "_missing" / "cases" / case_key


def _find_processed_demo_case(exclude_case_id: str | None = None) -> tuple[str, str]:
    """Pick a stable processed demo case with full segmentation outputs."""
    if not UPLOAD_FIXTURES_DIR.exists():
        raise RuntimeError(f"Upload fixture directory not found: {UPLOAD_FIXTURES_DIR}")
    if not OUTPUTS_DIR.exists():
        raise RuntimeError(f"Output directory not found: {OUTPUTS_DIR}")

    for upload_path in sorted(UPLOAD_FIXTURES_DIR.iterdir()):
        if not upload_path.is_file() or upload_extension(upload_path.name) not in UPLOAD_SUFFIXES:
            continue
        case_id = _case_id_from_upload(upload_path)
        if case_id == exclude_case_id:
            continue
        case_dir = _resolve_case_dir(case_id)
        if _case_has_processed_outputs(case_dir):
            return case_id, upload_path.name

    for workspace_dir in sorted((OUTPUTS_DIR / "workspaces").iterdir() if (OUTPUTS_DIR / "workspaces").exists() else []):
        cases_dir = workspace_dir / "cases"
        if not cases_dir.is_dir():
            continue
        for case_dir in sorted(cases_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            if case_dir.name == exclude_case_id:
                continue
            if _case_has_processed_outputs(case_dir):
                for upload_path in sorted(UPLOAD_FIXTURES_DIR.glob(f"{case_dir.name}*")):
                    if upload_path.is_file() and upload_extension(upload_path.name) in UPLOAD_SUFFIXES:
                        return case_dir.name, upload_path.name

    # Fallback for lighter local demos that only have upload + minimal output metadata.
    for upload_path in sorted(UPLOAD_FIXTURES_DIR.iterdir()):
        if not upload_path.is_file() or upload_extension(upload_path.name) not in UPLOAD_SUFFIXES:
            continue
        case_id = _case_id_from_upload(upload_path)
        if case_id == exclude_case_id:
            continue
        if _resolve_case_dir(case_id).is_dir():
            return case_id, upload_path.name

    if exclude_case_id is not None:
        return _find_processed_demo_case(exclude_case_id=None)

    raise RuntimeError("No demo case with both upload and processed outputs was found.")


def _safe_demo_case_pair(resolver, *args, **kwargs) -> tuple[str | None, str | None]:
    """Return a demo case pair or None values when demo data is unavailable."""
    try:
        return resolver(*args, **kwargs)
    except RuntimeError:
        return None, None


DEMO_RUN_CASE_ID, DEMO_RUN_UPLOAD_FILENAME = _safe_demo_case_pair(_find_run_demo_case)
DEMO_CASE_ID, DEMO_UPLOAD_FILENAME = _safe_demo_case_pair(_find_processed_demo_case, exclude_case_id=DEMO_RUN_CASE_ID)

# The standard GUI state when the demo case is fully loaded
ADNI2_GUI_STATE = {
    "is_job_running": False,
    "case_id": DEMO_CASE_ID,
    "layers": (
        _gui_layers_for_volumes(_loaded_volumes_for_case(_resolve_case_dir(DEMO_CASE_ID)))
        if DEMO_CASE_ID
        else []
    ),
    "current_intensity_volume": DEMO_UPLOAD_FILENAME,
    "current_intensity_artifact_id": None,
}


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_job_manager():
    """Keep the in-process job worker singleton from leaking state across tests."""
    yield
    try:
        from api_service.jobs import job_manager

        job_manager.shutdown(wait=False)
        with job_manager._lock:
            job_manager._handles.clear()
    except Exception:
        pass


@pytest.fixture(scope="session")
def app_url():
    """Base URL of the local app."""
    return APP_URL


@pytest.fixture(scope="session")
def api_token():
    """Bearer token for API requests."""
    return API_TOKEN


@pytest.fixture(scope="session")
def adni2_state():
    """Standard GUI state dict for the adni2 test case."""
    return fresh_processed_case_data()["gui_state"].copy()


@pytest.fixture(scope="session")
def demo_case_id():
    """Processed demo case id used by GUI tests."""
    if not DEMO_CASE_ID:
        pytest.skip("No processed demo case with outputs is available in the configured test outputs directory.")
    return DEMO_CASE_ID


@pytest.fixture(scope="session")
def demo_upload_filename():
    """Upload fixture filename for the processed demo case."""
    if not DEMO_UPLOAD_FILENAME:
        pytest.skip("No processed demo upload fixture is available.")
    return DEMO_UPLOAD_FILENAME


@pytest.fixture(scope="session")
def demo_run_case_id():
    """Demo case id used for run-submission tests."""
    if not DEMO_RUN_CASE_ID:
        pytest.skip("No demo upload is available for run tests.")
    return DEMO_RUN_CASE_ID


@pytest.fixture(scope="session")
def demo_run_upload_filename():
    """Upload fixture filename used for run-submission tests."""
    if not DEMO_RUN_UPLOAD_FILENAME:
        pytest.skip("No demo upload is available for run tests.")
    return DEMO_RUN_UPLOAD_FILENAME


@pytest.fixture(scope="session")
def services_up(app_url):
    """Verify the local app is reachable (session-scoped, runs once)."""
    try:
        r = requests.get(f"{app_url}/api/app/healthz", timeout=5)
        r.raise_for_status()
    except Exception as exc:
        pytest.skip(
            f"NeuroCade app not reachable at {app_url}: {exc}"
        )


# ─── Helper functions (importable by tests) ──────────────────────────────────


def _get_app_auth_headers() -> dict[str, str]:
    """Build app auth headers from APP_AUTH_TOKEN or Playwright storage."""
    explicit_token = os.environ.get("APP_AUTH_TOKEN", "").strip()
    if explicit_token:
        return {"Authorization": f"Bearer {explicit_token}"}
    if not DEFAULT_STORAGE_STATE_PATH.exists():
        return {}
    try:
        state = json.loads(DEFAULT_STORAGE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    for cookie in state.get("cookies", []):
        token = cookie.get("value")
        if cookie.get("name") == "__session" and token and not _jwt_is_expired(token):
            return {"Authorization": f"Bearer {token}"}
    return {}


def _jwt_is_expired(token: str) -> bool:
    """Return whether a JWT exp claim is present and expired."""
    parts = token.split(".")
    if len(parts) < 2:
        return False
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return exp <= time.time()


def get_app_auth_headers() -> dict[str, str]:
    """Return Authorization headers for authenticated /api/app test requests."""
    return _get_app_auth_headers()


def require_app_auth_headers() -> dict[str, str]:
    """Return app auth headers, allowing the explicit local-auth profile."""
    headers = get_app_auth_headers()
    if headers:
        return headers
    try:
        response = requests.get(f"{APP_URL}/api/app/frontend-config", timeout=5)
        if response.ok and response.json().get("local_auth_enabled") is True:
            return {}
    except (requests.RequestException, ValueError):
        pass
    pytest.skip(
        "Authenticated /api/app tests require local auth, APP_AUTH_TOKEN, or PLAYWRIGHT_STORAGE_STATE with a __session cookie."
    )


def _skip_if_auth_failed(response: requests.Response, *, route: str) -> None:
    """Skip authenticated app tests when the API rejects credentials."""
    if response.status_code in {401, 403}:
        pytest.skip(
            f"Authenticated /api/app test request to {route} was rejected with {response.status_code}; "
            "configure APP_AUTH_TOKEN or refresh PLAYWRIGHT_STORAGE_STATE."
        )


def _case_storage_dir(workspace_id: str, case_id: str) -> Path:
    """Return the filesystem output directory for an app case."""
    return case_storage_dir_from_root(OUTPUTS_DIR, workspace_id, case_id)


def _unique_test_name(prefix: str, source_name: str | None = None) -> str:
    """Build a unique, filesystem-friendly test resource name."""
    normalized_prefix = prefix.strip().replace(" ", "-")
    normalized_source = (source_name or "").strip().replace(" ", "-")
    parts = [normalized_prefix]
    if normalized_source:
        parts.append(normalized_source)
    parts.append(uuid4().hex[:8])
    return "-".join(part for part in parts if part)


def create_workspace_via_api(
    *,
    app_url: str = APP_URL,
    name_prefix: str = "pytest-e2e-workspace",
) -> dict:
    """Create a uniquely named workspace through the authenticated app API."""
    workspace_name = _unique_test_name(name_prefix)
    response = requests.post(
        f"{app_url}/api/app/workspaces",
        headers=get_app_auth_headers(),
        json={"name": workspace_name},
        timeout=20,
    )
    _skip_if_auth_failed(response, route="/api/app/workspaces")
    response.raise_for_status()
    return response.json()


def upload_case_via_api(
    workspace_id: str,
    upload_filename: str,
    *,
    app_url: str = APP_URL,
    case_name_prefix: str = "pytest-e2e-case",
) -> dict:
    """Upload a fixture file as a uniquely named case in a workspace."""
    upload_path = UPLOAD_FIXTURES_DIR / upload_filename
    if not upload_path.exists():
        raise RuntimeError(f"Upload fixture not found: {upload_path}")

    case_name = _unique_test_name(case_name_prefix, _case_id_from_upload(upload_path))
    with upload_path.open("rb") as handle:
        response = requests.post(
            f"{app_url}/api/app/cases",
            headers=get_app_auth_headers(),
            data={"workspace_id": workspace_id, "title": case_name},
            files={"file": (upload_filename, handle, "application/octet-stream")},
            timeout=60,
        )
    _skip_if_auth_failed(response, route="/api/app/cases")
    response.raise_for_status()
    return response.json()


def upload_path_as_case_via_api(
    workspace_id: str,
    upload_path: Path,
    *,
    title: str,
    upload_filename: str | None = None,
    content_type: str = "application/octet-stream",
    app_url: str = APP_URL,
) -> dict:
    """Upload an arbitrary local file as a case for live QA."""
    with upload_path.open("rb") as handle:
        response = requests.post(
            f"{app_url}/api/app/cases",
            headers=get_app_auth_headers(),
            data={"workspace_id": workspace_id, "title": title},
            files={"file": (upload_filename or upload_path.name, handle, content_type)},
            timeout=60,
        )
    _skip_if_auth_failed(response, route="/api/app/cases")
    response.raise_for_status()
    return response.json()


def delete_workspace_via_api(workspace_id: str, *, app_url: str = APP_URL) -> None:
    """Delete a disposable workspace once any finishing run releases it."""
    deadline = time.monotonic() + 30
    while True:
        response = requests.delete(
            f"{app_url}/api/app/workspaces/{workspace_id}",
            headers=get_app_auth_headers(),
            json={"confirm_non_empty_delete": True},
            timeout=30,
        )
        _skip_if_auth_failed(response, route=f"/api/app/workspaces/{workspace_id}")
        if response.status_code != 409 or time.monotonic() >= deadline:
            response.raise_for_status()
            return
        time.sleep(0.5)


@pytest.fixture(scope="module")
def disposable_workspace(services_up):
    """Provide one isolated workspace per live-QA module and always remove it."""
    workspace = create_workspace_via_api(name_prefix="pytest-live")
    try:
        yield workspace
    finally:
        delete_workspace_via_api(workspace["id"])


def get_case_detail(case_id: str, app_url: str = APP_URL) -> dict:
    """Fetch case details from the authenticated app API."""
    response = requests.get(
        f"{app_url}/api/app/cases/{case_id}",
        headers=require_app_auth_headers(),
        timeout=20,
    )
    _skip_if_auth_failed(response, route=f"/api/app/cases/{case_id}")
    response.raise_for_status()
    return response.json()


def _build_case_context(workspace: dict, upload_result: dict) -> dict:
    """Assemble case metadata and GUI state expected by tests."""
    case_dir = _case_storage_dir(workspace["id"], upload_result["case_id"])
    loaded_volumes = _loaded_volumes_for_case(case_dir)
    return {
        "id": upload_result["case_id"],
        "workspace_id": workspace["id"],
        "workspace_name": workspace["name"],
        "case_id": upload_result["case_id"],
        "title": upload_result["title"],
        "upload_filename": upload_result["filenames"][0],
        "case_dir": case_dir,
        "loaded_volumes": loaded_volumes,
        "gui_state": {
            "workspace_id": workspace["id"],
            "case_id": upload_result["case_id"],
            "is_job_running": False,
            "layers": _gui_layers_for_volumes(loaded_volumes),
            "current_intensity_volume": upload_result["filenames"][0],
            "current_intensity_artifact_id": None,
        },
    }


def build_fresh_uploaded_case(
    *,
    upload_filename: str,
    app_url: str = APP_URL,
    workspace_prefix: str = "pytest-e2e-workspace",
    case_prefix: str = "pytest-e2e-case",
) -> dict:
    """Create a workspace, upload a fixture, and return its test context."""
    workspace = create_workspace_via_api(app_url=app_url, name_prefix=workspace_prefix)
    upload_result = upload_case_via_api(
        workspace["id"],
        upload_filename,
        app_url=app_url,
        case_name_prefix=case_prefix,
    )
    case_detail = get_case_detail(upload_result["case_id"], app_url)
    context = _build_case_context(workspace, upload_result)
    context["source_upload_filename"] = upload_filename
    context["artifacts"] = case_detail.get("artifacts", [])
    input_artifact = next(
        (
            artifact
            for artifact in context["artifacts"]
            if artifact.get("kind") == "volume" and (artifact.get("metadata") or {}).get("volume_role") != "segmentation"
        ),
        None,
    )
    if input_artifact is not None:
        context["gui_state"]["current_intensity_artifact_id"] = input_artifact.get("id")
        context["gui_state"]["current_intensity_volume"] = input_artifact.get("name") or upload_result["filenames"][0]
    context["runs"] = case_detail.get("runs", [])
    return context


def build_fresh_processed_case(
    *,
    source_case_key: str,
    upload_filename: str,
    app_url: str = APP_URL,
    workspace_prefix: str = "pytest-processed-workspace",
    case_prefix: str = "pytest-processed-case",
) -> dict:
    """Return API-backed context for a stable, read-only processed demo case."""
    del workspace_prefix, case_prefix
    source_case_dir = _resolve_case_dir(source_case_key)
    if not source_case_dir.exists():
        raise RuntimeError(f"Processed demo case directory not found: {source_case_dir}")
    case_id = case_id_from_storage_dir(source_case_dir)
    if not case_id:
        raise RuntimeError(f"Processed demo case has no identity manifest: {source_case_dir}")
    case_detail = get_case_detail(case_id, app_url)
    artifacts = case_detail.get("artifacts", [])
    loaded_volumes = _loaded_volumes_for_case(source_case_dir)
    input_artifact = next(
        (
            artifact
            for artifact in artifacts
            if artifact.get("kind") == "volume"
            and (artifact.get("metadata") or {}).get("volume_role") != "segmentation"
        ),
        None,
    )
    current_intensity = (input_artifact or {}).get("name") or upload_filename
    return {
        "id": case_id,
        "workspace_id": case_detail["workspace_id"],
        "workspace_name": "processed-demo",
        "case_id": case_id,
        "title": case_detail["title"],
        "upload_filename": upload_filename,
        "source_case_key": source_case_key,
        "case_dir": source_case_dir,
        "loaded_volumes": loaded_volumes,
        "artifacts": artifacts,
        "runs": case_detail.get("runs", []),
        "gui_state": {
            "workspace_id": case_detail["workspace_id"],
            "case_id": case_id,
            "is_job_running": False,
            "layers": _gui_layers_for_volumes(loaded_volumes),
            "current_intensity_volume": current_intensity,
            "current_intensity_artifact_id": (input_artifact or {}).get("id"),
        },
    }


def fresh_processed_case_data(app_url: str = APP_URL) -> dict:
    """Return cached GUI-ready processed case data for an application URL."""
    cache_key = app_url
    cached = _GUI_PROCESSED_CASE_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    if not DEMO_CASE_ID or not DEMO_UPLOAD_FILENAME:
        pytest.skip("No processed demo case with outputs is available in the configured test outputs directory.")
    processed_case = build_fresh_processed_case(
        source_case_key=DEMO_CASE_ID,
        upload_filename=DEMO_UPLOAD_FILENAME,
        app_url=app_url,
        workspace_prefix="pytest-gui-workspace",
        case_prefix="pytest-gui-case",
    )
    _GUI_PROCESSED_CASE_CACHE[cache_key] = processed_case
    return dict(processed_case)


@pytest.fixture(scope="module")
def fresh_run_case(services_up):
    """Fresh uploaded demo case for run-submission tests."""
    if not DEMO_RUN_UPLOAD_FILENAME:
        pytest.skip("No demo upload is available for run tests.")
    return build_fresh_uploaded_case(
        upload_filename=DEMO_RUN_UPLOAD_FILENAME,
        workspace_prefix="pytest-run-workspace",
        case_prefix="pytest-run-case",
    )


@pytest.fixture(scope="module")
def fresh_processed_case(services_up):
    """Stable processed demo case for module-scoped read-only tests."""
    if not DEMO_CASE_ID or not DEMO_UPLOAD_FILENAME:
        pytest.skip("No processed demo case with outputs is available in the configured test outputs directory.")
    return build_fresh_processed_case(
        source_case_key=DEMO_CASE_ID,
        upload_filename=DEMO_UPLOAD_FILENAME,
        workspace_prefix="pytest-processed-workspace",
        case_prefix="pytest-processed-case",
    )


def list_cases(app_url: str = APP_URL, workspace_id: str | None = None) -> list[dict]:
    """Fetch accessible cases from the authenticated app API."""
    params = {"workspace_id": workspace_id} if workspace_id else None
    response = requests.get(
        f"{app_url}/api/app/cases",
        headers=require_app_auth_headers(),
        params=params,
        timeout=10,
    )
    _skip_if_auth_failed(response, route="/api/app/cases")
    response.raise_for_status()
    return response.json()


def get_case_summary_by_case_id(case_id: str, app_url: str = APP_URL) -> dict:
    """Resolve an app case summary from the canonical case id."""
    normalized_case_id = str(case_id).strip()
    match = next(
        (item for item in list_cases(app_url) if item.get("id") == normalized_case_id),
        None,
    )
    if match is None:
        raise LookupError(f"No accessible case found for case_id={normalized_case_id!r}")
    return match


def get_case_runs(case_id: str, app_url: str = APP_URL) -> list[dict]:
    """Fetch runs for one case from the authenticated app API."""
    response = requests.get(
        f"{app_url}/api/app/cases/{case_id}/runs",
        headers=get_app_auth_headers(),
        timeout=10,
    )
    _skip_if_auth_failed(response, route=f"/api/app/cases/{case_id}/runs")
    response.raise_for_status()
    return response.json()


def _resolve_case_context(case_id: str | None, app_url: str = APP_URL) -> tuple[str | None, str | None]:
    """Resolve workspace and case ids for an existing app case."""
    normalized_case_id = str(case_id or "").strip()
    if not normalized_case_id:
        return None, None
    match = next((item for item in list_cases(app_url) if item.get("id") == normalized_case_id), None)
    if match is None:
        return None, None
    return match.get("workspace_id"), match.get("id")


def seed_gui_state(
    state: dict,
    app_url: str = APP_URL,
    *,
    gui_session_id: str = DEFAULT_GUI_SESSION_ID,
) -> dict:
    """POST GUI state through the authenticated app API."""
    payload = dict(state)
    payload["gui_session_id"] = gui_session_id
    workspace_id = payload.get("workspace_id")
    case_id = payload.get("case_id")
    if not workspace_id or not case_id:
        resolved_workspace_id, resolved_case_id = _resolve_case_context(case_id, app_url)
        workspace_id = workspace_id or resolved_workspace_id
        case_id = case_id or resolved_case_id
    if workspace_id:
        payload["workspace_id"] = workspace_id
    if case_id:
        payload["case_id"] = case_id

    url = f"{app_url}/api/app/gui/state"
    r = requests.post(url, json=payload, headers=require_app_auth_headers(), timeout=10)
    _skip_if_auth_failed(r, route="/api/app/gui/state")
    r.raise_for_status()
    _LAST_GUI_SCOPE.update(
        {
            "workspace_id": workspace_id,
            "case_id": case_id,
            "gui_session_id": gui_session_id,
        }
    )
    return r.json()


def chat_send(
    messages: list[dict],
    app_url: str = APP_URL,
    token: str = API_TOKEN,
    timeout: int = 300,
    workspace_id: str | None = None,
    case_id: str | None = None,
    gui_session_id: str | None = None,
) -> dict:
    """Send a assistant turn request through the app API."""
    resolved_workspace_id = workspace_id or _LAST_GUI_SCOPE.get("workspace_id")
    resolved_case_id = case_id or _LAST_GUI_SCOPE.get("case_id")
    resolved_gui_session_id = gui_session_id or _LAST_GUI_SCOPE.get("gui_session_id") or DEFAULT_GUI_SESSION_ID
    url = f"{app_url}/api/app/assistant/turns"
    headers = {"Content-Type": "application/json", **get_app_auth_headers()}
    payload = {
        "messages": messages,
        "workspace_id": resolved_workspace_id,
        "case_id": resolved_case_id,
        "gui_session_id": resolved_gui_session_id,
        "scope": "case" if resolved_case_id else "workspace",
    }
    for attempt in range(2):
        r = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        _skip_if_auth_failed(r, route="/api/app/assistant/turns")
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if "text/event-stream" in content_type or r.text.startswith("event: "):
            try:
                return parse_sse_response(r.text)
            except RuntimeError as exc:
                message = str(exc)
                transient_markers = [
                    "assistant_runtime_error",
                    "Error reading from remote server",
                ]
                if attempt == 0 and any(marker in message for marker in transient_markers):
                    time.sleep(2)
                    continue
                raise
        return r.json()
    raise RuntimeError("chat_send exhausted retries without a response")


def parse_sse_response(body: str) -> dict:
    """Convert the proxy SSE stream into the final JSON-ish payload used by tests."""
    events: list[tuple[str, str]] = []
    current_event: str | None = None
    data_lines: list[str] = []

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if current_event is not None:
                events.append((current_event, "\n".join(data_lines)))
            current_event = None
            data_lines = []
            continue
        if line.startswith("event: "):
            current_event = line.removeprefix("event: ").strip()
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))

    if current_event is not None:
        events.append((current_event, "\n".join(data_lines)))

    tool_calls_log: list[dict] = []
    final_payload: dict | None = None
    error_payload: dict | None = None
    for event_name, data in events:
        parsed = json.loads(data)
        if event_name == "tool_call":
            tool_calls_log.append(parsed)
        elif event_name == "done":
            final_payload = parsed
        elif event_name == "error":
            error_payload = parsed

    if final_payload is None:
        if error_payload is not None:
            raise RuntimeError(json.dumps(error_payload))
        raise ValueError("No SSE 'done' event found in chat response")

    if tool_calls_log:
        final_payload["tool_calls_log"] = tool_calls_log
    return final_payload


def docker_logs_since(since: str | None = None) -> str:
    """Fetch local container logs."""
    cmd = ["docker", "logs"]
    if since:
        cmd.extend(["--since", since])
    cmd.append(os.environ.get("NEUROCADE_CONTAINER_NAME", "neurocade"))
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, timeout=10
        )
        return out.decode(errors="replace")
    except Exception as e:
        return f"(could not fetch logs: {e})"


runtime_logs_since = docker_logs_since


def utc_timestamp() -> str:
    """Return current UTC time as an ISO string for log filtering."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def assert_tool_executed(logs: str, tool_name: str | None = None) -> bool:
    """Check that proxy logs confirm a tool call was made and returned."""
    assert "calling tool" in logs, "No 'calling tool' found in proxy logs"
    assert "result (" in logs, "No 'result (' found in proxy logs"
    if tool_name:
        assert (
            f"'{tool_name}'" in logs or f'"{tool_name}"' in logs
        ), f"Tool '{tool_name}' not found in proxy logs"
    return True


def assert_no_text_explanation(content: str) -> None:
    """Assert the LLM did NOT just give a text explanation instead of calling a tool."""
    explanation_markers = [
        "you should use",
        "you can use",
        "here is the command",
        "here's the command",
        "the command is",
        "example usage",
        "here's how",
        "you would run",
        "/path/to/",
        "command breakdown",
    ]
    content_lower = content.lower()
    found = [m for m in explanation_markers if m in content_lower]
    assert not found, (
        f"Response looks like a text explanation (markers: {found}). "
        f"The agent should have called a tool instead.\n"
        f"Content preview: {content[:300]}"
    )


def get_response_content(result: dict) -> str:
    """Extract assistant content from a assistant turn response."""
    return result.get("message", {}).get("content", "")
