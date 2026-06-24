"""Test security hardening behavior for NeuroCade."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time

import pytest
from fastapi import HTTPException
from jwt.exceptions import ExpiredSignatureError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api-service"))

from api_service.helpers import get_case_for_user  # noqa: E402
from api_service.services import get_run_for_user, get_workspace_batch_run_for_user  # noqa: E402
from backend_common.auth import allow_local_auth, get_auth_context, validate_auth_configuration  # noqa: E402
from backend_common.case_storage import build_case_id, case_storage_dir, ensure_case_storage_layout  # noqa: E402
from backend_common.db import AssistantScope, Artifact, Base, Case, RoleEnum, User, Run, RunStatus, Workspace, WorkspaceMembership  # noqa: E402
from backend_common import sample_seed as sample_seed_module  # noqa: E402
from api_service import bootstrap as bootstrap_module  # noqa: E402
from backend_common.deployment_policy import get_deployment_policy  # noqa: E402


@pytest.fixture()
def db_session():
    """Create an isolated in-memory database session."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def _seed_case_context(session):
    """Create a shared workspace with owner, member, outsider, and case records."""
    owner = User(id="user-1", external_auth_id="user-1", email="owner@example.com", full_name="Owner")
    member = User(id="user-2", external_auth_id="user-2", email="member@example.com", full_name="Member")
    outsider = User(id="user-3", external_auth_id="user-3", email="outside@example.com", full_name="Outside")
    workspace = Workspace(
        id="workspace-1",
        owner_user_id=owner.id,
        name="Primary Workspace",
        kind="shared",
        is_default=False,
    )
    session.add_all([owner, member, outsider, workspace])
    session.flush()
    case = Case(id=build_case_id(workspace.id, "case-1"), workspace_id=workspace.id, owner_user_id=owner.id, title="case-1")
    session.add(case)
    session.flush()
    session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=owner.id, role=RoleEnum.owner, granted_by_user_id=owner.id))
    session.add(WorkspaceMembership(workspace_id=workspace.id, user_id=member.id, role=RoleEnum.user, granted_by_user_id=owner.id))
    session.commit()
    return owner, member, outsider, case, workspace


def test_allow_local_auth_requires_local_profile(monkeypatch):
    from backend_common import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", True)
    monkeypatch.setattr(auth_module.settings, "deployment_profile", "local")
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    assert allow_local_auth() is True

    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", False)
    assert allow_local_auth() is False

    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", True)
    monkeypatch.setattr(auth_module.settings, "deployment_profile", "internal")
    assert allow_local_auth() is False


def test_deployment_policy_profiles_expose_expected_flags(monkeypatch):
    from backend_common import settings as settings_module

    active_settings = settings_module.get_settings()
    monkeypatch.setattr(active_settings, "deployment_profile", "demo")
    monkeypatch.setattr(active_settings, "app_base_url", "https://demo.example.org")
    policy = get_deployment_policy(active_settings)

    assert policy.profile == "demo"
    assert policy.uploads_enabled is False
    assert policy.destructive_actions_enabled is False
    assert policy.sample_data()["scope"] == "global"
    assert policy.feature_flags(clerk_configured=True)["demo_mode"] is True


