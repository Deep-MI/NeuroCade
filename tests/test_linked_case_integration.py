"""Integration coverage for host-linked case sources."""

import asyncio
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import cast

import pytest
from api_service.cases import files as files_module
from api_service.cases import operations as operations_module
from api_service.cases import run_operations as run_operations_module
from api_service.cases.operations import create_case_from_linked_source, delete_case_for_user
from api_service.mcp_adapter import transfers as mcp_transfers
from api_service.routers import artifacts as artifacts_module
from api_service.routers.artifacts import download_artifact, download_case_archive, download_case_file, list_files
from api_service.schemas import LinkedCaseCreateRequest, StartRunRequest, WorkflowInputFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend_common import storage as storage_module
from backend_common.case_storage import case_storage_dir, ensure_workspace_storage_layout
from backend_common.db import Artifact, ArtifactKind, Base, Case, LinkedCaseSource, LinkedSourceClaim
from tests.factories import seed_workspace_context


class FakeBridge:
    source = b"linked-volume"
    contents = {
        "mri/orig.mgz": source,
        "mri/aparc+aseg.mgz": b"s" * 20,
        "stats/aseg.stats": b"t" * 30,
        "notes.bin": b"note",
    }

    def __init__(self):
        self.revoked: list[str] = []

    def linked_source_manifest(self, source_id: str):
        assert source_id == "source-1"
        return {
            "source_id": source_id,
            "name": "subject-01",
            "truncated": False,
            "entries": [
                {"path": "mri/orig.mgz", "type": "file", "size": len(self.source)},
                {"path": "mri/aparc+aseg.mgz", "type": "file", "size": 20},
                {"path": "stats/aseg.stats", "type": "file", "size": 30},
                {"path": "notes.bin", "type": "file", "size": 4},
            ],
        }

    def read_linked_source(self, source_id: str, relative_path: str, *, offset: int = 0, length: int = 1):
        assert source_id == "source-1"
        content = self.contents[relative_path]
        return content[offset : offset + length], len(content)

    def revoke_linked_source(self, source_id: str) -> bool:
        self.revoked.append(source_id)
        return True


class SubjectsBridge(FakeBridge):
    def linked_source_manifest(self, source_id: str):
        assert source_id == "source-1"
        return {
            "source_id": source_id,
            "name": "subjects",
            "truncated": False,
            "entries": [
                {"path": f"{subject}/mri/orig.mgz", "type": "file", "size": len(self.source)}
                for subject in ("subject-a", "subject-b")
            ] + [
                {"path": f"{subject}/stats/aseg.stats", "type": "file", "size": 30}
                for subject in ("subject-a", "subject-b")
            ],
        }


