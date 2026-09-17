"""Safety and recovery checks for the opt-in live QA runner (no Docker required)."""
import json
from types import SimpleNamespace

import pytest

from scripts import pacs_qa_acceptance as qa


def test_preflight_rejects_other_workspaces(monkeypatch):
    monkeypatch.setattr(qa, "api", lambda *_: [{"id": "personal", "name": "personal"}])
    monkeypatch.setattr(qa.pacs_qa, "docker", lambda *_a, **_k: pytest.fail("Must not touch Docker"))
    with pytest.raises(RuntimeError, match="dedicated pacs-qa"):
        qa.preflight("personal")


def test_preflight_rejects_unowned_archive(monkeypatch):
    def api(_method, path, **_kwargs):
        return [{"id": "qa", "name": "pacs-qa"}] if path == "/workspaces" else {"enabled": True, "source": "local-remind-qa"}
    monkeypatch.setattr(qa, "api", api)
    monkeypatch.setattr(qa.pacs_qa, "docker", lambda *_a, **_k: SimpleNamespace(stdout=json.dumps({"Config": {"Labels": {}}, "State": {"Running": True}})))
    with pytest.raises(RuntimeError, match="ownership"):
        qa.preflight("qa")


def test_default_run_is_read_only(monkeypatch):
    monkeypatch.setattr(qa, "preflight", lambda _: [])
    monkeypatch.setattr(qa, "api", lambda *_a, **_k: pytest.fail("Must not create imports"))
    qa.run("qa")


@pytest.mark.parametrize("outcome", ["success", "unexpected_failure", "timeout", "interrupt"])
def test_outage_always_restores_archive_before_retry(monkeypatch, outcome):
    events = []
    monkeypatch.setattr(qa.pacs_qa, "docker", lambda action, *_a, **_k: events.append(action))
    monkeypatch.setattr(qa, "wait_archive", lambda: events.append("healthy"))
    monkeypatch.setattr(qa, "inspect_app", lambda *_: {"cleanup": [{"staging_removed": True}]})

    def wait(_):
        if outcome == "timeout":
            raise TimeoutError()
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        return {"state": "failed", "series": [{"error_code": "retrieval_failed" if outcome == "success" else "conversion_failed"}]}

    monkeypatch.setattr(qa, "wait_import", wait)
    monkeypatch.setattr(qa, "api", lambda *_: events.append("retry"))
    if outcome == "success":
        qa.outage_and_retry({"id": "import"}, "qa")
        assert events == ["stop", "start", "healthy", "retry"]
    else:
        with pytest.raises((RuntimeError, TimeoutError, KeyboardInterrupt)):
            qa.outage_and_retry({"id": "import"}, "qa")
        assert events == ["stop", "start", "healthy"]
