"""Manage local NeuroCade runtime containers and installed tool indexes."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CalledProcessError, SubprocessError
from threading import Lock
from typing import Any, Iterable

from .container_paths import (
    CONTAINER_IGNORED_COMMANDS_JSONL,
    CONTAINER_INDEX_SCHEMA_VERSION,
    CONTAINER_PROBE_CWD,
    CONTAINER_TOOLS_JSONL,
    DISCOVERY_COMMANDS_FILE,
    apptainer_bin,
    container_ignored_commands_path,
    container_index_meta_path,
    container_root,
    container_tool_index_path,
    default_image_path,
    find_repo_root,
    help_cache_path,
    ignored_commands_path,
    index_lock_path,
    installed_tools_path,
    inventory_path,
    license_path,
)
from .container_specs import (
    CORE_SPECS,
    DOCKER_HUB_API_BASE,
    NEUROCONTAINER_BUILD_TAG_PATTERN,
    NEUROCONTAINERS_NAMESPACE,
    ContainerSpec,
    _neurocontainer_spec,
    _parse_neurocontainer_repo,
    _valid_container_command,
    container_display_name,
)
from .execution import RuntimeExecutionPolicy, RuntimeExecutionRequest, execute_runtime_request

HELP_PROBE_ARGS = ("--help", "-h", "--h", "help")
INSTALL_FREESURFER_ENV = "NEUROCADE_INSTALL_FREESURFER"
COMMAND_DISCOVERY_DIRS = (
    "/opt/*/bin",
    "/opt/*/tktools",
    "/opt/*/fsfast/bin",
    "/app",
    "/app/bin",
    "/apps",
    "/apps/*/bin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/neurodocker",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_COMMAND_EXCLUDED_SUFFIXES = (
    ".so",
    ".txt",
    ".xsd",
    ".xml",
    ".json",
    ".md",
    ".html",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
)
_COMMAND_EXCLUDED_PREFIXES = ("bashcomplete_",)

CORE_DOWNLOAD_JOBS_ENV = "NEUROCADE_CONTAINER_DOWNLOAD_JOBS"
PREBUILT_INDEX_ROOT = Path(__file__).with_name("prebuilt_indexes")
_INSTALL_PRINT_LOCK = Lock()


def _env_bool(name: str, default: bool = False) -> bool:
    """Return a boolean environment override."""
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _install_print(message: str, *, file: Any | None = None) -> None:
    """Print installer progress from serial or parallel install paths."""
    with _INSTALL_PRINT_LOCK:
        print(message, file=file or sys.stdout, flush=True)
_COMMAND_EXCLUDED_NAMES = {
    "engopts.sh",
    "matopts.sh",
    "mex",
    "mexext",
    "mexopts.sh",
    "mexsh",
    "mw_mpiexec",
    "mw_smpd",
    "optsetup.sh",
    "worker",
}

def _http_json(url: str) -> dict[str, Any]:
    """Fetch a JSON object with NeuroCade request defaults."""
    timeout = float(os.environ.get("NEUROCADE_CONTAINER_SEARCH_TIMEOUT", "20"))
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "NeuroCade"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_neurocontainer_image_name(image_name: str) -> tuple[str, str, str, str] | None:
    """Parse app, runtime, and build metadata from a container image name."""
    stem = image_name.removesuffix(".sif").removesuffix(".simg")
    repo_name, sep, build_date = stem.rpartition("_")
    if not sep or not build_date:
        return None
    app, runtime_version = _parse_neurocontainer_repo(repo_name)
    return repo_name, app, runtime_version, build_date


def _candidate_score(query: str, repo_name: str) -> tuple[int, str]:
    """Score a NeuroContainers repository match for search ranking."""
    normalized_query = query.lower()
    normalized_repo = repo_name.lower()
    app, _version = _parse_neurocontainer_repo(repo_name)
    normalized_app = app.lower()
    if normalized_repo == normalized_query:
        return 0, repo_name
    if normalized_app == normalized_query:
        return 1, repo_name
    if normalized_repo.startswith(f"{normalized_query}_") or normalized_app.startswith(normalized_query):
        return 2, repo_name
    if normalized_query in normalized_repo:
        return 3, repo_name
    return 4, repo_name


def _docker_hub_namespace_repositories(query: str) -> list[str]:
    """Find matching NeuroContainers repositories in Docker Hub."""
    base_url = os.environ.get("DOCKER_HUB_API_BASE", DOCKER_HUB_API_BASE).rstrip("/")
    encoded_query = urllib.parse.quote(query)
    urls = [
        f"{base_url}/v2/namespaces/{NEUROCONTAINERS_NAMESPACE}/repositories?page_size=100&name={encoded_query}",
        f"{base_url}/v2/search/repositories/?page_size=100&query={encoded_query}",
    ]
    repositories: set[str] = set()
    for url in urls:
        payload = _http_json(url)
        for row in payload.get("results") or []:
            repo_name = str(row.get("name") or row.get("repo_name") or "")
            namespace = str(row.get("namespace") or row.get("repo_owner") or "")
            if "/" in repo_name:
                namespace, repo_name = repo_name.split("/", 1)
            if namespace and namespace != NEUROCONTAINERS_NAMESPACE:
                continue
            if repo_name:
                repositories.add(repo_name)
    return sorted(repositories, key=lambda repo: _candidate_score(query, repo))


def _docker_hub_tags(repo_name: str) -> list[str]:
    """Return Docker Hub tag names for a NeuroContainers repository."""
    base_url = os.environ.get("DOCKER_HUB_API_BASE", DOCKER_HUB_API_BASE).rstrip("/")
    encoded_repo = urllib.parse.quote(repo_name, safe="")
    url = f"{base_url}/v2/namespaces/{NEUROCONTAINERS_NAMESPACE}/repositories/{encoded_repo}/tags?page_size=100"
    payload = _http_json(url)
    return [str(row.get("name")) for row in payload.get("results") or [] if row.get("name")]


def _latest_neurocontainer_tag(repo_name: str) -> str:
    """Return the newest build tag available for a NeuroContainers repository."""
    tags = _docker_hub_tags(repo_name)
    build_tags = sorted((tag for tag in tags if re.fullmatch(NEUROCONTAINER_BUILD_TAG_PATTERN, tag)), reverse=True)
    if build_tags:
        return build_tags[0]
    if tags:
        return sorted(tags, reverse=True)[0]
    raise RuntimeError(f"NeuroContainer '{repo_name}' exists but has no Docker Hub tags.")


def resolve_neurocontainer_spec(query: str) -> ContainerSpec:
    """Resolve a NeuroContainers search query into an installable spec."""
    requested = query.removeprefix("docker://").removeprefix(f"{NEUROCONTAINERS_NAMESPACE}/")
    repo_query, sep, requested_tag = requested.partition(":")
    candidates = _docker_hub_namespace_repositories(repo_query)
    if not candidates:
        raise KeyError(f"No NeuroContainer repository matching '{query}' was found in the {NEUROCONTAINERS_NAMESPACE} namespace.")
    best_score = _candidate_score(repo_query, candidates[0])[0]
    best_candidates = [candidate for candidate in candidates if _candidate_score(repo_query, candidate)[0] == best_score]
    if len(best_candidates) > 1 and best_score > 1:
        matches = ", ".join(best_candidates[:10])
        raise KeyError(f"Multiple NeuroContainer repositories match '{query}': {matches}. Use a more specific name.")
    repo_name = sorted(best_candidates, reverse=True)[0]
    build_date = requested_tag if sep else _latest_neurocontainer_tag(repo_name)
    return _neurocontainer_spec(repo_name, build_date, name=repo_query)


def resolve_container_spec(name: str) -> ContainerSpec:
    """Resolve a managed or NeuroContainers name into an installable spec."""
    spec = CORE_SPECS.get(name)
    if spec is not None:
        return spec
    return resolve_neurocontainer_spec(name)


def install_command(name: str) -> str:
    """Return the shell command users can run to install a container."""
    return f"./scripts/containers.sh install {name}"


def refresh_command() -> str:
    """Return the shell command users can run to refresh the tool index."""
    return "./scripts/containers.sh refresh-index"


def missing_container_message(name: str, *, stale_index: bool = False) -> str:
    """Return an actionable message for a missing or stale container."""
    label = container_display_name(name)
    if name not in CORE_SPECS:
        if stale_index:
            return f"{label} is not installed or the installed index is stale. Run `{refresh_command()}` after installing the container."
        return f"{label} is not installed."
    if stale_index:
        return f"{label} is not installed or the installed index is stale. Run `{install_command(name)}` or `{refresh_command()}`."
    return f"{label} is not installed. Run `{install_command(name)}`."


def _load_inventory(path: Path | None = None) -> dict[str, Any]:
    """Load the installed-container inventory, or return an empty inventory."""
    target = path or inventory_path()
    if not target.exists():
        return {"containers": []}
    return json.loads(target.read_text(encoding="utf-8"))


def installed_containers(path: Path | None = None) -> list[dict[str, Any]]:
    """Return container rows from the generated inventory."""
    payload = _load_inventory(path)
    return [row for row in payload.get("containers", []) if isinstance(row, dict)]


def resolve_core_image(name: str, *, root: Path | None = None) -> Path:
    """Return the installed image path for a core container."""
    for row in installed_containers(inventory_path(root)):
        if row.get("name") == name and row.get("image_path"):
            image = Path(str(row["image_path"])).expanduser()
            if image.is_file():
                return image.resolve()
    raise FileNotFoundError(missing_container_message(name, stale_index=True))


def _run(command: list[str], *, cwd: Path, dry_run: bool = False) -> None:
    """Run a command, or print it when dry-run mode is enabled."""
    if dry_run:
        print("$ " + " ".join(command))
        return
    execute_runtime_request(
        RuntimeExecutionRequest(
            argv=command,
            cwd=cwd,
            timeout_s=None,
            execution_mode="runtime-tools-install",
            check=True,
            capture_output=False,
        )
    )


def _lima_apptainer_wrapper(command: str) -> bool:
    """Return whether the configured Apptainer command is the macOS Lima wrapper."""
    if sys.platform != "darwin":
        return False
    command_path = Path(command).expanduser()
    return command_path.name == "apptainer" and command_path.parent.name == "bin" and command_path.parent.parent.name == ".apptainer"


def _run_lima_apptainer_build(target: Path, build_file: Path, *, dry_run: bool = False) -> None:
    """Build a SIF inside the Lima VM and copy it back to the host filesystem."""
    guest_target = f"/tmp/neurocade-{target.stem}-{os.getpid()}.sif"
    partial = target.with_name(f"{target.name}.partial")
    build_command = [
        "limactl",
        "shell",
        "apptainer",
        "sh",
        "-lc",
        "rm -f \"$1\"; apptainer build \"$1\" \"$2\"",
        "sh",
        guest_target,
        str(build_file),
    ]
    copy_command = ["limactl", "copy", "--backend=scp", f"apptainer:{guest_target}", str(partial)]
    cleanup_command = ["limactl", "shell", "apptainer", "rm", "-f", guest_target]
    if dry_run:
        print("$ " + " ".join(build_command))
        print("$ " + " ".join(copy_command))
        print("$ " + " ".join(cleanup_command))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial.unlink(missing_ok=True)
    try:
        _run(build_command, cwd=target.parent, dry_run=False)
        _run(copy_command, cwd=target.parent, dry_run=False)
        partial.replace(target)
    finally:
        try:
            _run(cleanup_command, cwd=target.parent, dry_run=False)
        except Exception:
            pass


def _apptainer_tmp_dir(target: Path, *, create: bool = True) -> Path:
    configured = os.environ.get("APPTAINER_TMPDIR")
    if configured:
        return Path(configured).expanduser()
    uid = getattr(os, "getuid", lambda: "user")()
    for candidate in (Path("/var/tmp") / f"neurocade-apptainer-{uid}", target.parent / ".apptainer-tmp"):
        if not create:
            return candidate
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        return candidate
    return target.parent / ".apptainer-tmp"


def _run_apptainer_pull(target: Path, uri: str, *, dry_run: bool = False) -> None:
    cache_dir = Path(os.environ.get("APPTAINER_CACHEDIR") or find_repo_root() / ".apptainer" / "cache").expanduser()
    tmp_dir = _apptainer_tmp_dir(target, create=not dry_run).expanduser()
    command = [apptainer_bin(), "pull", "--force", str(target), uri]
    if dry_run:
        print(f"APPTAINER_CACHEDIR={cache_dir} APPTAINER_TMPDIR={tmp_dir} " + " ".join(command))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("APPTAINER_CACHEDIR", str(cache_dir))
    env.setdefault("SINGULARITY_CACHEDIR", str(cache_dir))
    env.setdefault("APPTAINER_TMPDIR", str(tmp_dir))
    env.setdefault("SINGULARITY_TMPDIR", str(tmp_dir))
    try:
        execute_runtime_request(
            RuntimeExecutionRequest(
                argv=command,
                cwd=target.parent,
                env=env,
                timeout_s=None,
                execution_mode="runtime-tools-apptainer-pull",
                check=True,
                capture_output=False,
            )
        )
    except Exception as exc:
        raise RuntimeError(
            "Apptainer failed to pull or convert the OCI image. Large images need substantial "
            f"temporary space; this pull used APPTAINER_TMPDIR={env['APPTAINER_TMPDIR']} and "
            f"APPTAINER_CACHEDIR={env['APPTAINER_CACHEDIR']}. If the error mentions quota or "
            "short write, set APPTAINER_TMPDIR and APPTAINER_CACHEDIR to a filesystem with more quota "
            "and rerun the install."
        ) from exc


def _sha256_file(path: Path) -> str:
    """Return the SHA256 digest for a local file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sha256(path: Path, expected_sha256: str) -> None:
    """Fail when a downloaded container does not match its expected digest."""
    actual = _sha256_file(path)
    if actual.lower() != expected_sha256.lower():
        path.unlink(missing_ok=True)
        raise RuntimeError(f"SHA256 mismatch for {path}: expected {expected_sha256}, got {actual}")