@pytest.fixture()
def linked_case(tmp_path: Path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    context, workspace, _cases = seed_workspace_context(db, workspace_id="workspace-linked")
    data_root = tmp_path / "data"
    monkeypatch.setattr(operations_module.settings, "fs_data_root", data_root)
    monkeypatch.setattr(storage_module.settings, "fs_data_root", data_root)
    ensure_workspace_storage_layout(operations_module.settings, workspace)
    bridge = FakeBridge()
    monkeypatch.setattr(operations_module.BridgeClient, "from_environment", lambda: bridge)
    monkeypatch.setattr(artifacts_module.BridgeClient, "from_environment", lambda: bridge)
    monkeypatch.setattr(files_module.BridgeClient, "from_environment", lambda: bridge)
    response = create_case_from_linked_source(
        db,
        context,
        request=LinkedCaseCreateRequest(workspace_id=workspace.id, source_id="source-1", title="subject-01"),
    )
    try:
        yield db, context, response
    finally:
        db.close()


def test_linked_subject_is_indexed_without_copying_and_gets_default_layers(linked_case):
    db, _context, response = linked_case
    source = db.query(LinkedCaseSource).filter_by(case_id=response.case_id).one()
    artifacts = db.query(Artifact).filter_by(case_id=response.case_id).all()

    assert source.source_id == "source-1"
    claim = db.get(LinkedSourceClaim, "source-1")
    assert claim is not None
    assert claim.workspace_id == "workspace-linked"
    assert source.format == "freesurfer"
    assert response.import_summary["transfer"] == "linked"
    assert {artifact.kind for artifact in artifacts} == {ArtifactKind.volume, ArtifactKind.report}
    assert response.import_summary["file_count"] == 4
    assert response.import_summary["indexed_file_count"] == 3
    assert {artifact.name for artifact in artifacts} == {"orig.mgz", "aparc+aseg.mgz", "aseg.stats"}
    assert {layer["type"] for layer in response.default_layers} == {"intensity", "segmentation"}
    assert all(not (operations_module.settings.outputs_dir / artifact.relative_path).exists() for artifact in artifacts)


def test_linked_artifact_download_supports_byte_ranges(linked_case):
    db, context, response = linked_case
    artifact = db.query(Artifact).filter_by(case_id=response.case_id, name="orig.mgz").one()
    result = download_artifact(artifact.id, range_header="bytes=2-7", db=db, context=context)

    async def collect() -> bytes:
        return b"".join([chunk async for chunk in result.body_iterator])

    assert result.status_code == 206
    assert result.headers["content-range"] == f"bytes 2-7/{len(FakeBridge.source)}"
    assert asyncio.run(collect()) == FakeBridge.source[2:8]


def test_linked_artifact_serialization_contains_only_semantic_metadata(linked_case):
    db, context, response = linked_case
    summaries = artifacts_module.list_case_artifacts(response.case_id, db=db, context=context)
    metadata = next(item.metadata for item in summaries if item.name == "orig.mgz")

    assert metadata["source"] == "linked"
    assert "linked_source_id" not in metadata
    assert "linked_relative_path" not in metadata
    assert "linked_source_relative" not in metadata


def test_linked_file_listing_includes_unindexed_files(linked_case):
    db, context, response = linked_case

    entries = list_files(response.case_id, db=db, context=context)

    by_path = {(entry.source, entry.path): entry for entry in entries}
    assert by_path[("managed", ".")].type == "directory"
    assert by_path[("linked", ".")].type == "directory"
    assert ("linked", "notes.bin") in by_path
    assert by_path[("linked", "notes.bin")].size_bytes == 4
    assert db.query(Artifact).filter_by(case_id=response.case_id, name="notes.bin").one_or_none() is None


def test_linked_case_file_download_does_not_require_artifact_row(linked_case, monkeypatch):
    db, context, response = linked_case
    bridge = cast(FakeBridge, files_module.BridgeClient.from_environment())

    def read(source_id: str, relative_path: str, *, offset: int = 0, length: int = 1):
        assert source_id == "source-1"
        assert relative_path == "notes.bin"
        content = b"note"
        return content[offset : offset + length], len(content)

    monkeypatch.setattr(bridge, "read_linked_source", read)
    result = download_case_file(response.case_id, source="linked", path="notes.bin", db=db, context=context)

    async def collect() -> bytes:
        return b"".join([chunk async for chunk in result.body_iterator])

    assert asyncio.run(collect()) == b"note"


def test_linked_case_archive_includes_unindexed_files(linked_case):
    db, context, response = linked_case

    result = download_case_archive(response.case_id, db=db, context=context)
    archive_path = Path(result.path)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.read(f"{response.title}/notes.bin") == b"note"


def test_mcp_linked_case_archive_includes_unindexed_files(linked_case, monkeypatch):
    db, context, response = linked_case
    case = db.get(Case, response.case_id)
    assert case is not None
    case_dir = case_storage_dir(operations_module.settings, case.workspace_id, case.id)
    monkeypatch.setattr(mcp_transfers, "get_settings", lambda: operations_module.settings)

    result = mcp_transfers.archive_case(db, SimpleNamespace(id="mcp-client"), context, case, case_dir)

    with zipfile.ZipFile(Path(result.path)) as archive:
        assert archive.read("notes.bin") == b"note"


def test_linked_source_claim_cannot_be_reused_in_another_workspace(linked_case):
    db, _context, _response = linked_case
    other_context, other_workspace, _cases = seed_workspace_context(
        db,
        user_id="other-user",
        user_email="other@example.com",
        workspace_id="other-workspace",
    )

    with pytest.raises(Exception) as caught:
        create_case_from_linked_source(
            db,
            other_context,
            request=LinkedCaseCreateRequest(workspace_id=other_workspace.id, source_id="source-1", title="stolen"),
        )

    assert getattr(caught.value, "status_code", None) == 404
    assert db.query(Case).filter_by(title="stolen").count() == 0


def test_deleting_final_linked_case_revokes_claim_and_bridge_capability(linked_case):
    db, context, response = linked_case
    bridge = cast(FakeBridge, files_module.BridgeClient.from_environment())

    result = delete_case_for_user(db, context, case_id=response.case_id)

    assert result == {"deleted": response.case_id}
    claim = db.get(LinkedSourceClaim, "source-1")
    assert claim is not None and claim.revoked is True
    assert bridge.revoked == ["source-1"]


def test_subjects_directory_creates_one_case_per_subject_with_scoped_roots(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    context, workspace, _cases = seed_workspace_context(db, workspace_id="workspace-batch-linked")
    data_root = tmp_path / "data"
    monkeypatch.setattr(operations_module.settings, "fs_data_root", data_root)
    monkeypatch.setattr(storage_module.settings, "fs_data_root", data_root)
    ensure_workspace_storage_layout(operations_module.settings, workspace)
    monkeypatch.setattr(operations_module.BridgeClient, "from_environment", lambda: SubjectsBridge())

    response = create_case_from_linked_source(
        db, context, request=LinkedCaseCreateRequest(workspace_id=workspace.id, source_id="source-1")
    )

    assert response.import_summary["scope"] == "subjects-directory"
    assert response.import_summary["created_case_count"] == 2
    assert [item["title"] for item in response.created_cases] == ["subject-a", "subject-b"]
    sources = db.query(LinkedCaseSource).order_by(LinkedCaseSource.source_relative).all()
    assert [(item.source_id, item.source_relative) for item in sources] == [
        ("source-1", "subject-a"), ("source-1", "subject-b")
    ]
    for source in sources:
        artifacts = db.query(Artifact).filter_by(case_id=source.case_id).all()
        assert {item.relative_path for item in artifacts} == {"mri/orig.mgz", "stats/aseg.stats"}
        assert all(item.metadata_json["source"] == "linked" for item in artifacts)

    first_source = sources[0]
    prepared: dict = {}
    submitted: dict = {}
    monkeypatch.setattr(run_operations_module, "resolve_gpu_enabled", lambda *args, **kwargs: False)
    monkeypatch.setattr(run_operations_module, "prepare_workflow", lambda *args, **kwargs: prepared.update({"args": args, **kwargs}))
    monkeypatch.setattr(
        run_operations_module.workflow_runs,
        "submit_workflow_run",
        lambda *args, **kwargs: submitted.update({"args": args, **kwargs}),
    )
    run_operations_module._start_neuroimaging_run(
        db,
        context,
        request=StartRunRequest(
            case_id=first_source.case_id,
            tool_id="fastsurfer_segmentation",
            input_files=[WorkflowInputFile(source="linked", path="mri/orig.mgz")],
        ),
    )
    assert prepared["args"][1] == ["/linked-input/mri/orig.mgz"]
    assert prepared["linked_bind"].source_relative == "subject-a"
    assert submitted["linked_source_id"] == "source-1"
    assert submitted["linked_source_relative"] == "subject-a"
    db.close()


def test_subjects_directory_can_be_linked_as_one_case(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    context, workspace, _cases = seed_workspace_context(db, workspace_id="workspace-single-linked")
    data_root = tmp_path / "data"
    monkeypatch.setattr(operations_module.settings, "fs_data_root", data_root)
    monkeypatch.setattr(storage_module.settings, "fs_data_root", data_root)
    ensure_workspace_storage_layout(operations_module.settings, workspace)
    monkeypatch.setattr(operations_module.BridgeClient, "from_environment", lambda: SubjectsBridge())

    response = create_case_from_linked_source(
        db,
        context,
        request=LinkedCaseCreateRequest(
            workspace_id=workspace.id,
            source_id="source-1",
            title="combined-outputs",
            import_mode="single",
        ),
    )

    assert response.title == "combined-outputs"
    assert response.created_cases == []
    assert db.query(Case).filter(Case.workspace_id == workspace.id).count() == 1
    source = db.query(LinkedCaseSource).filter_by(case_id=response.case_id).one()
    assert source.source_relative == "."
    assert db.query(Artifact).filter_by(case_id=response.case_id).count() == 4
    db.close()


def test_linked_batch_preflights_all_case_name_conflicts(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    context, workspace, _cases = seed_workspace_context(db, workspace_id="workspace-batch-conflict")
    db.add(Case(id="existing", workspace_id=workspace.id, owner_user_id=context.user.id, title="subject-b"))
    db.commit()
    data_root = tmp_path / "data"
    monkeypatch.setattr(operations_module.settings, "fs_data_root", data_root)
    monkeypatch.setattr(storage_module.settings, "fs_data_root", data_root)
    ensure_workspace_storage_layout(operations_module.settings, workspace)
    monkeypatch.setattr(operations_module.BridgeClient, "from_environment", lambda: SubjectsBridge())

    with pytest.raises(Exception) as caught:
        create_case_from_linked_source(
            db, context, request=LinkedCaseCreateRequest(workspace_id=workspace.id, source_id="source-1")
        )

    assert getattr(caught.value, "status_code", None) == 409
    assert {case.title for case in db.query(Case).all()} == {"subject-b"}
    assert db.query(LinkedCaseSource).count() == 0
    db.close()


def test_bids_manifest_groups_subjects_under_independent_link_roots():
    files = [
        {"path": "dataset_description.json", "type": "file", "size": 10},
        {"path": "sub-001/anat/sub-001_T1w.nii.gz", "type": "file", "size": 20},
        {"path": "sub-002/anat/sub-002_T1w.nii.gz", "type": "file", "size": 20},
    ]
    posix_paths = [PurePosixPath(str(item["path"])) for item in files]
    layout = operations_module._detect_uploaded_layout(
        [PurePosixPath("study") / path for path in posix_paths]
    )

    groups = operations_module._linked_subject_groups(files, posix_paths, layout)

    assert layout["scope"] == "dataset"
    assert set(groups) == {"sub-001", "sub-002"}
    assert groups["sub-001"][0] == "sub-001"
    assert groups["sub-001"][1][0][1].as_posix() == "anat/sub-001_T1w.nii.gz"