def test_assistant_runtime_commands_are_no_network(monkeypatch, tmp_path):
    from api_service.runtime_tools import container_commands as container_commands_module

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    monkeypatch.setattr(container_commands_module, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(container_commands_module, "HOST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("FREESURFER_LICENSE", raising=False)
    command = container_commands_module._docker_run_workspace_case_bash(
        "curl https://example.org",
        case_dir=str(case_dir),
        image="neurocade-runtime-bash:test",
    )

    assert command.network_disabled is True
    assert command.image == "neurocade-runtime-bash:test"
    assert command.command == ["/bin/bash", "-lc", "curl https://example.org"]
    assert command.env == {}
    assert [(str(bind.host_path), bind.container_path, bind.mode) for bind in command.binds] == [
        (str(case_dir), "/case", "rw")
    ]


def test_workspace_command_binds_selected_cases_without_data_root(monkeypatch, tmp_path):
    from api_service.runtime_tools import container_commands as container_commands_module

    data_root = tmp_path / "neurocade-data"
    cases_dir = data_root / ".workspace-inputs" / "analysis-1" / "cases"
    case_dir = data_root / "output" / "workspaces" / "workspace-1" / "cases" / "case-1"
    workspace_dir = data_root / "output" / "workspaces" / "workspace-1" / "workspace-analyses" / "analysis-1"
    cases_dir.mkdir(parents=True)
    case_dir.mkdir(parents=True)
    workspace_dir.mkdir(parents=True)
    (cases_dir / "cases.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "workspace-1__case-1",
                    "case_title": "case-1",
                    "mount_name": "case-1",
                    "mount_path": "/cases/case-1",
                    "host_path": str(case_dir.resolve()),
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(container_commands_module, "HOST_DATA_DIR", str(data_root))
    monkeypatch.setattr(container_commands_module, "LOCAL_DATA_ROOT", str(data_root))
    monkeypatch.setattr(container_commands_module, "LOCAL_OUTPUT_ROOT", str(data_root / "output"))

    command = container_commands_module._docker_run_workspace_bash(
        "find /cases -type f > /workspace/files.txt",
        cases_dir=str(cases_dir),
        workspace_dir=str(workspace_dir),
        image="neurocade-runtime-bash:test",
    )
    binds = {(str(bind.host_path), bind.container_path, bind.mode) for bind in command.binds}

    assert command.network_disabled is True
    assert (str(case_dir.resolve()), "/cases/case-1", "ro") in binds
    assert (str(workspace_dir.resolve()), "/workspace", "rw") in binds
    assert all(bind.container_path not in {"/data", "/output"} for bind in command.binds)


def test_workspace_runtime_commands_reject_legacy_data_paths():
    from api_service.runtime_tools import container_commands as container_commands_module

    for legacy_path in ("/data/output/workspaces/workspace-1/cases/case-1/mri/orig.mgz", "/output/workspaces/workspace-1/cases/case-1/mri/orig.mgz"):
        with pytest.raises(ValueError, match="/data and /output are not mounted"):
            container_commands_module.execute_workspace_case_bash(
                {
                    "command": f"mri_info {legacy_path}",
                    "case_dir": "/tmp/not-needed-because-command-validation-runs-first",
                }
            )


def test_validate_auth_configuration_rejects_local_auth_outside_local_profile(monkeypatch):
    from backend_common import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "deployment_profile", "internal")
    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", True)
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", "https://clerk.example.test")
    monkeypatch.setattr(auth_module.settings, "clerk_audience", "fastsurfer-app")

    with pytest.raises(RuntimeError, match="LOCAL_AUTH_ENABLED must be false"):
        validate_auth_configuration()


def test_validate_auth_configuration_requires_real_clerk_config_for_shared_profiles(monkeypatch):
    from backend_common import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "deployment_profile", "internal")
    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", False)
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", None)
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", None)
    monkeypatch.setattr(auth_module.settings, "clerk_audience", "fastsurfer-app")

    with pytest.raises(RuntimeError, match="CLERK_JWKS_URL must be configured"):
        validate_auth_configuration()


def test_validate_auth_configuration_requires_clerk_audience_for_shared_profiles(monkeypatch):
    from backend_common import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "deployment_profile", "internal")
    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", False)
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", "https://clerk.example.test")
    monkeypatch.setattr(auth_module.settings, "clerk_audience", None)

    with pytest.raises(RuntimeError, match="CLERK_AUDIENCE must be configured"):
        validate_auth_configuration()


def test_validate_auth_configuration_rejects_default_credentials_in_shared_profile_urls(monkeypatch):
    # SQLite is the only database and carries no credentials, but an externally
    # configured DATABASE_URL with embedded default credentials is still rejected
    # in shared deployment profiles.
    from backend_common import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "deployment_profile", "demo")
    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", False)
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", "https://clerk.example.test")
    monkeypatch.setattr(auth_module.settings, "clerk_audience", "neurocade-demo")
    monkeypatch.setattr(
        auth_module.settings,
        "database_url",
        "postgresql+psycopg://neurocade_user:CHANGE_ME@127.0.0.1:55432/neurocade_db",
    )

    with pytest.raises(RuntimeError, match="DATABASE_URL must not contain default"):
        validate_auth_configuration()