def _download_file(url: str, target: Path, *, expected_sha256: str | None = None, dry_run: bool = False) -> None:
    """Download a file atomically to the requested target path and verify it when configured."""
    partial = target.with_name(f"{target.name}.partial")
    if not dry_run:
        partial.unlink(missing_ok=True)
    try:
        _run(["curl", "-fL", url, "-o", str(partial)], cwd=target.parent, dry_run=dry_run)
        if dry_run:
            return
        if expected_sha256:
            _verify_sha256(partial, expected_sha256)
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _fallback_install(spec: ContainerSpec, target: Path, *, dry_run: bool = False) -> None:
    """Install a container from its upstream image or local build file."""
    if spec.docker_uri:
        print(f"Installing {spec.name} from upstream Docker image: {spec.docker_uri}")
        _run_apptainer_pull(target, spec.docker_uri, dry_run=dry_run)
        return
    if spec.build_file:
        build_file = find_repo_root() / spec.build_file
        print(f"Building {spec.name} locally from {build_file}")
        if _lima_apptainer_wrapper(apptainer_bin()):
            if os.environ.get("NEUROCADE_ALLOW_LOCAL_CONTAINER_BUILDS") != "1":
                raise RuntimeError(
                    f"{spec.name} prebuilt GitHub release asset is unavailable, and local Apptainer builds are disabled on macOS/Lima. "
                    "Publish the release asset or set NEUROCADE_ALLOW_LOCAL_CONTAINER_BUILDS=1 to attempt a large local build."
                )
            print(f"Building {spec.name} inside the Lima VM, then copying the SIF back to {target}")
            _run_lima_apptainer_build(target, build_file, dry_run=dry_run)
            return
        _run([apptainer_bin(), "build", str(target), str(build_file)], cwd=target.parent, dry_run=dry_run)
        return
    raise RuntimeError(f"No install source is configured for {spec.name}.")


