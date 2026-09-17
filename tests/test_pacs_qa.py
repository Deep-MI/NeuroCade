"""The local QA gateway must authenticate clients and preserve DICOMweb streaming."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import pacs_qa


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(pacs_qa, "configuration", lambda: {
        "username": "qa", "password": "orthanc-test", "client_id": "test", "client_secret": "secret",
    })
    return TestClient(pacs_qa.create_app())


def test_gateway_requires_credentials(gateway):
    assert gateway.post("/token", data={"grant_type": "client_credentials"}).status_code == 401
    assert gateway.get("/dicom-web/studies").status_code == 401
    assert gateway.post("/token", auth=("test", "secret"), data={"grant_type": "password"}).status_code == 400


def test_gateway_preserves_query_and_stream(gateway, monkeypatch):
    token = gateway.post("/token", auth=("test", "secret"), data={"grant_type": "client_credentials"})
    assert token.status_code == 200
    assert token.headers["cache-control"] == "no-store"
    access = token.json()["access_token"]
    closed = []

    class Response:
        status_code = 200
        headers = {"Content-Type": 'multipart/related; boundary="test"'}

        def iter_content(self, size):
            yield b"first"
            yield b"second"

        def close(self):
            closed.append(True)

    def get(url, **kwargs):
        assert url == pacs_qa.ORTHANC + "/dicom-web/studies"
        assert kwargs["params"] == [("limit", "100"), ("offset", "100")]
        assert kwargs["auth"] == ("qa", "orthanc-test")
        assert kwargs["allow_redirects"] is False
        return Response()

    monkeypatch.setattr(pacs_qa.requests, "get", get)
    response = gateway.get("/dicom-web/studies?limit=100&offset=100", headers={"Authorization": f"Bearer {access}"})
    assert response.content == b"firstsecond"
    assert response.headers["content-type"] == 'multipart/related; boundary="test"'
    assert closed == [True]
    assert gateway.post("/dicom-web/studies", headers={"Authorization": f"Bearer {access}"}).status_code == 405


def test_expired_token_is_rejected(gateway, monkeypatch):
    token = gateway.post("/token", auth=("test", "secret"), data={"grant_type": "client_credentials"}).json()["access_token"]
    current = pacs_qa.time.monotonic()
    monkeypatch.setattr(pacs_qa.time, "monotonic", lambda: current + 301)
    assert gateway.get("/dicom-web/studies", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("key", ["../escape", "/absolute", "folder/../escape", "folder\\escape", "folder//file"])
def test_download_rejects_unsafe_object_paths(key):
    with pytest.raises(ValueError, match="Unsafe public object key"):
        pacs_qa.download(key, 1)
