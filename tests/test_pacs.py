"""PACS protocol limits, permissions and partial-import recovery."""

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.pacs import client, service, worker

from backend_common.auth import AuthContext
from backend_common.db import Base, Case, PacsImport, RoleEnum, User, Workspace, WorkspaceMembership
from backend_common.settings import Settings


def settings_for_test(**values):
    options: dict[str, Any] = {"_env_file": None, **values}
    return Settings(**options)


@pytest.mark.parametrize("identifier", ["", "1..2", "1/2", "1.02", "a.2", "1" * 65])
def test_invalid_uid(identifier):
    with pytest.raises(client.PacsError):
        client.uid(identifier)


@pytest.mark.parametrize("values", [{}, {"patient_id": "*"}, {"accession": "a?"}, {"date_from": "2026-01-01"}, {"date_from": "2026-01-01", "date_to": "2026-03-01"}])
def test_search_requires_bounded_input(values):
    with pytest.raises(ValidationError):
        service.Search(workspace_id="workspace", **values)


def test_search_accepts_exact_id():
    assert service.Search(workspace_id="workspace", patient_id="patient-123").limit == 50


def test_disabled_pacs_requires_no_external_configuration():
    settings = settings_for_test(pacs_enabled=False)
    client.validate_config(settings)
    assert settings.pacs_base_url == ""
    assert settings.pacs_token_url == ""
    assert settings.pacs_workspace_ids == ""


@pytest.mark.parametrize("url", ["http://pacs.test", "https://user:secret@pacs.test", "https://pacs.test?q=1"])
def test_configuration_rejects_unsafe_endpoint(url):
    settings = Settings(**{"_env_file": None, "pacs_enabled": True, "pacs_base_url": url, "pacs_token_url": "https://id.test/token", "pacs_client_id": "id", "pacs_client_secret": "secret", "pacs_workspace_ids": "workspace"})
    with pytest.raises(ValueError):
        client.validate_config(settings)


class Response:
    status_code = 200

    def __init__(self, body, content_type):
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        for offset in range(0, len(self.body), 3):
            yield self.body[offset:offset + 3]


def test_streams_multipart_split_across_chunks(tmp_path, monkeypatch):
    pacs = client.PacsClient(settings_for_test())
    body = b'--boundary\r\nContent-Type: application/dicom\r\n\r\nDICOMBYTES\r\n--boundary--\r\n'
    monkeypatch.setattr(pacs, "_get", lambda *a, **k: Response(body, 'multipart/related; boundary="boundary"'))
    target = tmp_path / "instance.dcm"
    size, digest = pacs.retrieve("1.2", "1.3", "1.4", target, lambda size: None)
    assert target.read_bytes() == b"DICOMBYTES"
    assert size == len(body)
    assert len(digest) == 64


def test_rejects_truncated_multipart(tmp_path, monkeypatch):
    pacs = client.PacsClient(settings_for_test())
    monkeypatch.setattr(pacs, "_get", lambda *a, **k: Response(b'--boundary\r\nContent-Type: application/dicom\r\n\r\nDATA', 'multipart/related; boundary=boundary'))
    with pytest.raises(client.PacsError, match="truncated_instance"):
        pacs.retrieve("1.2", "1.3", "1.4", tmp_path / "instance.dcm", lambda size: None)


def test_limits_unknown_content_length(tmp_path, monkeypatch):
    pacs = client.PacsClient(Settings(**{"_env_file": None, "pacs_max_instance_bytes": 5}))
    monkeypatch.setattr(pacs, "_get", lambda *a, **k: Response(b"0123456789", "application/dicom"))
    with pytest.raises(client.PacsError, match="instance_size_limit"):
        pacs.retrieve("1.2", "1.3", "1.4", tmp_path / "instance.dcm", lambda size: None)
    assert (tmp_path / "instance.dcm").stat().st_size <= 5


