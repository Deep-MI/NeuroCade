"""Fault-injection tests for retrying publication across GitHub and Docker Hub."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("publication", Path(__file__).resolve().parents[1] / "scripts/release/publication.py")
assert SPEC and SPEC.loader
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


@pytest.fixture
def state(tmp_path, monkeypatch):
    plan = tmp_path / "plan.txt"
    plan.write_text(
        "tag=v2026.9.9-beta.2\nversion=2026.9.9-beta.2\nkind=beta\nprerelease=true\n"
        f"head_sha={'a' * 40}\nshould_release=true\nlatest_reachable_release_tag=v2026.9.9-beta.1\n"
    )
    monkeypatch.setenv("APP_SIF", "app.sif")
    monkeypatch.setenv("BRIDGE_WHEEL", "bridge.whl")
    monkeypatch.setenv("CANDIDATE_DIGEST_IMAGE", "docker.io/deepmi/neurocade@sha256:123")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Deep-MI/NeuroCade")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    data = {"releases": [], "tag": False, "calls": [], "fail": None}

    def command(*args):
        data["calls"].append(args)
        if data["fail"] and data["fail"](args):
            data["fail"] = None
            raise RuntimeError("Simulated interruption")
        if args[:2] == ("gh", "api"):
            return json.dumps([data["releases"]])
        if args[:3] == ("gh", "release", "create"):
            data["releases"].append({
                "tag_name": "v2026.9.9-beta.2", "target_commitish": "a" * 40,
                "draft": True, "prerelease": True,
            })
        if args[:3] == ("git", "push", "origin"):
            data["tag"] = True
        if args[:3] == ("gh", "release", "edit"):
            data["releases"][0]["draft"] = False
        return ""

    monkeypatch.setattr(publication, "run", command)
    monkeypatch.setattr(publication, "check_tag", lambda _: data["tag"])
    return plan, data


@pytest.mark.parametrize("stage", ["upload", "image", "tag", "publish", "channel"])
def test_retry_finishes_partial_publication(state, stage):
    plan, data = state
    predicates = {
        "upload": lambda a: a[:3] == ("gh", "release", "upload"),
        "image": lambda a: a[:2] == ("docker", "buildx") and a[5].endswith("beta.2"),
        "tag": lambda a: a[:3] == ("git", "push", "origin"),
        "publish": lambda a: a[:3] == ("gh", "release", "edit"),
        "channel": lambda a: a[:2] == ("docker", "buildx") and a[5].endswith(":beta"),
    }
    data["fail"] = predicates[stage]
    with pytest.raises(RuntimeError, match="interruption"):
        publication.publish(plan)
    data["calls"].clear()
    publication.publish(plan)
    assert len(data["releases"]) == 1
    assert data["releases"][0]["draft"] is False
    assert data["calls"][-1][-2:] == ("docker.io/deepmi/neurocade:beta", "docker.io/deepmi/neurocade:v2026.9.9-beta.2")
    if stage == "channel":
        assert not any(a[:2] == ("gh", "release") for a in data["calls"])
        assert sum(a[0] == "docker" for a in data["calls"]) == 1


def test_retry_restores_original_sha_and_published_state(state, tmp_path):
    plan, data = state
    publication.publish(plan)
    publication.prepare(plan)
    output = (tmp_path / "outputs").read_text()
    assert "published=true" in output
    assert "tag=v2026.9.9-beta.2" in output
    assert data["calls"][-1] == ("git", "checkout", "--detach", "a" * 40)


def test_old_retry_does_not_roll_back_channel(state):
    plan, data = state
    publication.publish(plan)
    data["releases"].append({"tag_name": "v2026.9.9-beta.3", "draft": False, "prerelease": True})
    data["calls"].clear()
    publication.publish(plan)
    assert not any(a[0] == "docker" for a in data["calls"])


def test_tag_collision_rejected(monkeypatch):
    monkeypatch.setattr(publication.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "b" * 40, ""))
    with pytest.raises(ValueError, match="different commit"):
        publication.check_tag({"tag": "v2026.9.9-beta.2", "head_sha": "a" * 40})


def test_draft_collision_rejected(state):
    plan, data = state
    data["releases"].append({"tag_name": "v2026.9.9-beta.2", "target_commitish": "b" * 40, "draft": True, "prerelease": True})
    with pytest.raises(ValueError, match="different commit"):
        publication.publish(plan)
    assert not any(a[:2] == ("gh", "release") for a in data["calls"])


def test_upload_failure_does_not_promote_image_or_tag(state):
    plan, data = state
    data["fail"] = lambda a: a[:3] == ("gh", "release", "upload")
    with pytest.raises(RuntimeError):
        publication.publish(plan)
    assert data["releases"][0]["draft"]
    assert not data["tag"]
    assert not any(a[0] == "docker" for a in data["calls"])


def test_stable_publication_and_retry(state):
    plan, data = state
    plan.write_text(plan.read_text().replace("-beta.2", "-2").replace("kind=beta", "kind=stable").replace("prerelease=true", "prerelease=false"))
    # Existing draft lets this case exercise the stable publication flags.
    data["releases"].append({
        "tag_name": "v2026.9.9-2", "target_commitish": "a" * 40, "draft": True, "prerelease": False,
    })
    publication.publish(plan)
    assert any(a[:3] == ("gh", "release", "edit") and "--latest=true" in a for a in data["calls"])
    assert data["calls"][-1][-2:] == ("docker.io/deepmi/neurocade:latest", "docker.io/deepmi/neurocade:v2026.9.9-2")
    data["calls"].clear()
    publication.publish(plan)
    assert not any(a[:2] == ("gh", "release") for a in data["calls"])


def test_retry_old_draft_does_not_mark_stable_latest(state):
    plan, data = state
    plan.write_text(plan.read_text().replace("-beta.2", "-2").replace("kind=beta", "kind=stable").replace("prerelease=true", "prerelease=false"))
    data["releases"].extend([
        {"tag_name": "v2026.9.9-2", "target_commitish": "a" * 40, "draft": True, "prerelease": False},
        {"tag_name": "v2026.9.10", "draft": False, "prerelease": False},
    ])
    publication.publish(plan)
    assert any(a[:3] == ("gh", "release", "edit") and "--latest=false" in a for a in data["calls"])
    assert not any(a[0] == "docker" and a[5].endswith(":latest") for a in data["calls"])


def test_no_change_plan_stays_skipped(state, tmp_path):
    plan, data = state
    plan.write_text(plan.read_text().replace("should_release=true", "should_release=false"))
    publication.prepare(plan)
    assert "should_release=false" in (tmp_path / "outputs").read_text()
    assert not any(a[0] == "gh" for a in data["calls"])