def test_validate_auth_configuration_warns_when_local_clerk_has_no_audience(monkeypatch, caplog):
    from backend_common import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "deployment_profile", "local")
    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", True)
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", "https://clerk.example.test")
    monkeypatch.setattr(auth_module.settings, "clerk_audience", None)

    with caplog.at_level("WARNING", logger=auth_module.logger.name):
        validate_auth_configuration()

    assert "CLERK_AUDIENCE is unset in local mode" in caplog.text


def test_workflow_lookup_requires_workspace_membership(db_session):
    owner, member, outsider, case, workspace = _seed_case_context(db_session)
    workflow = Run(
        id="wf-1",
        case_id=case.id,
        workspace_id=workspace.id,
        created_by_user_id=owner.id,
        status=RunStatus.queued,
        run_type="run_fastsurfer",
        thread_id="thread-1",
        provider_name="openai-compatible",
        model_name="model",
        result_json={},
    )
    db_session.add(workflow)
    db_session.commit()

    resolved, role = get_run_for_user(db_session, "wf-1", member.id)
    assert resolved.id == "wf-1"
    assert role == RoleEnum.user

    with pytest.raises(HTTPException) as exc_info:
        get_run_for_user(db_session, "wf-1", outsider.id)
    assert exc_info.value.status_code == 404


def test_workspace_batch_lookup_requires_membership_and_hides_non_batch_runs(db_session):
    owner, member, outsider, _case, workspace = _seed_case_context(db_session)
    db_session.add_all(
        [
            Run(
                id="wf-batch-1",
                scope_type=AssistantScope.workspace,
                case_id=None,
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                status=RunStatus.queued,
                run_type="workspace_bash",
                thread_id=f"workspace:{workspace.id}",
                provider_name="openai-compatible",
                model_name="model",
                result_json={},
            ),
            Run(
                id="wf-assistant-1",
                scope_type=AssistantScope.workspace,
                case_id=None,
                workspace_id=workspace.id,
                created_by_user_id=owner.id,
                status=RunStatus.queued,
                run_type="summarize_workspace",
                thread_id=f"workspace:{workspace.id}",
                provider_name="openai-compatible",
                model_name="model",
                result_json={},
            ),
        ]
    )
    db_session.commit()

    resolved, role = get_workspace_batch_run_for_user(db_session, workspace.id, "wf-batch-1", member.id)
    assert resolved.id == "wf-batch-1"
    assert role == RoleEnum.user

    with pytest.raises(HTTPException) as exc_info:
        get_workspace_batch_run_for_user(db_session, workspace.id, "wf-assistant-1", member.id)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        get_workspace_batch_run_for_user(db_session, workspace.id, "wf-batch-1", outsider.id)
    assert exc_info.value.status_code == 404


def test_get_case_for_user_accepts_workspace_membership(db_session):
    owner, member, outsider, case, workspace = _seed_case_context(db_session)

    resolved_case, role = get_case_for_user(db_session, case.id, member.id)

    assert resolved_case.id == case.id
    assert role == RoleEnum.user


def test_local_auth_creates_default_personal_workspace(db_session, monkeypatch):
    from backend_common import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", True)
    monkeypatch.setattr(auth_module.settings, "deployment_profile", "local")
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", None)

    context = get_auth_context(db_session, None)
    memberships = db_session.query(WorkspaceMembership).filter(WorkspaceMembership.user_id == context.user.id).all()
    workspaces = db_session.query(Workspace).filter(Workspace.owner_user_id == context.user.id).all()
    default_workspaces = [workspace for workspace in workspaces if workspace.is_default]
    personal_workspaces = [workspace for workspace in workspaces if workspace.kind == "personal"]

    assert context.auth_mode == "local"
    assert len(default_workspaces) == 1
    assert len(personal_workspaces) == 1
    assert default_workspaces[0].kind == "personal"
    assert len(memberships) >= 1
    assert any(membership.workspace_id == default_workspaces[0].id and membership.role == RoleEnum.owner for membership in memberships)