@pytest.fixture
def context(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'pacs.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(service.settings, "pacs_enabled", True)
    monkeypatch.setattr(service.settings, "pacs_workspace_ids", "workspace")
    monkeypatch.setattr(service.settings, "pacs_source_id", "source")
    monkeypatch.setattr(service.settings, "fs_data_root", tmp_path)
    with factory() as db:
        user = User(id="user", email="user@example.org", full_name="Researcher")
        workspace = Workspace(id="workspace", owner_user_id="user", name="research")
        case = Case(id="case", workspace_id="workspace", owner_user_id="user", title="research-case")
        db.add_all([user, workspace, case])
        db.flush()
        db.add(WorkspaceMembership(workspace_id="workspace", user_id="user", role=RoleEnum.user))
        from backend_common.case_storage import ensure_case_storage_layout
        ensure_case_storage_layout(service.settings, case, workspace)
        db.commit()
        yield db, AuthContext(user=user, role=RoleEnum.user, auth_mode="local"), factory


def test_member_allowed_outsider_denied(context):
    db, auth, _ = context
    assert service.authorize(db, auth, "workspace").id == "workspace"
    with pytest.raises(HTTPException) as exc:
        service.authorize(db, auth, "other")
    assert exc.value.status_code == 404


def test_active_import_blocks_case_mutation(context):
    from api_service.cases.service import ensure_case_not_active
    db, _, _ = context
    row = PacsImport(id="import", workspace_id="workspace", case_id="case", user_id="user", source_id="source", study_uid="1.2", submission_key="key", state="running")
    db.add(row)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        ensure_case_not_active(db, db.get(Case, "case"))
    assert exc.value.status_code == 409


def test_recovery_preserves_completed_series(context):
    db, _, _ = context
    row = PacsImport(id="import", workspace_id="workspace", case_id="case", user_id="user", source_id="source", study_uid="1.2", submission_key="key", state="running", series_json=[{"state": "completed"}, {"state": "converting"}])
    db.add(row)
    db.commit()
    worker.recover_imports()
    db.expire_all()
    assert row.state == "interrupted"
    assert [item["state"] for item in row.series_json] == ["completed", "interrupted"]


def test_progress_excludes_instance_manifest(context):
    db, _, _ = context
    row = PacsImport(id="import", workspace_id="workspace", case_id="case", user_id="user", source_id="source", study_uid="1.2", submission_key="key", series_json=[{"state": "completed", "manifest": [{"uid": "1.2.3"}]}])
    db.add(row)
    db.commit()
    assert "manifest" not in service.serialize(row)["series"][0]


@pytest.mark.parametrize("patient_name", ["", "Example^Name=例^名"])
def test_partial_import_retry_and_cleanup(context, monkeypatch, patient_name):
    import numpy as np
    from nibabel.nifti1 import Nifti1Image
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian

    from backend_common.db import Artifact

    db, _, _ = context
    fail = [True]

    class FakeClient:
        def __init__(self, settings):
            pass

        def close(self):
            pass

        def instances(self, study, series):
            return [{"00080018": {"Value": [series + ".1"]}}]

        def retrieve(self, study, series, instance, target, check):
            if series == "1.3" and fail[0]:
                raise client.PacsError("retrieval_failed")
            header = FileDataset(str(target), {}, file_meta=FileMetaDataset(), preamble=b"\0" * 128)
            header.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
            header.StudyInstanceUID = study
            header.SeriesInstanceUID = series
            header.SOPInstanceUID = instance
            header.SOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
            header.SpecificCharacterSet = "ISO_IR 192"
            header.PatientName = patient_name
            header.save_as(target)
            check(target.stat().st_size)
            return target.stat().st_size, "checksum"

    def convert(incoming, output):
        Nifti1Image(np.zeros((3, 3, 3), dtype=np.float32), np.eye(4)).to_filename(output / "scan.nii.gz")
        (output / "scan.json").write_text('{"PatientName":"SECRET", "RepetitionTime":2.0}')
        (output / "scan.bval").write_text("0")

    monkeypatch.setattr(worker, "PacsClient", FakeClient)
    monkeypatch.setattr(worker, "run_dcm2niix", convert)
    row = PacsImport(id="import", workspace_id="workspace", case_id="case", user_id="user", source_id="source", study_uid="1.1", submission_key="key", state="queued",
                     provenance_json={"patient_name": patient_name.split("=")[0]},
                     series_json=[{"series_uid": "1.2", "state": "queued", "modality": "MR"}, {"series_uid": "1.3", "state": "queued", "modality": "MR"}])
    db.add(row)
    db.commit()
    worker.run_import("import")
    db.expire_all()
    assert row.state == "completed_with_errors"
    assert db.query(Artifact).count() == 3
    from backend_common.case_storage import case_storage_dir
    case_dir = case_storage_dir(service.settings, "workspace", "case")
    assert "SECRET" not in (case_dir / "pacs-series-0/volume-0.json").read_text()
    assert not list(service.settings.fs_data_root.rglob("*.dcm"))
    fail[0] = False
    row.state = "queued"
    db.commit()
    worker.run_import("import")
    db.expire_all()
    assert row.state == "completed"
    assert db.query(Artifact).count() == 6
    assert not list(service.settings.fs_data_root.rglob("*.dcm"))


def test_migration_upgrades_and_downgrades_isolated_database(tmp_path):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    def migration(name):
        spec = importlib.util.spec_from_file_location("migration", ROOT / "migrations/versions" / name)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    initial = migration("20260814_000001_initial_app_schema.py")
    pacs = migration("20260909_000001_pacs_imports.py")
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        initial.upgrade()
        pacs.upgrade()
        assert "pacs_imports" in inspect(connection).get_table_names()
        assert {column["name"] for column in inspect(connection).get_columns("pacs_imports")} >= {"provenance_json", "series_json", "submission_key"}
        pacs.downgrade()
        assert "pacs_imports" not in inspect(connection).get_table_names()


def test_oauth_tokens_cached_and_refreshed(monkeypatch):
    calls = []

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            calls.append(kwargs)
            return Response(b'{"access_token":"synthetic-token","token_type":"Bearer","expires_in":3600}', "application/json")

    monkeypatch.setattr(client.requests, "Session", Session)
    provider = client.TokenProvider()
    settings = settings_for_test(pacs_token_url="https://id.test/token", pacs_client_id="client", pacs_client_secret="secret")
    assert provider.get(settings) == "synthetic-token"
    assert provider.get(settings) == "synthetic-token"
    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["verify"] is True
    provider.get(settings, refresh=True)
    assert len(calls) == 2


def test_revoked_membership_stops_worker(context):
    db, _, _ = context
    row = PacsImport(id="import", workspace_id="workspace", case_id="case", user_id="user", source_id="source", study_uid="1.2", submission_key="key")
    db.add(row)
    db.query(WorkspaceMembership).delete()
    db.commit()
    with pytest.raises(client.PacsError, match="access_revoked"):
        worker.check_access(db, row)
