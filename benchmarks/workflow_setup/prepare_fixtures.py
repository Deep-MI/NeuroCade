"""Prepare pinned real-data fixtures without accessing a NeuroCade application."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from importlib.metadata import version
from pathlib import Path, PurePosixPath

import numpy as np
from nibabel.loadsave import load, save
from nibabel.nifti1 import Nifti1Image
from nibabel.spatialimages import SpatialImage

SAMPLE = {
    "name": "neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
    "url": "https://github.com/Deep-MI/NeuroCade/releases/download/v2026.6.7/neurocade-sample-case-FastSurfer_Rhineland_0000.tar.gz",
    "algorithm": "sha256",
    "checksum": "71814b4687180e10543523bb07292725f4b165acce7d0f9d34148028daa061b7",
}
PROCESSED = {
    "name": "sub_rs_mri_proc.zip",
    "url": "https://zenodo.org/records/19133592/files/sub_rs_mri_proc.zip?download=1",
    "algorithm": "md5",
    "checksum": "5a300bc10b8cf09b7799a2e3087c3220",
}
REQUIRED = ("mri/norm.mgz", "mri/wmparc.mgz", "mri/aseg.mgz", "mri/transforms/talairach.xfm")
SUBJECT_DIRS = {"mri", "surf", "label", "stats", "scripts", "touch"}


def load_volume(path: Path) -> SpatialImage:
    image = load(path)
    if not isinstance(image, SpatialImage) or image.affine is None:
        raise ValueError(f"Not a spatial image: {path}")
    return image


def digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def fetch(cache: Path, spec: dict[str, str]) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / spec["name"]
    if target.exists():
        if not target.is_file() or target.is_symlink() or digest(target, spec["algorithm"]) != spec["checksum"]:
            raise ValueError(f"Cached archive checksum mismatch: {target}; remove it and retry")
        return target
    print(f"Downloading {spec['url']}", flush=True)
    with tempfile.NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            with urllib.request.urlopen(spec["url"], timeout=60) as response:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
            handle.close()
            if digest(temporary, spec["algorithm"]) != spec["checksum"]:
                raise ValueError(f"Downloaded archive checksum mismatch: {spec['name']}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe archive path: {name}")
    return path


def convert_sample(archive: Path, destination: Path) -> dict:
    with tarfile.open(archive, "r:gz") as bundle:
        matches = [m for m in bundle.getmembers()
                   if safe_path(m.name).as_posix().endswith("FastSurfer_Rhineland_0000/mri/001.mgz")]
        if len(matches) != 1 or not matches[0].isfile():
            raise ValueError("Sample archive must contain exactly one regular mri/001.mgz")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "001.mgz"
            stream = bundle.extractfile(matches[0])
            assert stream is not None
            with stream, source.open("wb") as handle:
                shutil.copyfileobj(stream, handle)
            image = load_volume(source)
            values = np.asanyarray(image.dataobj)
            converted = Nifti1Image(values, image.affine)
            converted.header.set_xyzt_units("mm")
            converted.set_qform(image.affine, code=1)
            converted.set_sform(image.affine, code=1)
            save(converted, destination)
            loaded = load_volume(destination)
            assert loaded.affine is not None and image.affine is not None
            if (loaded.shape != image.shape or not np.array_equal(loaded.get_fdata(), image.get_fdata())
                    or not np.allclose(loaded.affine, image.affine, rtol=0, atol=1e-5)
                    or not np.allclose(loaded.header.get_zooms(), image.header.get_zooms())):
                raise ValueError("NIfTI conversion changed values or geometry")
            return {"archive_member": matches[0].name, "source_sha256": digest(source),
                    "shape": [int(v) for v in image.shape], "voxel_size_mm": [float(v) for v in image.header.get_zooms()],
                    "conversion": "NiBabel NIfTI-1; no resampling; voxel values and affine verified"}


def extract_subject(archive: Path, destination: Path) -> str:
    with zipfile.ZipFile(archive) as bundle:
        entries = {safe_path(item.filename): item for item in bundle.infolist()}
        candidates = []
        for path in entries:
            if path.as_posix().endswith("/mri/norm.mgz"):
                root = path.parent.parent
                if all(root / relative in entries for relative in REQUIRED):
                    candidates.append(root)
        if len(candidates) != 1:
            raise ValueError(f"Expected one complete FreeSurfer subject, found {len(candidates)}: {candidates}")
        root = candidates[0]
        selected = [(path.relative_to(root), item) for path, item in entries.items()
                    if path.is_relative_to(root) and len(path.relative_to(root).parts) > 1
                    and path.relative_to(root).parts[0] in SUBJECT_DIRS and not item.is_dir()]
        if sum(item.file_size for _, item in selected) > 20 * 1024**3:
            raise ValueError("Subject exceeds the 20 GiB extraction bound")
        for relative, item in selected:
            # Remove only old target results from the new fixture, never source data.
            name = relative.name.lower()
            if "thalamicnuclei" in name or "thalamic-nuclei" in name or name.startswith("isrunning"):
                continue
            mode = item.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ValueError(f"Non-regular subject member: {item.filename}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(item) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
        reference = load_volume(destination / "mri/norm.mgz")
        for relative in REQUIRED[:3]:
            volume = load_volume(destination / relative)
            assert volume.affine is not None and reference.affine is not None
            if volume.shape != reference.shape or not np.allclose(volume.affine, reference.affine, atol=1e-5, rtol=0):
                raise ValueError(f"Inconsistent subject geometry: {relative}")
        if not (destination / REQUIRED[-1]).read_text().strip():
            raise ValueError("Empty Talairach transform")
        return root.as_posix()


def prepare(cache: Path, output: Path) -> Path:
    if output.exists():
        raise FileExistsError(f"Choose a new output directory: {output}")
    sample = fetch(cache, SAMPLE)
    processed = fetch(cache, PROCESSED)
    output.mkdir(parents=True, exist_ok=False)
    try:
        (output / "blur").mkdir()
        (output / "thalamus").mkdir()
        conversion = convert_sample(sample, output / "blur/001.nii.gz")
        subject = extract_subject(processed, output / "thalamus")
        hashes = {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file()}
        manifest = {"format_version": 1, "sources": [dict(SAMPLE, sha256=digest(sample)), dict(PROCESSED, sha256=digest(processed))],
                    "subject_archive_path": subject, "conversion": conversion, "nibabel_version": version("nibabel"),
                    "numpy_version": np.__version__, "files_sha256": hashes,
                    "prompts": json.loads(Path(__file__).with_name("prompts.json").read_text())}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    except BaseException:
        shutil.rmtree(output)
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/neurocade/workflow-benchmark")
    parser.add_argument("--output", type=Path, required=True, help="New disposable fixture directory; never an application case")
    args = parser.parse_args()
    print(prepare(args.cache.expanduser().resolve(), args.output.expanduser().resolve()))


if __name__ == "__main__":
    main()
