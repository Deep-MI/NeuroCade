"""Docker container specifications for NeuroCade runtime tools."""

from __future__ import annotations

from dataclasses import dataclass
import re


NEUROCONTAINERS_NAMESPACE = "vnmd"

FREESURFER_COMMANDS = (
    "mri_info",
    "mri_convert",
    "mri_binarize",
    "mri_vol2vol",
    "mri_label2vol",
    "mri_segstats",
)
FASTSURFER_FREESURFER_COMMANDS = FREESURFER_COMMANDS


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    kind: str
    app: str
    runtime_version: str
    build_date: str | None
    command_names: tuple[str, ...]
    docker_uri: str | None = None
    requires_license: bool = False


def _valid_container_command(name: str) -> bool:
    """Return whether a command name is safe to store and invoke."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name or ""))


def _parse_neurocontainer_repo(repo_name: str) -> tuple[str, str]:
    """Split a NeuroContainers repository name into app and runtime version."""
    app, sep, runtime_version = repo_name.rpartition("_")
    if not sep:
        return repo_name, "unknown"
    return app, runtime_version


def _neurocontainer_spec(
    repo_name: str,
    build_date: str,
    *,
    name: str | None = None,
    command_names: tuple[str, ...] | None = None,
    requires_license: bool = False,
) -> ContainerSpec:
    """Build a managed container spec from NeuroContainers metadata."""
    app, runtime_version = _parse_neurocontainer_repo(repo_name)
    inferred_commands = tuple(dict.fromkeys(candidate for candidate in (app, name or "") if _valid_container_command(candidate)))
    return ContainerSpec(
        name=name or app,
        kind="neurocontainer",
        app=app,
        runtime_version=runtime_version,
        build_date=build_date,
        command_names=command_names or inferred_commands or (app,),
        docker_uri=f"docker://{NEUROCONTAINERS_NAMESPACE}/{repo_name}:{build_date}",
        requires_license=requires_license,
    )


CORE_SPECS: dict[str, ContainerSpec] = {
    "fastsurfer": ContainerSpec(
        name="fastsurfer",
        kind="core",
        app="fastsurfer",
        runtime_version="2.4.2",
        build_date="20260115",
        command_names=FASTSURFER_FREESURFER_COMMANDS,
        docker_uri="docker://vnmd/fastsurfer_2.4.2:20260115",
    ),
    "bash_image": ContainerSpec(
        name="bash_image",
        kind="core",
        app="bash_image",
        runtime_version="python-3.12",
        build_date=None,
        command_names=("bash", "python3.12"),
        docker_uri="neurocade-runtime-bash:local",
    ),
    "dcm2niix": _neurocontainer_spec(
        "dcm2niix_v1.0.20240202",
        "20260512",
        name="dcm2niix",
        command_names=("dcm2niix",),
    ),
    "freesurfer": _neurocontainer_spec(
        "freesurfer_8.1.0",
        "20260311",
        name="freesurfer",
        command_names=FREESURFER_COMMANDS,
        requires_license=True,
    ),
}


def container_display_name(name: str) -> str:
    """Return a user-facing label for a managed container name."""
    return {
        "bash_image": "managed bash image",
        "dcm2niix": "dcm2niix container",
        "fastsurfer": "FastSurfer container",
        "freesurfer": "FreeSurfer container",
    }.get(name, f"{name} container")