def test_clerk_auth_rejects_expired_tokens_with_401(db_session, monkeypatch):
    from backend_common import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", "https://clerk.example.test")
    monkeypatch.setattr(auth_module.settings, "clerk_audience", "fastsurfer-app")
    monkeypatch.setattr(
        auth_module,
        "PyJWKClient",
        lambda _url: type(
            "FakeJWKClient",
            (),
            {"get_signing_key_from_jwt": staticmethod(lambda _token: (_ for _ in ()).throw(ExpiredSignatureError("expired")))}
        )(),
    )

    with pytest.raises(HTTPException) as exc_info:
        get_auth_context(db_session, "Bearer expired-token")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired authentication token"


def test_verify_clerk_token_passes_configured_audience(db_session, monkeypatch):
    from backend_common import auth as auth_module

    captured: dict[str, object] = {}

    class DummyKey:
        key = "public-key"

    class DummyJWKClient:
        def __init__(self, url):
            captured["jwks_url"] = url

        def get_signing_key_from_jwt(self, token):
            captured["token"] = token
            return DummyKey()

    def fake_decode(token, key, algorithms, issuer, audience=None, options=None):
        captured["decode"] = {
            "token": token,
            "key": key,
            "algorithms": algorithms,
            "issuer": issuer,
            "audience": audience,
            "options": options,
        }
        return {"sub": "user-1"}

    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", "https://clerk.example.test")
    monkeypatch.setattr(auth_module.settings, "clerk_audience", "fastsurfer-app")
    monkeypatch.setattr(auth_module, "PyJWKClient", DummyJWKClient)
    monkeypatch.setattr(auth_module.jwt, "decode", fake_decode)

    claims = auth_module._verify_clerk_token("signed-token")

    assert claims == {"sub": "user-1"}
    assert captured["jwks_url"] == "https://example.test/jwks"
    assert captured["decode"] == {
        "token": "signed-token",
        "key": "public-key",
        "algorithms": ["RS256"],
        "issuer": "https://clerk.example.test",
        "audience": "fastsurfer-app",
        "options": {"verify_aud": True},
    }


def test_clerk_auth_indexes_profile_and_commits_sample_seed(db_session, monkeypatch, tmp_path):
    from backend_common import auth as auth_module

    sample_root = tmp_path / "sample_case_root"
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "001.mgz").write_bytes(b"t1")
    (sample_root / "aparc.DKTatlas+aseg.deep.mgz").write_bytes(b"seg")
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", sample_root)
    monkeypatch.setattr(sample_seed_module.settings, "fs_data_root", tmp_path / "fs-data")
    monkeypatch.setattr(sample_seed_module.settings, "outputs_dir_override", tmp_path / "fs-data" / "output")
    sample_seed_module.settings.outputs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", "https://clerk.example.test")
    monkeypatch.setattr(
        auth_module,
        "_verify_clerk_token",
        lambda token: {
            "sub": "clerk-user-2",
        },
    )
    monkeypatch.setattr(
        auth_module,
        "_fetch_clerk_user_profile",
        lambda user_id: {"email": "clerk-user@example.com", "full_name": "Clerk User"},
    )

    context = get_auth_context(db_session, "Bearer token")
    db_session.rollback()
    persisted_user = db_session.get(User, context.user.id)
    persisted_workspace = db_session.query(Workspace).filter(Workspace.owner_user_id == context.user.id, Workspace.is_default.is_(True)).one()
    persisted_case = db_session.get(Case, sample_seed_module.sample_case_id_for_workspace(persisted_workspace.id))

    assert persisted_user is not None
    assert persisted_user.email == "clerk-user@example.com"
    assert persisted_user.full_name == "Clerk User"
    assert persisted_workspace.id.startswith("clerk-user-workspace-")
    assert persisted_case is not None
    assert persisted_case.title == "sample-case"


def test_bootstrap_database_uses_create_all_for_in_memory_sqlite(monkeypatch):
    called = {"create_all": False, "upgrade": False}

    def fake_create_all(*args, **kwargs):
        called["create_all"] = True

    def fake_upgrade(*args, **kwargs):
        called["upgrade"] = True

    monkeypatch.setattr(bootstrap_module.Base.metadata, "create_all", fake_create_all)
    monkeypatch.setattr(bootstrap_module.command, "upgrade", fake_upgrade)

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    bootstrap_module.bootstrap_database(engine)

    assert called["create_all"] is True
    assert called["upgrade"] is False


