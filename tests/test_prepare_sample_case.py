"""Tests for verified sample-case installation."""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.runtime_tools.prepare_sample_case import install_sample_case


def _archive(path: Path, member_name: str, content: bytes = b"sample") -> str:
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(member_name)
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_install_sample_case_verifies_and_extracts_archive(tmp_path: Path) -> None:
    archive = tmp_path / "sample.tar.gz"
    digest = _archive(archive, "demo/README.md")
    root = tmp_path / "installed"

    target = install_sample_case(
        url=archive.as_uri(),
        name="demo",
        expected_sha256=digest,
        root=root,
    )

    assert target == (root / "demo").resolve()
    assert (target / "README.md").read_bytes() == b"sample"


def test_install_sample_case_rejects_checksum_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "sample.tar.gz"
    _archive(archive, "demo/README.md")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        install_sample_case(
            url=archive.as_uri(),
            name="demo",
            expected_sha256="0" * 64,
            root=tmp_path / "installed",
        )


def test_install_sample_case_rejects_escaping_archive_path(tmp_path: Path) -> None:
    archive = tmp_path / "sample.tar.gz"
    digest = _archive(archive, "../escape.txt")

    with pytest.raises(RuntimeError, match="Unsafe sample archive path"):
        install_sample_case(
            url=archive.as_uri(),
            name="demo",
            expected_sha256=digest,
            root=tmp_path / "installed",
        )
