"""Offline fixtures for the downloadable real-data benchmark preparation."""
import json
import stat
import tarfile
import zipfile
from typing import Any, cast

import numpy as np
import pytest
from nibabel.freesurfer.mghformat import MGHImage
from nibabel.loadsave import save
from nibabel.nifti1 import Nifti1Image

from benchmarks.workflow_setup import prepare_fixtures as prepare


@pytest.fixture
def archives(tmp_path, monkeypatch):
    image = tmp_path / "source.mgz"
    affine = np.array([[0, -0.8, 0, 20], [0.8, 0, 0, -30], [0, 0, 0.8, 10], [0, 0, 0, 1]])
    save(MGHImage(cast(Any, np.arange(120, dtype=np.float32).reshape(4, 5, 6)), affine), image)
    sample = tmp_path / "sample.tar.gz"
    with tarfile.open(sample, "w:gz") as bundle:
        bundle.add(image, arcname="FastSurfer_Rhineland_0000/mri/001.mgz")
    processed = tmp_path / "proc.zip"
    with zipfile.ZipFile(processed, "w") as bundle:
        for name in prepare.REQUIRED[:3]:
            bundle.write(image, "sub_rs_mri_proc/freesurfer6_pipeline/subject/" + name)
        bundle.writestr("sub_rs_mri_proc/freesurfer6_pipeline/subject/" + prepare.REQUIRED[-1], "transform")
        bundle.writestr("sub_rs_mri_proc/freesurfer6_pipeline/subject/mri/ThalamicNuclei.v13.T1.mgz", "stale")
        bundle.writestr("sub_rs_mri_proc/freesurfer6_pipeline/subject/stats/thalamic-nuclei.lh.v13.T1.stats", "stale")
        bundle.writestr("sub_rs_mri_proc/other_pipeline/unrelated", "ignored")
    for key, path in [("SAMPLE", sample), ("PROCESSED", processed)]:
        monkeypatch.setattr(prepare, key, dict(name=path.name, url=path.as_uri(), algorithm="sha256", checksum=prepare.digest(path)))
    return sample, processed


def test_prepare_download_convert_extract_and_reuse_cache(archives, tmp_path, monkeypatch):
    before = [prepare.digest(p) for p in archives]
    cache = tmp_path / "cache"
    output = prepare.prepare(cache, tmp_path / "first")
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(manifest["files_sha256"]) == 5
    assert manifest["conversion"]["shape"] == [4, 5, 6]
    assert Nifti1Image.from_filename(output / "blur/001.nii.gz").header.get_xyzt_units()[0] == "mm"
    assert not list(output.rglob("*ThalamicNuclei*"))
    assert not list(output.rglob("*thalamic-nuclei*"))
    for name, expected in manifest["files_sha256"].items():
        assert prepare.digest(output / name) == expected
    def no_download(*args, **kwargs):
        pytest.fail("Verified cache should work offline")
    monkeypatch.setattr(prepare.urllib.request, "urlopen", no_download)
    second = prepare.prepare(cache, tmp_path / "second")
    assert Nifti1Image.from_filename(second / "blur/001.nii.gz").shape == (4, 5, 6)
    assert json.loads((second / "manifest.json").read_text())["files_sha256"] == manifest["files_sha256"]
    assert [prepare.digest(p) for p in archives] == before
    with pytest.raises(FileExistsError):
        prepare.prepare(cache, output)


def test_checksum_failure_does_not_publish_cache(archives, tmp_path):
    spec = dict(prepare.SAMPLE, checksum="0" * 64)
    with pytest.raises(ValueError, match="checksum mismatch"):
        prepare.fetch(tmp_path / "cache", spec)
    assert list((tmp_path / "cache").iterdir()) == []


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../escape", "a\\escape"])
def test_unsafe_paths_rejected(name):
    with pytest.raises(ValueError, match="Unsafe"):
        prepare.safe_path(name)


def test_zip_symlink_rejected(archives, tmp_path):
    archive = archives[1]
    with zipfile.ZipFile(archive, "a") as bundle:
        link = zipfile.ZipInfo("sub_rs_mri_proc/freesurfer6_pipeline/subject/mri/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "/outside")
    with pytest.raises(ValueError, match="Non-regular"):
        prepare.extract_subject(archive, tmp_path / "subject")


def test_ambiguous_subject_rejected(archives, tmp_path):
    archive = archives[1]
    with zipfile.ZipFile(archive, "a") as bundle:
        for name in prepare.REQUIRED:
            bundle.writestr("second/" + name, "dummy")
    with pytest.raises(ValueError, match="found 2"):
        prepare.extract_subject(archive, tmp_path / "subject")


def test_tar_link_rejected(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        link = tarfile.TarInfo("FastSurfer_Rhineland_0000/mri/001.mgz")
        link.type = tarfile.SYMTYPE
        link.linkname = "/outside"
        bundle.addfile(link)
    with pytest.raises(ValueError, match="regular"):
        prepare.convert_sample(archive, tmp_path / "output.nii.gz")


def test_corrupt_cached_archive_is_not_reused(archives, tmp_path):
    cache = tmp_path / 'cache'
    downloaded = prepare.fetch(cache, prepare.SAMPLE)
    downloaded.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='Cached archive checksum mismatch'):
        prepare.fetch(cache, prepare.SAMPLE)


def test_extraction_failure_removes_only_new_output(archives, tmp_path, monkeypatch):
    def fail(*args):
        raise ValueError('bad archive')
    monkeypatch.setattr(prepare, 'extract_subject', fail)
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='bad archive'):
        prepare.prepare(tmp_path / 'cache', output)
    assert not output.exists()
    assert all(path.exists() for path in archives)