def test_bootstrap_database_uses_alembic_for_persistent_databases(monkeypatch):
    called = {"create_all": False, "upgrade": False}

    def fake_create_all(*args, **kwargs):
        called["create_all"] = True

    def fake_upgrade(*args, **kwargs):
        called["upgrade"] = True

    class DummyDialect:
        name = "postgresql"

    class DummyUrl:
        database = "fastsurfer_app"

    class DummyEngine:
        dialect = DummyDialect()
        url = DummyUrl()

    monkeypatch.setattr(bootstrap_module.Base.metadata, "create_all", fake_create_all)
    monkeypatch.setattr(bootstrap_module.command, "upgrade", fake_upgrade)

    bootstrap_module.bootstrap_database(DummyEngine())

    assert called["create_all"] is False
    assert called["upgrade"] is True


def test_local_auth_seeds_sample_case_once(db_session, monkeypatch, tmp_path):
    from backend_common import auth as auth_module

    sample_root = tmp_path / "sample_case_root"
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "001.mgz").write_bytes(b"t1")
    (sample_root / "aparc.DKTatlas+aseg.deep.mgz").write_bytes(b"seg")
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", sample_root)
    monkeypatch.setattr(sample_seed_module.settings, "fs_data_root", tmp_path / "fs-data")
    monkeypatch.setattr(sample_seed_module.settings, "outputs_dir_override", tmp_path / "fs-data" / "output")
    sample_seed_module.settings.outputs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", True)
    monkeypatch.setattr(auth_module.settings, "deployment_profile", "local")
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", None)

    context = get_auth_context(db_session, None)
    get_auth_context(db_session, None)

    sample_workspace = db_session.query(Workspace).filter(Workspace.owner_user_id == context.user.id, Workspace.is_default.is_(True)).one()
    sample_case = db_session.get(Case, sample_seed_module.sample_case_id_for_workspace(sample_workspace.id))
    sample_workspace = db_session.get(Workspace, sample_case.workspace_id)
    artifact_count = db_session.query(Artifact).filter(Artifact.case_id == sample_case.id).count()
    workspace_membership_count = (
        db_session.query(WorkspaceMembership)
        .filter(WorkspaceMembership.workspace_id == sample_workspace.id, WorkspaceMembership.user_id == context.user.id)
        .count()
    )

    assert sample_case is not None
    assert sample_workspace is not None
    assert sample_case.title == "sample-case"
    sample_case_dir = case_storage_dir(sample_seed_module.settings, sample_workspace.id, sample_case.id)
    assert (sample_case_dir / "001.mgz").exists()
    assert (sample_case_dir / "aparc.DKTatlas+aseg.deep.mgz").exists()
    assert artifact_count == 2
    assert workspace_membership_count == 1


def test_local_auth_preserves_renamed_sample_case(db_session, monkeypatch, tmp_path):
    from backend_common import auth as auth_module

    sample_root = tmp_path / "sample_case_root"
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "001.mgz").write_bytes(b"t1")
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", sample_root)
    monkeypatch.setattr(sample_seed_module.settings, "fs_data_root", tmp_path / "fs-data")
    monkeypatch.setattr(sample_seed_module.settings, "outputs_dir_override", tmp_path / "fs-data" / "output")
    sample_seed_module.settings.outputs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(auth_module.settings, "local_auth_enabled", True)
    monkeypatch.setattr(auth_module.settings, "deployment_profile", "local")
    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", None)

    context = get_auth_context(db_session, None)
    sample_workspace = db_session.query(Workspace).filter(Workspace.owner_user_id == context.user.id, Workspace.is_default.is_(True)).one()
    sample_case = db_session.get(Case, sample_seed_module.sample_case_id_for_workspace(sample_workspace.id))
    assert sample_case is not None
    workspace = db_session.get(Workspace, sample_case.workspace_id)
    assert workspace is not None

    renamed_sample_case_id = build_case_id(workspace.id, "renamed-sample-case")
    sample_case.id = renamed_sample_case_id
    sample_case.title = "renamed-sample-case"
    ensure_case_storage_layout(db_session, sample_seed_module.settings, sample_case, workspace)
    db_session.commit()

    get_auth_context(db_session, None)
    db_session.expire_all()
    sample_case = db_session.get(Case, renamed_sample_case_id)
    assert sample_case is not None
    workspace = db_session.get(Workspace, sample_case.workspace_id)
    assert workspace is not None
    renamed_case_dir = case_storage_dir(sample_seed_module.settings, workspace.id, sample_case.id)

    assert sample_case.title == "renamed-sample-case"
    assert (renamed_case_dir / "001.mgz").exists()


