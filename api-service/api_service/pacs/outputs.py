"""Allowlisted output publication and nonclinical, content-bound provenance."""
import hashlib
import json
import math
import re
import shutil

import numpy as np
from nibabel.loadsave import load
from nibabel.spatialimages import SpatialImage

from api_service.pacs.client import PacsError


def fingerprint(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sequence_type(description):
    text = description.upper()
    if re.search(r"T2|FLAIR|STIR|DIFF|DWI|POST|CONTRAST|GAD|(?:^|[^A-Z])(?:CE|GD)(?:$|[^A-Z])|\+\s*C|LOC|SCOUT|MAP", text):
        return "other"
    if re.search(r"T1", text):
        return "T1_candidate"
    return "unknown"


def prepare_outputs(converted, published, remaining_bytes, item):
    # Never promote the converter's directory. Unexpected files stay in staging.
    published.mkdir()
    allowed = {"RepetitionTime", "EchoTime", "FlipAngle", "MagneticFieldStrength", "PhaseEncodingDirection", "EffectiveEchoSpacing", "TotalReadoutTime", "SliceTiming"}
    profiles = {}
    volumes = sorted(p for p in converted.iterdir() if p.name.endswith((".nii", ".nii.gz")))
    if not volumes:
        raise PacsError("no_volume")
    for number, volume in enumerate(volumes):
        if volume.is_symlink() or not volume.is_file():
            raise PacsError("invalid_volume")
        image = load(volume)
        if not isinstance(image, SpatialImage) or image.affine is None or len(image.shape) not in (3, 4) or not np.isfinite(image.affine).all() or abs(np.linalg.det(image.affine[:3, :3])) < 1e-12:
            raise PacsError("invalid_volume")
        # Read every voxel in bounded planes: header-only loading misses truncation.
        if math.prod(image.shape) * image.get_data_dtype().itemsize > remaining_bytes or math.prod(image.shape[:2]) * 8 > 64 * 1024 * 1024:
            raise PacsError("output_size_limit")
        for frame in range(image.shape[3] if len(image.shape) == 4 else 1):
            for index in range(image.shape[2]):
                plane = image.dataobj[:, :, index, frame] if len(image.shape) == 4 else image.dataobj[:, :, index]
                if not np.isfinite(np.asanyarray(plane)).all():
                    raise PacsError("invalid_volume")
        stem = volume.name.removesuffix(".gz").removesuffix(".nii")
        name = f"volume-{number}" + (".nii.gz" if volume.name.endswith(".gz") else ".nii")
        if shutil.disk_usage(published).free < volume.stat().st_size + 512 * 1024 * 1024:
            raise PacsError("disk_space_limit")
        shutil.copyfile(volume, published / name)
        profiles[name] = {"modality": item["modality"], "dimensions": len(image.shape),
                          "sequence": "T1" if item.get("verified_native_t1") else sequence_type(item.get("description", "")),
                          "sequence_verified_by": item.get("verified_by"), "sha256": fingerprint(published / name)}
        for extension in (".json", ".bval", ".bvec"):
            source = converted / (stem + extension)
            if not source.exists():
                continue
            if source.is_symlink() or not source.is_file() or source.stat().st_size > 1024 * 1024:
                raise PacsError("invalid_sidecar")
            target = published / f"volume-{number}{extension}"
            if extension == ".json":
                data = json.loads(source.read_text())
                # Acquisition fields are numeric except the enumerated direction.
                clean = {}
                for key, value in data.items():
                    if key not in allowed:
                        continue
                    if key == "PhaseEncodingDirection":
                        if value in ("i", "i-", "j", "j-", "k", "k-"):
                            clean[key] = value
                    elif isinstance(value, (int, float)) and np.isfinite(value) or isinstance(value, list) and all(isinstance(v, (int, float)) and np.isfinite(v) for v in value):
                        clean[key] = value
                target.write_text(json.dumps(clean))
            else:
                # Gradient sidecars may contain numbers only, not arbitrary text.
                data = source.read_text()
                if not data.split() or not all(np.isfinite(float(v)) for v in data.split()):
                    raise PacsError("invalid_sidecar")
                target.write_text(data)
        if sum(p.stat().st_size for p in published.iterdir()) > remaining_bytes:
            raise PacsError("output_size_limit")
    return profiles
