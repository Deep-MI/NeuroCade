"""Managed container specifications for NeuroCade runtime tools."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re


GITHUB_RELEASE_CONTAINER_BASE_URL = "https://github.com/ClePol/NeuroCade/releases"
NEUROCONTAINERS_SINGULARITY_BASE_URL = "https://neurocontainers.neurodesk.workers.dev"
DOCKER_HUB_API_BASE = "https://hub.docker.com"
NEUROCONTAINERS_NAMESPACE = "vnmd"
NEUROCONTAINER_BUILD_TAG_PATTERN = r"\d{8}"


def github_release_asset_url(filename: str) -> str:
    """Return the GitHub release asset URL for a managed NeuroCade container."""
    tag = os.environ.get("NEUROCADE_CONTAINER_RELEASE_TAG", "latest").strip() or "latest"
    base_url = GITHUB_RELEASE_CONTAINER_BASE_URL.rstrip("/")
    if tag == "latest":
        return f"{base_url}/latest/download/{filename}"
    return f"{base_url}/download/{tag}/{filename}"

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
    image_name: str
    command_names: tuple[str, ...]
    fileshare_url: str | None = None
    fileshare_sha256: str | None = None
    docker_uri: str | None = None
    build_file: str | None = None
    requires_license: bool = False

    @property
    def directory_name(self) -> str:
        """Return the image directory name without its container suffix."""
        return self.image_name.removesuffix(".sif").removesuffix(".simg")


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
    image_name = f"{repo_name}_{build_date}.simg"
    inferred_commands = tuple(dict.fromkeys(candidate for candidate in (app, name or "") if _valid_container_command(candidate)))
    return ContainerSpec(
        name=name or app,
        kind="neurocontainer",
        app=app,
        runtime_version=runtime_version,
        build_date=build_date,
        image_name=image_name,
        command_names=command_names or inferred_commands or (app,),
        fileshare_url=f"{NEUROCONTAINERS_SINGULARITY_BASE_URL}/{image_name}",
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
        image_name="fastsurfer_2.4.2_20260115.simg",
        command_names=FASTSURFER_FREESURFER_COMMANDS,
        fileshare_url=f"{NEUROCONTAINERS_SINGULARITY_BASE_URL}/fastsurfer_2.4.2_20260115.simg",
        docker_uri="docker://vnmd/fastsurfer_2.4.2:20260115",
    ),
    "bash_image": ContainerSpec(
        name="bash_image",
        kind="core",
        app="bash_image",
        runtime_version="python-3.12",
        build_date=None,
        image_name="bash-image-python-3.12.sif",
        command_names=("bash", "python3.12"),
        fileshare_url=github_release_asset_url("bash-image-python-3.12.sif"),
        build_file="packages/neurocade-runtime-tools/src/neurocade_runtime_tools/bash_python_image/Buildfile",
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