def _pull_or_build(spec: ContainerSpec, target: Path, *, source: str, dry_run: bool = False) -> None:
    """Install a container from the preferred source, with fallback if needed."""
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
    if source in {"auto", "fileshare"} and spec.fileshare_url:
        print(f"Installing {spec.name} from direct container image: {spec.fileshare_url}")
        try:
            _download_file(spec.fileshare_url, target, expected_sha256=spec.fileshare_sha256, dry_run=dry_run)
            if source == "auto" and dry_run:
                _fallback_install(spec, target, dry_run=True)
            return
        except (CalledProcessError, OSError, RuntimeError) as exc:
            if source == "fileshare":
                raise
            print(f"Direct container image source unavailable for {spec.name}; falling back to local/upstream source. ({exc})", file=sys.stderr)
    _fallback_install(spec, target, dry_run=dry_run)


def _install_container_image(spec: ContainerSpec, *, source: str = "auto", dry_run: bool = False) -> dict[str, Any] | None:
    """Install one container image and return its inventory row when newly created."""
    if spec.requires_license and license_path() is None:
        raise RuntimeError(f"{spec.name} requires a FreeSurfer license. Install neurocade-data/license.txt first.")
    target = default_image_path(spec)
    if target.exists() and not dry_run:
        print(f"{spec.name} already installed: {target}")
        return None
    print(f"Installing {spec.name} -> {target}")
    _pull_or_build(spec, target, source=source, dry_run=dry_run)
    if dry_run:
        return None
    return _container_row(spec, target)


def install_container(
    name: str,
    *,
    source: str = "auto",
    dry_run: bool = False,
    refresh: bool = True,
    harvest_help: bool = True,
    rebuild_index: bool = False,
) -> dict[str, Any] | None:
    """Install one managed container and optionally refresh the index."""
    spec = resolve_container_spec(name)
    container = _install_container_image(spec, source=source, dry_run=dry_run)
    if container is None:
        return None
    if refresh:
        with _index_lock():
            refresh_container_index(container, harvest_help=harvest_help, use_prebuilt_index=not rebuild_index)
            merge_container_indexes()
    return container


def uninstall_container(name: str, *, dry_run: bool = False, refresh: bool = True) -> None:
    """Remove one managed container directory and optionally refresh the index."""
    spec = CORE_SPECS.get(name)
    path = default_image_path(spec) if spec else _installed_container_path(name)
    if path is None:
        raise FileNotFoundError(missing_container_message(name, stale_index=True))
    directory = path.parent
    if dry_run:
        print(f"would remove {directory}")
        return
    shutil.rmtree(directory, ignore_errors=True)
    if refresh:
        with _index_lock():
            merge_container_indexes()


def core_install_plan(*, root: Path | None = None, include_freesurfer: bool | None = None) -> tuple[str, ...]:
    """Return core containers to install for the current FreeSurfer opt-in state."""
    names = ["fastsurfer", "bash_image", "dcm2niix"]
    if include_freesurfer is None:
        include_freesurfer = _env_bool(INSTALL_FREESURFER_ENV)
    if include_freesurfer:
        names.append("freesurfer")
    return tuple(names)


def _container_row(spec: ContainerSpec, image: Path) -> dict[str, Any]:
    """Build an inventory row for an installed container image."""
    stat = image.stat()
    return {
        "name": spec.name,
        "kind": spec.kind,
        "app": spec.app,
        "runtime_version": spec.runtime_version,
        "build_date": spec.build_date,
        "image_name": spec.image_name,
        "image_path": str(image.resolve()),
        "image_size_bytes": stat.st_size,
        "image_mtime_ns": stat.st_mtime_ns,
        "image_sha256": _sha256_file(image),
        "commands": list(spec.command_names),
    }


def _container_row_without_hash(spec: ContainerSpec, image: Path) -> dict[str, Any]:
    """Build a current container row without reading the whole image."""
    stat = image.stat()
    return {
        "name": spec.name,
        "kind": spec.kind,
        "app": spec.app,
        "runtime_version": spec.runtime_version,
        "build_date": spec.build_date,
        "image_name": spec.image_name,
        "image_path": str(image.resolve()),
        "image_size_bytes": stat.st_size,
        "image_mtime_ns": stat.st_mtime_ns,
        "commands": list(spec.command_names),
    }


def _generic_neurocontainer_rows(root: Path | None = None, *, skip_paths: set[Path] | None = None) -> list[dict[str, Any]]:
    """Return inventory rows for installed non-core NeuroContainers images."""
    neurocontainer_root = container_root(root) / "neurocontainer"
    if not neurocontainer_root.is_dir():
        return []
    skipped = {path.resolve() for path in (skip_paths or set())}
    rows: list[dict[str, Any]] = []
    for image in sorted([*neurocontainer_root.glob("*/*.sif"), *neurocontainer_root.glob("*/*.simg")]):
        resolved = image.resolve()
        if resolved in skipped:
            continue
        parsed = _parse_neurocontainer_image_name(image.name)
        if parsed is None:
            continue
        repo_name, app, _runtime_version, build_date = parsed
        spec = _neurocontainer_spec(repo_name, build_date, name=app)
        rows.append(_container_row(spec, image))
    return rows


def _installed_container_path(name: str, *, root: Path | None = None) -> Path | None:
    """Find an installed container image by managed name, app, or image name."""
    for row in scan_containers(root):
        if name in {row.get("name"), row.get("app"), row.get("image_name")}:
            return Path(str(row["image_path"]))
    return None


def scan_containers(root: Path | None = None) -> list[dict[str, Any]]:
    """Scan the managed container root for installed images."""
    rows: list[dict[str, Any]] = []
    known_paths: set[Path] = set()
    for spec in CORE_SPECS.values():
        image = default_image_path(spec, root)
        if not image.is_file():
            continue
        known_paths.add(image.resolve())
        if spec.requires_license and license_path(root) is None:
            continue
        rows.append(_container_row(spec, image))
    rows.extend(_generic_neurocontainer_rows(root, skip_paths=known_paths))
    return sorted(rows, key=lambda row: (row["kind"], row["app"], row["runtime_version"], row["image_name"]))