def test_clerk_auth_serializes_same_user_bootstrap(monkeypatch, tmp_path):
    from backend_common import auth as auth_module

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'auth-bootstrap.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    sample_root = tmp_path / "sample_case_root"
    sample_root.mkdir(parents=True, exist_ok=True)
    (sample_root / "001.mgz").write_bytes(b"t1")
    (sample_root / "aparc.DKTatlas+aseg.deep.mgz").write_bytes(b"seg")
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", sample_root)
    monkeypatch.setattr(sample_seed_module.settings, "fs_data_root", tmp_path / "fs-data")
    monkeypatch.setattr(sample_seed_module.settings, "outputs_dir_override", tmp_path / "fs-data" / "output")
    sample_seed_module.settings.outputs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(auth_module.settings, "clerk_jwks_url", "https://example.test/jwks")
    monkeypatch.setattr(auth_module.settings, "clerk_issuer", "https://clerk.example.test")
    monkeypatch.setattr(
        auth_module,
        "_verify_clerk_token",
        lambda token: {
            "sub": "clerk-user-race",
        },
    )
    monkeypatch.setattr(
        auth_module,
        "_fetch_clerk_user_profile",
        lambda user_id: {"email": "race@example.com", "full_name": "Race User"},
    )

    real_ensure_sample_case = auth_module.ensure_sample_case
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def tracked_ensure_sample_case(db, user):
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            time.sleep(0.05)
            return real_ensure_sample_case(db, user)
        finally:
            with state_lock:
                state["active"] -= 1

    monkeypatch.setattr(auth_module, "ensure_sample_case", tracked_ensure_sample_case)

    def bootstrap_once() -> str:
        session = session_factory()
        try:
            context = get_auth_context(session, "Bearer token")
            return context.user.id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        user_ids = list(executor.map(lambda _index: bootstrap_once(), range(4)))

    assert user_ids == ["clerk-user-race"] * 4
    assert state["max_active"] == 1

    session = session_factory()
    try:
        user = session.get(User, "clerk-user-race")
        assert user is not None
        workspace = session.query(Workspace).filter(Workspace.owner_user_id == user.id, Workspace.is_default.is_(True)).one()
        sample_case = session.get(Case, sample_seed_module.sample_case_id_for_workspace(workspace.id))
        assert sample_case is not None
        assert workspace.id.startswith("race-workspace-")
        assert session.query(Workspace).filter(Workspace.owner_user_id == user.id, Workspace.is_default.is_(True)).count() == 1
        assert session.query(WorkspaceMembership).filter(WorkspaceMembership.user_id == user.id).count() == 1
        assert session.query(Artifact).filter(Artifact.case_id == sample_case.id).count() == 2
    finally:
        session.close()


def test_sample_seed_requires_configured_processed_dir(monkeypatch, tmp_path):
    unrelated_root = tmp_path / "Rhineland_0000"
    unrelated_root.mkdir(parents=True, exist_ok=True)

    configured_root = tmp_path / "FastSurfer_Rhineland_0000"
    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", configured_root)

    assert sample_seed_module._resolve_sample_case_root() == configured_root


def test_sample_seed_uses_generated_fastsurfer_dir(monkeypatch, tmp_path):
    generated_root = tmp_path / "FastSurfer_Rhineland_0000"
    generated_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(sample_seed_module, "SAMPLE_CASE_ROOT", generated_root)

    assert sample_seed_module._resolve_sample_case_root() == generated_root