def _inventory_by_name(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Index installed-container inventory rows by managed name."""
    return {str(row.get("name")): row for row in installed_containers(inventory_path(root))}


def container_status(root: Path | None = None) -> dict[str, Any]:
    """Return install and index status for core managed containers."""
    repo_root = find_repo_root(root)
    inventory = _load_inventory(inventory_path(repo_root))
    indexed = _inventory_by_name(repo_root)
    planned = set(core_install_plan(root=repo_root))
    rows: list[dict[str, Any]] = []
    for name, spec in sorted(CORE_SPECS.items()):
        image = default_image_path(spec, repo_root)
        installed = image.is_file()
        indexed_row = indexed.get(name)
        indexed_path = Path(str(indexed_row.get("image_path"))) if indexed_row and indexed_row.get("image_path") else None
        stale_index = bool(indexed_row) and (not indexed_path or not indexed_path.is_file() or indexed_path.resolve() != image.resolve())
        needs_attention = not installed or stale_index
        source_order = []
        if spec.fileshare_url:
            source_order.append("fileshare")
        if spec.docker_uri:
            source_order.append("docker")
        if spec.build_file:
            source_order.append("local-build")
        rows.append(
            {
                "name": name,
                "display_name": container_display_name(name),
                "kind": spec.kind,
                "app": spec.app,
                "runtime_version": spec.runtime_version,
                "build_date": spec.build_date,
                "installed": installed,
                "planned_by_core_install": name in planned,
                "requires_license": spec.requires_license,
                "license_found": license_path(repo_root) is not None if spec.requires_license else None,
                "indexed": indexed_row is not None,
                "stale_index": stale_index,
                "image_name": spec.image_name,
                "image_path": str(image),
                "commands": list(spec.command_names),
                "fileshare_url": spec.fileshare_url,
                "docker_uri": spec.docker_uri,
                "build_file": str(find_repo_root(repo_root) / spec.build_file) if spec.build_file else None,
                "source_order": source_order,
                "install_command": install_command(name),
                "missing_message": missing_container_message(name, stale_index=stale_index) if needs_attention else None,
            }
        )
    return {
        "container_root": str(container_root(repo_root)),
        "inventory_path": str(inventory_path(repo_root)),
        "installed_tools_path": str(installed_tools_path(repo_root)),
        "index_generated_at": inventory.get("generated_at"),
        "containers": rows,
    }


def check_core_fast(*, root: Path | None = None, include_freesurfer: bool | None = None) -> None:
    """Verify the core container startup contract without hashing image files."""
    repo_root = find_repo_root(root)
    inventory_target = inventory_path(repo_root)
    tools_target = installed_tools_path(repo_root)
    if not inventory_target.is_file() or inventory_target.stat().st_size == 0:
        raise FileNotFoundError(f"missing inventory {inventory_target}")
    if not tools_target.is_file() or tools_target.stat().st_size == 0:
        raise FileNotFoundError(f"missing installed tool index {tools_target}")

    indexed = _inventory_by_name(repo_root)
    for name in core_install_plan(root=repo_root, include_freesurfer=include_freesurfer):
        spec = resolve_container_spec(name)
        image = default_image_path(spec, repo_root)
        if not image.is_file():
            raise FileNotFoundError(f"missing {name} image at {image}")
        indexed_row = indexed.get(name)
        if not indexed_row or not indexed_row.get("image_path"):
            raise FileNotFoundError(f"missing {name} inventory row")
        indexed_path = Path(str(indexed_row["image_path"])).expanduser()
        if indexed_path.resolve() != image.resolve():
            raise FileNotFoundError(f"{name} inventory points to {indexed_path}, expected {image}")

        container = {"image_path": str(image)}
        for sidecar in (container_index_meta_path(container), container_tool_index_path(container)):
            if not sidecar.is_file() or sidecar.stat().st_size == 0:
                raise FileNotFoundError(f"missing {name} sidecar {sidecar}")


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(1, value)


def _container_stat_key(container: dict[str, Any], command: str) -> tuple[str, str, str, int, int]:
    return (
        str(container.get("image_path") or ""),
        str(container.get("image_name") or ""),
        command,
        int(container.get("image_mtime_ns") or 0),
        int(container.get("image_size_bytes") or 0),
    )


def _valid_discovered_command(name: str, *, path: str | None = None) -> bool:
    if not _valid_container_command(name):
        return False
    if name in {".", ".."}:
        return False
    if any(character.isspace() for character in name):
        return False
    lowered = name.lower()
    if lowered in _COMMAND_EXCLUDED_NAMES:
        return False
    if lowered.startswith(_COMMAND_EXCLUDED_PREFIXES):
        return False
    if lowered.endswith(_COMMAND_EXCLUDED_SUFFIXES) or ".so." in lowered:
        return False
    if path and any(part in path.lower() for part in ("/lib/", "/lib64/", "/mcr", "/cefclient/", "/resources/")):
        return False
    return True


def _normalize_command_names(commands: Iterable[str], *, max_commands: int | None = None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for command in commands:
        raw = str(command).strip()
        name = Path(raw).name if raw.startswith("/") else raw
        if not _valid_discovered_command(name, path=raw if raw.startswith("/") else None) or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
        if max_commands is not None and len(normalized) >= max_commands:
            break
    return normalized


def _run_container_text(image: Path, args: list[str], *, timeout_s: int):
    CONTAINER_PROBE_CWD.mkdir(parents=True, exist_ok=True)
    return execute_runtime_request(
        RuntimeExecutionRequest(
            argv=[apptainer_bin(), "exec", "--net", "--network", "none", "--cleanenv", "--no-home", str(image), *args],
            cwd=CONTAINER_PROBE_CWD,
            timeout_s=timeout_s,
            execution_mode="runtime-tools-container-probe",
            require_rootless_apptainer=True,
            runtime_policy=RuntimeExecutionPolicy(network_disabled=True, gpu_enabled=False),
        )
    )


def _discover_commands_from_output(output: str, *, max_commands: int | None = None) -> list[str]:
    commands: list[str] = []
    for line in output.splitlines():
        candidate = line.strip()
        commands.append(candidate)
    if max_commands is None:
        max_commands = _env_int("NEUROCADE_MAX_DISCOVERED_COMMANDS", 1200)
    return _normalize_command_names(commands, max_commands=max_commands)


def discover_container_commands(container: dict[str, Any]) -> list[str]:
    """Discover executable command names inside one installed container image."""
    seed_commands = [str(command) for command in container.get("commands") or []]
    image = Path(str(container["image_path"]))
    timeout_s = _env_int("NEUROCADE_COMMAND_DISCOVERY_TIMEOUT", 30)
    max_commands = _env_int("NEUROCADE_MAX_DISCOVERED_COMMANDS", 1200)
    quoted_patterns = " ".join("'" + directory.replace("'", "'\\''") + "'" for directory in COMMAND_DISCOVERY_DIRS)
    script = (
        "for pattern in "
        + quoted_patterns
        + "; do "
        + "for d in $pattern; do "
        + "[ -d \"$d\" ] || continue; "
        + "find -H \"$d\" -maxdepth 1 \\( -type f -o -type l \\) -perm /111 -printf '%p\\n' 2>/dev/null; "
        + "done; "
        + "done"
    )
    discovered: list[str] = []
    try:
        completed = _run_container_text(image, ["sh", "-lc", script], timeout_s=timeout_s)
        discovered = _discover_commands_from_output(completed.stdout or "", max_commands=max_commands)
    except (OSError, SubprocessError, TimeoutError):
        discovered = []
    commands = _normalize_command_names([*seed_commands, *discovered], max_commands=max_commands)
    try:
        commands_path = image.parent / DISCOVERY_COMMANDS_FILE
        commands_path.write_text("".join(f"{command}\n" for command in commands), encoding="utf-8")
    except OSError:
        pass
    return commands


def _load_help_cache(path: Path) -> dict[tuple[str, str, str, int, int], dict[str, Any]]:
    if not path.exists():
        return {}
    cache: dict[tuple[str, str, str, int, int], dict[str, Any]] = {}
    for row in _iter_jsonl_dicts(path):
        key = (
            str(row.get("image_path") or ""),
            str(row.get("image_name") or ""),
            str(row.get("command") or ""),
            int(row.get("image_mtime_ns") or 0),
            int(row.get("image_size_bytes") or 0),
        )
        cache[key] = row
    return cache


def _write_help_cache(path: Path, cache: dict[tuple[str, str, str, int, int], dict[str, Any]]) -> None:
    rows = []
    for cached_row in cache.values():
        row = dict(cached_row)
        raw_help = row.get("raw_help_text")
        if raw_help:
            reason = _help_failure_reason(str(raw_help))
            if reason is not None:
                row["ignored"] = True
                row["ignore_reason"] = reason
                row["raw_help_text"] = None
        rows.append(row)
    rows = sorted(rows, key=lambda row: (str(row.get("image_name") or ""), str(row.get("command") or "")))
    _atomic_write_jsonl(path, rows)


def _is_unrecognized_help_arg_output(normalized: str) -> bool:
    retry_markers = (
        "flag --help unrecognized",
        "unrecognized flag",
        "unrecognized option",
        "unrecognized argument",
        "unrecognized arguments",
        "unknown option",
        "unknown argument",
        "option -help unknown",
        "did you really mean --help",
        "unknown !!",
        "invalid option",
        "invalid option --",
        "illegal option",
        "not a recognized option",
        "not recognized",
        "not regocnized",
        "lacking argument to option",
        "option requires an argument",
        "requires an argument --",
        "unexpected argument",
        "cannot find --help",
    )
    if any(marker in normalized for marker in retry_markers):
        return True
    if normalized.startswith("error") and any(help_arg in normalized for help_arg in HELP_PROBE_ARGS):
        return True
    return "unrecognized" in normalized and any(token in normalized for token in ("flag", "option", "argument", "arg"))


def _is_not_found_failure_output(normalized: str) -> bool:
    if not normalized:
        return False
    not_found_markers = (
        "command not found",
        "no such file or directory",
        "cannot open",
        "can't open",
        "could not open",
        "cannot cd",
        "cannot find",
        "couldn't determine type of file",
    )
    if any(marker in normalized for marker in not_found_markers):
        return True
    return normalized.startswith("error") and "not found" in normalized


def _looks_like_invocation_line(line: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?=[A-Za-z][\w.-]*[0-9.-])[A-Za-z][\w.-]*\s+(?:\[?<?[A-Z][A-Z0-9_./-]*>?]?\s*)+",
            line,
        )
    )


def _looks_like_help(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if _is_removed_freesurfer_help(normalized):
        return False
    if _is_unrecognized_help_arg_output(normalized):
        return False
    if _is_not_found_failure_output(normalized):
        return False
    return bool(
        "usage" in normalized
        or "synopsis" in normalized
        or "options" in normalized
        or re.search(r"(?m)^\s*-{1,2}[\w?][\w?.-]*(?:[,\s=]|$)", text)
        or (
            "this script" in normalized
            and any(_looks_like_invocation_line(line) for line in text.splitlines())
        )
    )


def _help_failure_reason(text: str) -> str | None:
    normalized = text.strip().lower()
    if not normalized:
        return "empty"
    if _is_removed_freesurfer_help(normalized):
        return "removed"
    if _is_not_found_failure_output(normalized):
        return "not_found"
    if _is_unrecognized_help_arg_output(normalized):
        return "unrecognized_help_arg"
    if not _looks_like_help(text):
        return "not_help"
    return None


def _is_removed_freesurfer_help(text: str) -> bool:
    return "has been removed from this version of freesurfer" in text.lower()


def _help_cache_row(
    key: tuple[str, str, str, int, int],
    command: str,
    *,
    help_arg: str | None,
    raw_help_text: str | None,
    returncode: int | None = None,
    ignored: bool = False,
    ignore_reason: str | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row = {
        "image_path": key[0],
        "image_name": key[1],
        "command": command,
        "image_mtime_ns": key[3],
        "image_size_bytes": key[4],
        "help_arg": help_arg,
        "returncode": returncode,
        "raw_help_text": raw_help_text,
    }
    if ignored:
        row["ignored"] = True
    if ignore_reason:
        row["ignore_reason"] = ignore_reason
    if attempts:
        row["attempts"] = attempts
    if ignore_reason == "removed":
        row["removed"] = True
    return row


def harvest_command_help(
    container: dict[str, Any],
    command: str,
    cache: dict[tuple[str, str, str, int, int], dict[str, Any]],
) -> dict[str, Any]:
    key = _container_stat_key(container, command)
    cached = cache.get(key)
    if cached is not None:
        cached_raw_help = str(cached.get("raw_help_text") or "")
        if _is_removed_freesurfer_help(cached_raw_help):
            cached["removed"] = True
            return cached
        if cached.get("raw_help_text"):
            reason = _help_failure_reason(cached_raw_help)
            if reason is None:
                return cached
            cached["ignored"] = True
            cached["ignore_reason"] = reason
            cached["raw_help_text"] = None
            return cached
        if cached.get("removed"):
            return cached

    image = Path(str(container["image_path"]))
    timeout_s = _env_int("NEUROCADE_HELP_TIMEOUT", 10)
    attempts: list[dict[str, Any]] = []
    for help_arg in HELP_PROBE_ARGS:
        try:
            completed = _run_container_text(image, [command, help_arg], timeout_s=timeout_s)
        except (OSError, SubprocessError, TimeoutError):
            attempts.append({"help_arg": help_arg, "reason": "error"})
            continue
        raw_help = _strip_ansi(((completed.stdout or "") + "\n" + (completed.stderr or "")).strip())
        reason = _help_failure_reason(raw_help)
        if reason is not None:
            attempts.append(
                {
                    "help_arg": help_arg,
                    "reason": reason,
                    "returncode": completed.returncode,
                    "output_preview": raw_help[:500],
                }
            )
            if reason == "removed":
                row = _help_cache_row(
                    key,
                    command,
                    help_arg=help_arg,
                    returncode=completed.returncode,
                    raw_help_text=raw_help,
                    ignored=True,
                    ignore_reason=reason,
                    attempts=attempts,
                )
                cache[key] = row
                return row
            continue
        row = _help_cache_row(
            key,
            command,
            help_arg=help_arg,
            returncode=completed.returncode,
            raw_help_text=raw_help,
        )
        cache[key] = row
        return row

    ignore_reason = attempts[-1]["reason"] if attempts else "not_help"
    row = _help_cache_row(
        key,
        command,
        help_arg=None,
        raw_help_text=None,
        ignored=True,
        ignore_reason=str(ignore_reason),
        attempts=attempts,
    )
    cache[key] = row
    return row


def _ignored_command_row(container: dict[str, Any], command: str, help_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": command,
        "app": container.get("app"),
        "runtime_version": container.get("runtime_version"),
        "build_date": container.get("build_date"),
        "image_name": container.get("image_name"),
        "image_path": container.get("image_path"),
        "container_command": command,
        "ignore_reason": help_row.get("ignore_reason") or ("removed" if help_row.get("removed") else "not_help"),
        "attempts": help_row.get("attempts") or [],
    }


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def _parse_argument_line(line: str) -> dict[str, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    option_match = re.match(r"^((?:-{1,2}[\w][\w.-]*(?:[ =][^\s,]+)?)(?:,\s*-{1,2}[\w][\w.-]*(?:[ =][^\s,]+)?)*)\s{2,}(.+)$", stripped)
    if option_match:
        return {"name": option_match.group(1).strip(), "description": option_match.group(2).strip()}
    positional_match = re.match(r"^([A-Za-z][\w.-]*(?:\s+[A-Z0-9_<>{}\[\]./-]+)?)\s{2,}(.+)$", stripped)
    if positional_match:
        return {"name": positional_match.group(1).strip(), "description": positional_match.group(2).strip()}
    return None


def _section_name(line: str) -> str | None:
    stripped = line.strip().rstrip(":").lower()
    if stripped in {"options", "optional arguments", "arguments", "positional arguments", "required arguments", "outputs", "output"}:
        return stripped
    return None


def parse_help_text(command: str, raw_help_text: str | None) -> dict[str, Any]:
    """Parse lightweight command help into fields useful for LLM tool search."""
    raw_help = _strip_ansi(raw_help_text or "").strip()
    if not raw_help:
        return {
            "synopsis": command,
            "description": f"{command} from installed container.",
            "arguments": [],
            "outputs": [],
            "raw_help_text": None,
        }

    lines = [line.rstrip() for line in raw_help.splitlines()]
    nonempty = [line.strip() for line in lines if line.strip()]
    synopsis = command
    for index, line in enumerate(nonempty):
        lowered = line.lower()
        if lowered.startswith(("usage:", "synopsis:")):
            synopsis = line
            break
        if lowered == "synopsis" and index + 1 < len(nonempty):
            synopsis = nonempty[index + 1]
            break
        if _looks_like_invocation_line(line):
            synopsis = line
            break

    description_lines: list[str] = []
    for line in nonempty:
        lowered = line.lower()
        if lowered.startswith(("usage:", "synopsis:")) or lowered in {"usage", "synopsis"}:
            continue
        if _section_name(line):
            break
        if line.startswith("-"):
            continue
        description_lines.append(line)
        if len(" ".join(description_lines)) >= 400:
            break
    description = " ".join(description_lines).strip() or f"{command} from installed container."

    arguments: list[dict[str, str]] = []
    outputs: list[dict[str, str]] = []
    current_section: str | None = None
    for line in lines:
        section = _section_name(line)
        if section:
            current_section = section
            continue
        parsed = _parse_argument_line(line)
        if parsed is None:
            continue
        name_text = parsed["name"].lower()
        description_text = parsed["description"].lower()
        if (
            current_section in {"outputs", "output"}
            or any(token in name_text for token in ("output", "outfile", "--out", "--o", "-o "))
            or description_text.startswith(("output", "write", "save"))
        ):
            outputs.append(parsed)
        else:
            arguments.append(parsed)

    return {
        "synopsis": synopsis,
        "description": description,
        "arguments": arguments[:80],
        "outputs": outputs[:40],
        "raw_help_text": raw_help,
    }


def _structured_items_text(items: Any) -> str:
    parts: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                parts.append(" ".join(str(value) for value in item.values() if value))
            elif item:
                parts.append(str(item))
    return " ".join(parts)


def _build_searchable_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("name"),
        row.get("toolbox"),
        row.get("app"),
        row.get("container_command"),
        row.get("runtime_version"),
        row.get("synopsis"),
        row.get("description"),
        row.get("raw_help_text"),
        _structured_items_text(row.get("arguments")),
        _structured_items_text(row.get("outputs")),
    ]
    parts.extend(row.get("aliases") or [])
    parts.extend(row.get("categories") or [])
    return "\n".join(str(part) for part in parts if part)


def _tool_row(container: dict[str, Any], command: str, help_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one installed-tool index row for a container command."""
    app = str(container["app"])
    aliases: list[str] = []
    if app != command:
        aliases.append(app)
    metadata = help_metadata or {}
    row = {
        "name": command,
        "aliases": aliases,
        "toolbox": app,
        "app": app,
        "runtime_version": str(container["runtime_version"]),
        "build_date": container.get("build_date"),
        "image_name": container["image_name"],
        "image_path": container["image_path"],
        "container_command": command,
        "source_path": "generated:neurocade-containers",
        "recipe_path": None,
        "release_path": None,
        "categories": ["core"] if container["kind"] == "core" else ["neurocontainer"],
        "synopsis": metadata.get("synopsis") or command,
        "description": metadata.get("description") or f"{command} from installed {app} container.",
        "arguments": metadata.get("arguments") or [],
        "outputs": metadata.get("outputs") or [],
        "raw_help_text": metadata.get("raw_help_text"),
    }
    row["searchable_text"] = _build_searchable_text(row)
    return row


def build_installed_tool_rows(
    containers: Iterable[dict[str, Any]],
    *,
    harvest_help: bool = True,
    root: Path | None = None,
    ignored_rows: list[dict[str, Any]] | None = None,
    progress_label: str | None = None,
) -> list[dict[str, Any]]:
    """Build installed-tool rows from scanned containers and harvested help."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    cache_path = help_cache_path(root)
    help_cache = _load_help_cache(cache_path) if harvest_help else {}
    max_indexed_commands = _env_int("NEUROCADE_MAX_INDEXED_COMMANDS", 500)
    for container in containers:
        indexed_for_container = 0
        commands = list(container.get("commands") or [])
        for command_index, command in enumerate(commands, start=1):
            if progress_label and (command_index == 1 or command_index % 25 == 0 or command_index == len(commands)):
                print(
                    f"[{progress_label}] probing help {command_index}/{len(commands)} "
                    f"(indexed {indexed_for_container})",
                    flush=True,
                )
            key = (str(command), str(container["image_path"]))
            if key in seen:
                continue
            seen.add(key)
            metadata = None
            if harvest_help:
                help_row = harvest_command_help(container, str(command), help_cache)
                if help_row.get("removed") or help_row.get("ignored") or not help_row.get("raw_help_text"):
                    if ignored_rows is not None:
                        ignored_rows.append(_ignored_command_row(container, str(command), help_row))
                    continue
                metadata = parse_help_text(str(command), str(help_row["raw_help_text"]))
            rows.append(_tool_row(container, str(command), metadata))
            indexed_for_container += 1
            if indexed_for_container >= max_indexed_commands:
                break
    if harvest_help:
        _write_help_cache(cache_path, help_cache)
    return sorted(rows, key=lambda row: (row["name"], row["app"], row["runtime_version"]))


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text through a temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write JSONL rows through an atomic text replace."""
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    _atomic_write_text(path, content)


@contextmanager
def _index_lock(root: Path | None = None):
    """Serialize index refresh and merge operations across processes."""
    lock_path = index_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"waiting for another tool index update to finish: {lock_path}", flush=True)
            fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL rows, skipping blank or malformed lines."""
    if not path.exists():
        return []
    return list(_iter_jsonl_dicts(path))


def _iter_jsonl_dicts(path: Path) -> Iterable[dict[str, Any]]:
    """Yield JSONL object rows, skipping blank, malformed, or non-object lines."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            yield parsed


def _container_index_matches(container: dict[str, Any], metadata: dict[str, Any]) -> bool:
    """Return whether a per-container sidecar belongs to the current image file."""
    return (
        metadata.get("schema_version") == CONTAINER_INDEX_SCHEMA_VERSION
        and str(metadata.get("image_path") or "") == str(container.get("image_path") or "")
        and str(metadata.get("image_name") or "") == str(container.get("image_name") or "")
        and int(metadata.get("image_mtime_ns") or 0) == int(container.get("image_mtime_ns") or 0)
        and int(metadata.get("image_size_bytes") or 0) == int(container.get("image_size_bytes") or 0)
    )


def _load_container_index(container: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Load one current per-container index, or return None if missing/stale."""
    meta_path = container_index_meta_path(container)
    tools_path = container_tool_index_path(container)
    ignored_path = container_ignored_commands_path(container)
    if not meta_path.exists() or not tools_path.exists():
        return None
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict) or not _container_index_matches(container, metadata):
        return None
    return metadata, _load_jsonl(tools_path), _load_jsonl(ignored_path)


def _container_index_metadata(container: dict[str, Any], tools: list[dict[str, Any]], ignored: list[dict[str, Any]]) -> dict[str, Any]:
    """Build metadata for a per-container tool index."""
    return {
        "schema_version": CONTAINER_INDEX_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "image_path": container.get("image_path"),
        "image_name": container.get("image_name"),
        "image_mtime_ns": container.get("image_mtime_ns"),
        "image_size_bytes": container.get("image_size_bytes"),
        "command_count": len(container.get("commands") or []),
        "tool_count": len(tools),
        "ignored_count": len(ignored),
        "container": container,
    }


def _prebuilt_index_dir(container: dict[str, Any]) -> Path:
    """Return the expected prebuilt index directory for a container row."""
    image_name = str(container.get("image_name") or "")
    return PREBUILT_INDEX_ROOT / image_name.removesuffix(".sif").removesuffix(".simg")


def _prebuilt_identity_matches(container: dict[str, Any], identity: dict[str, Any]) -> bool:
    """Return whether a prebuilt index belongs to the current container spec."""
    for key in ("name", "app", "runtime_version", "build_date", "image_name"):
        if key not in identity or identity[key] != container.get(key):
            return False
    return True


def _prebuilt_tool_row(container: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    """Materialize one prebuilt tool template for the local container image."""
    command = str(template.get("container_command") or template.get("name") or "").strip()
    if not command:
        raise ValueError("prebuilt tool row is missing name/container_command")
    metadata = {
        "synopsis": template.get("synopsis") or command,
        "description": template.get("description"),
        "arguments": template.get("arguments") or [],
        "outputs": template.get("outputs") or [],
        "raw_help_text": template.get("raw_help_text"),
    }
    row = _tool_row(container, command, metadata)
    if template.get("aliases") is not None:
        row["aliases"] = list(template.get("aliases") or [])
    if template.get("categories") is not None:
        row["categories"] = list(template.get("categories") or [])
    row["source_path"] = str(_prebuilt_index_dir(container) / "tool_index.jsonl")
    row["searchable_text"] = _build_searchable_text(row)
    return row


def install_prebuilt_container_index(container: dict[str, Any]) -> dict[str, Any] | None:
    """Install a matching prebuilt per-container index beside the local image."""
    index_dir = _prebuilt_index_dir(container)
    identity_path = index_dir / "identity.json"
    tools_path = index_dir / CONTAINER_TOOLS_JSONL
    ignored_path = index_dir / CONTAINER_IGNORED_COMMANDS_JSONL
    if not identity_path.is_file() or not tools_path.is_file():
        return None
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(identity, dict) or not _prebuilt_identity_matches(container, identity):
        return None

    row = dict(container)
    tool_templates = _load_jsonl(tools_path)
    tools = sorted(
        [_prebuilt_tool_row(row, template) for template in tool_templates],
        key=lambda item: (str(item["name"]), str(item["app"]), str(item["runtime_version"])),
    )
    row["commands"] = [str(tool["container_command"]) for tool in tools]
    ignored = _load_jsonl(ignored_path)
    _atomic_write_jsonl(container_tool_index_path(row), tools)
    _atomic_write_jsonl(container_ignored_commands_path(row), ignored)
    metadata = _container_index_metadata(row, tools, ignored)
    metadata["prebuilt_index"] = str(index_dir)
    _atomic_write_text(container_index_meta_path(row), json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    label = str(row.get("name") or row.get("app") or row.get("image_name"))
    print(f"[{label}] installed prebuilt index with {len(tools)} tool row(s)", flush=True)
    return row


def refresh_container_index(
    container: dict[str, Any],
    *,
    root: Path | None = None,
    harvest_help: bool = True,
    use_prebuilt_index: bool = True,
) -> dict[str, Any]:
    """Rebuild one installed container's command and tool sidecar indexes."""
    row = dict(container)
    label = str(row.get("name") or row.get("app") or row.get("image_name"))
    if use_prebuilt_index:
        prebuilt = install_prebuilt_container_index(row)
        if prebuilt is not None:
            return prebuilt
    print(f"[{label}] discovering commands", flush=True)
    row["commands"] = discover_container_commands(row)
    print(f"[{label}] discovered {len(row.get('commands') or [])} command(s)", flush=True)
    ignored_tools: list[dict[str, Any]] = []
    tools = build_installed_tool_rows(
        [row],
        harvest_help=harvest_help,
        root=root,
        ignored_rows=ignored_tools,
        progress_label=label if harvest_help else None,
    )
    _atomic_write_jsonl(container_tool_index_path(row), tools)
    _atomic_write_jsonl(container_ignored_commands_path(row), sorted(ignored_tools, key=lambda item: str(item["name"])))
    metadata = _container_index_metadata(row, tools, ignored_tools)
    _atomic_write_text(container_index_meta_path(row), json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    print(f"[{label}] wrote {len(tools)} tool row(s), ignored {len(ignored_tools)} command(s)", flush=True)
    return row


def merge_container_indexes(*, root: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge current per-container sidecar indexes into the global catalog files."""
    repo_root = find_repo_root(root)
    containers: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    ignored_tools: list[dict[str, Any]] = []
    missing_or_stale: list[dict[str, Any]] = []
    for scanned in scan_containers(repo_root):
        loaded = _load_container_index(scanned)
        if loaded is None:
            missing_or_stale.append(scanned)
            continue
        metadata, tool_rows, ignored_rows = loaded
        metadata_container = metadata.get("container")
        indexed_container: dict[str, Any] = metadata_container if isinstance(metadata_container, dict) else scanned
        containers.append(indexed_container)
        tools.extend(tool_rows)
        ignored_tools.extend(ignored_rows)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "containers": sorted(containers, key=lambda row: (str(row["kind"]), str(row["app"]), str(row["runtime_version"]), str(row["image_name"]))),
    }
    _atomic_write_text(inventory_path(repo_root), json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _atomic_write_jsonl(installed_tools_path(repo_root), sorted(tools, key=lambda row: (str(row["name"]), str(row["app"]), str(row["runtime_version"]))))
    _atomic_write_jsonl(ignored_commands_path(repo_root), sorted(ignored_tools, key=lambda row: (str(row["name"]), str(row["app"]))))
    for container in missing_or_stale:
        print(
            f"missing or stale per-container index for {container.get('name')}: "
            f"run ./scripts/containers.sh refresh-index",
            file=sys.stderr,
        )
    return containers, tools, ignored_tools


def refresh_index(*, root: Path | None = None, harvest_help: bool = True, rebuild_index: bool = False) -> None:
    """Refresh container inventory, installed tools, and ignored-command indexes."""
    repo_root = find_repo_root(root)
    scanned = scan_containers(repo_root)
    print(f"refresh-index: rebuilding {len(scanned)} installed container index(es)", flush=True)
    with _index_lock(repo_root):
        for index, container in enumerate(scanned, start=1):
            label = container.get("name") or container.get("app") or container.get("image_name")
            print(f"refresh-index: [{index}/{len(scanned)}] {label}", flush=True)
            refresh_container_index(
                container,
                root=repo_root,
                harvest_help=harvest_help,
                use_prebuilt_index=not rebuild_index,
            )
        print("refresh-index: merging per-container indexes", flush=True)
        containers, tools, ignored_tools = merge_container_indexes(root=repo_root)
    print(f"wrote {len(containers)} container(s) -> {inventory_path(repo_root)}")
    print(f"wrote {len(tools)} tool row(s) -> {installed_tools_path(repo_root)}")
    if harvest_help:
        print(f"wrote {len(ignored_tools)} ignored command row(s) -> {ignored_commands_path(repo_root)}")


def _core_download_jobs(count: int) -> int:
    """Return the bounded parallelism for direct core container downloads."""
    default = min(4, max(1, count))
    return min(count, _env_int(CORE_DOWNLOAD_JOBS_ENV, default))


def _download_direct_core_container(spec: ContainerSpec, target: Path, *, source: str) -> dict[str, Any] | None:
    """Install one core image from its direct URL without invoking Apptainer fallback."""
    if not spec.fileshare_url:
        raise RuntimeError(f"No direct container image source is configured for {spec.name}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    _install_print(f"Installing {spec.name} -> {target}")
    _install_print(f"Installing {spec.name} from direct container image: {spec.fileshare_url}")
    try:
        _download_file(spec.fileshare_url, target, expected_sha256=spec.fileshare_sha256)
    except (CalledProcessError, OSError, RuntimeError) as exc:
        target.unlink(missing_ok=True)
        if source == "fileshare":
            raise
        _install_print(f"Direct container image source unavailable for {spec.name}; falling back to local/upstream source. ({exc})", file=sys.stderr)
        return None
    return _container_row(spec, target)


def _install_core_images(source: str, dry_run: bool, *, include_freesurfer: bool | None = None) -> list[dict[str, Any]]:
    """Install core images, parallelizing only independent direct URL downloads."""
    items: list[tuple[int, ContainerSpec, Path]] = []
    existing_needing_index: list[tuple[int, dict[str, Any]]] = []
    for index, name in enumerate(core_install_plan(include_freesurfer=include_freesurfer)):
        spec = resolve_container_spec(name)
        if spec.requires_license and license_path() is None:
            raise RuntimeError(f"{spec.name} requires a FreeSurfer license. Install neurocade-data/license.txt first.")
        target = default_image_path(spec)
        if target.exists() and not dry_run:
            container = _container_row_without_hash(spec, target)
            print(f"{spec.name} already installed: {target}")
            if _load_container_index(container) is None:
                print(f"{spec.name} local tool index is missing or stale; installing the bundled prebuilt index when available.")
                existing_needing_index.append((index, container))
            continue
        items.append((index, spec, target))

    if dry_run or source == "upstream":
        rows: list[tuple[int, dict[str, Any]]] = []
        for index, spec, _target in items:
            row = _install_container_image(spec, source=source, dry_run=dry_run)
            if row is not None:
                rows.append((index, row))
        rows.extend(existing_needing_index)
        return [row for _index, row in sorted(rows, key=lambda item: item[0])]

    direct_items = [(index, spec, target) for index, spec, target in items if spec.fileshare_url]
    fallback_items = [(index, spec, target) for index, spec, target in items if not spec.fileshare_url]
    installed_rows: list[tuple[int, dict[str, Any]]] = []

    if direct_items:
        jobs = _core_download_jobs(len(direct_items))
        _install_print(f"Installing {len(direct_items)} direct container image(s) with {jobs} download job(s).")
        with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="neurocade-container-download") as executor:
            futures = {
                executor.submit(_download_direct_core_container, spec, target, source=source): (index, spec, target)
                for index, spec, target in direct_items
            }
            for future in as_completed(futures):
                index, spec, target = futures[future]
                row = future.result()
                if row is None:
                    fallback_items.append((index, spec, target))
                else:
                    installed_rows.append((index, row))

    for index, spec, _target in sorted(fallback_items, key=lambda item: item[0]):
        fallback_source = "upstream" if source == "auto" else source
        row = _install_container_image(spec, source=fallback_source, dry_run=dry_run)
        if row is not None:
            installed_rows.append((index, row))

    installed_rows.extend(existing_needing_index)
    return [row for _index, row in sorted(installed_rows, key=lambda item: item[0])]


def prefetch_core(*, dry_run: bool = False, include_freesurfer: bool | None = None) -> None:
    """Download direct core container images selected for core install without indexing."""
    planned = set(core_install_plan(include_freesurfer=include_freesurfer))
    if "freesurfer" in planned and license_path() is None:
        raise RuntimeError("freesurfer requires a FreeSurfer license. Install neurocade-data/license.txt first.")
    items = [
        (index, spec, default_image_path(spec))
        for index, spec in enumerate(CORE_SPECS.values())
        if spec.name in planned and spec.fileshare_url
    ]
    pending = [(index, spec, target) for index, spec, target in items if dry_run or not target.exists()]
    if not pending:
        print("All direct core container images are already present.")
        return
    if dry_run:
        for _index, spec, target in pending:
            print(f"would prefetch {spec.name}: {spec.fileshare_url} -> {target}")
        return

    jobs = _core_download_jobs(len(pending))
    _install_print(f"Prefetching {len(pending)} direct core container image(s) with {jobs} download job(s).")
    with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="neurocade-container-prefetch") as executor:
        futures = {
            executor.submit(_download_direct_core_container, spec, target, source="fileshare"): (index, spec)
            for index, spec, target in pending
        }
        for future in as_completed(futures):
            _index, spec = futures[future]
            future.result()
            _install_print(f"Prefetched {spec.name}")


def install_core(
    *,
    source: str = "auto",
    dry_run: bool = False,
    refresh: bool = True,
    harvest_help: bool = True,
    rebuild_index: bool = False,
    include_freesurfer: bool | None = None,
) -> None:
    """Install the core NeuroCade runtime container set."""
    installed_containers = _install_core_images(source, dry_run, include_freesurfer=include_freesurfer)
    if refresh and not dry_run and installed_containers:
        with _index_lock():
            for container in installed_containers:
                refresh_container_index(
                    container,
                    harvest_help=harvest_help,
                    use_prebuilt_index=not rebuild_index,
                )
            merge_container_indexes()


def list_containers() -> None:
    """Print a compact status line for each core container."""
    for row in container_status()["containers"]:
        status = "installed" if row["installed"] else "missing"
        suffix = " stale-index" if row["stale_index"] else ""
        print(f"{row['name']:12s} {status:10s}{suffix:12s} {row['image_path']}")


def print_status(*, as_json: bool = False) -> None:
    """Print managed container status as text or JSON."""
    status = container_status()
    if as_json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return
    list_containers()
    if status.get("index_generated_at"):
        print(f"index generated: {status['index_generated_at']}")
    print(f"inventory: {status['inventory_path']}")
    print(f"installed tools: {status['installed_tools_path']}")


def print_path(name: str) -> None:
    """Print the installed image path for a managed container."""
    if name in CORE_SPECS:
        print(resolve_core_image(name))
        return
    path = _installed_container_path(name)
    if path is None:
        raise FileNotFoundError(missing_container_message(name, stale_index=True))
    print(path)


def print_search(query: str, *, as_json: bool = False) -> None:
    """Print Docker Hub NeuroContainers search results as text or JSON."""
    repositories = _docker_hub_namespace_repositories(query)
    rows = []
    for repo_name in repositories[:20]:
        app, runtime_version = _parse_neurocontainer_repo(repo_name)
        rows.append(
            {
                "name": app,
                "app": app,
                "runtime_version": runtime_version,
                "repository": f"{NEUROCONTAINERS_NAMESPACE}/{repo_name}",
            }
        )
    if as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    for row in rows:
        print(f"{row['repository']:60s} app={row['app']} version={row['runtime_version']}")


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run the container management CLI."""
    parser = argparse.ArgumentParser(description="Manage NeuroCade runtime containers and generated tool indexes.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install one container or the core set")
    install_parser.add_argument("name", help="Managed name, 'core', or a NeuroContainers app/repository name.")
    install_parser.add_argument("--source", choices=("auto", "fileshare", "upstream"), default="auto")
    install_parser.add_argument("--dry-run", action="store_true")
    install_parser.add_argument("--no-refresh", action="store_true", help="Skip index refresh after this install.")
    install_parser.add_argument("--rebuild-index", action="store_true", help="Force live command discovery instead of using prebuilt indexes.")
    install_parser.add_argument(
        "--with-freesurfer",
        action="store_true",
        default=None,
        help="Include the full licensed FreeSurfer image when installing the core set.",
    )
    install_parser.add_argument(
        "--no-harvest-help",
        action="store_true",
        help="Discover installed commands but skip command help probing during index refresh.",
    )

    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall one managed container")
    uninstall_parser.add_argument("name")
    uninstall_parser.add_argument("--dry-run", action="store_true")
    uninstall_parser.add_argument("--no-refresh", action="store_true", help="Skip index refresh after uninstall.")

    prefetch_parser = subparsers.add_parser("prefetch", help="Prefetch direct container image downloads")
    prefetch_parser.add_argument("name", choices=("core",))
    prefetch_parser.add_argument("--dry-run", action="store_true")
    prefetch_parser.add_argument(
        "--with-freesurfer",
        action="store_true",
        default=None,
        help="Include the full FreeSurfer image in the core prefetch set.",
    )

    subparsers.add_parser("list", help="List managed containers")
    status_parser = subparsers.add_parser("status", help="Show managed container and index status")
    status_parser.add_argument("--json", action="store_true")
    check_parser = subparsers.add_parser("check", help="Check managed container readiness")
    check_parser.add_argument("name", choices=("core",))
    check_parser.add_argument("--fast", action="store_true", help="Use startup-safe checks that avoid hashing image files.")
    check_parser.add_argument(
        "--with-freesurfer",
        action="store_true",
        default=None,
        help="Include the full licensed FreeSurfer image in the core check.",
    )
    refresh_parser = subparsers.add_parser("refresh-index", help="Refresh installed container inventory and tool index")
    refresh_parser.add_argument(
        "--no-harvest-help",
        action="store_true",
        help="Discover installed commands but skip command help probing.",
    )
    refresh_parser.add_argument("--rebuild-index", action="store_true", help="Force live command discovery instead of using prebuilt indexes.")

    path_parser = subparsers.add_parser("path", help="Print an installed core image path")
    path_parser.add_argument("name")

    search_parser = subparsers.add_parser("search", help="Search NeuroContainers in the Docker Hub vnmd namespace")
    search_parser.add_argument("query")
    search_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "install":
        if args.name == "core":
            install_core(
                source=args.source,
                dry_run=args.dry_run,
                refresh=not args.no_refresh,
                harvest_help=not args.no_harvest_help,
                rebuild_index=args.rebuild_index,
                include_freesurfer=args.with_freesurfer,
            )
        else:
            install_container(
                args.name,
                source=args.source,
                dry_run=args.dry_run,
                refresh=not args.no_refresh,
                harvest_help=not args.no_harvest_help,
                rebuild_index=args.rebuild_index,
            )
    elif args.command == "uninstall":
        uninstall_container(args.name, dry_run=args.dry_run, refresh=not args.no_refresh)
    elif args.command == "prefetch":
        prefetch_core(dry_run=args.dry_run, include_freesurfer=args.with_freesurfer)
    elif args.command == "list":
        list_containers()
    elif args.command == "status":
        print_status(as_json=args.json)
    elif args.command == "check":
        if args.name == "core" and args.fast:
            try:
                check_core_fast(include_freesurfer=args.with_freesurfer)
            except FileNotFoundError as exc:
                print(f"Core runtime container fast check failed: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc
            print("Core runtime containers already verified by lightweight startup check.")
        elif args.name == "core":
            missing = [row["name"] for row in container_status()["containers"] if row["planned_by_core_install"] and row["missing_message"]]
            if missing:
                raise FileNotFoundError(f"core container check failed for: {', '.join(missing)}")
            print("Core runtime containers are installed.")
    elif args.command == "refresh-index":
        refresh_index(harvest_help=not args.no_harvest_help, rebuild_index=args.rebuild_index)
    elif args.command == "path":
        print_path(args.name)
    elif args.command == "search":
        print_search(args.query, as_json=args.json)


if __name__ == "__main__":
    main(sys.argv[1:])
