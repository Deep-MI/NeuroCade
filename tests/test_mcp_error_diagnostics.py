"""An agent must distinguish invalid metadata from corrupt imaging bytes."""

import hashlib

import httpx
import pytest
from mcp import types
from mcp.shared.exceptions import McpError
from neurocade_mcp import call_remote_tool
from neurocade_mcp.errors import TransferError
from neurocade_mcp.transfers import check_response, upload
from test_mcp_adapter import database as database
from test_mcp_transfers import app_for, headers, volume

__all__ = ["database"]


def test_invalid_title_is_rejected_before_file_access(tmp_path):
    with pytest.raises(TransferError) as error:
        upload({}, {"path": str(tmp_path / "missing.nii"), "title": "claude_case", "idempotency_key": "k"})
    assert error.value.details["code"] == "INVALID_CASE_TITLE"
    assert "do not modify the scan" in str(error.value)


@pytest.mark.parametrize("status,detail,code", [
    (400, "Case name must be a lowercase slug, 2-64 characters, using only a-z, 0-9, and hyphen", "INVALID_CASE_TITLE"),
    (400, "SHA-256 mismatch", "CHECKSUM_MISMATCH"),
    (400, "NIfTI upload has an invalid header", "REQUEST_REJECTED"),
    (409, "Case has an active PACS import", "REQUEST_REJECTED"),
    (409, "Installation identity mismatch; reconnect NeuroCade", "INSTALLATION_MISMATCH"),
    (409, "IDEMPOTENCY_CONFLICT: use the original upload parameters", "IDEMPOTENCY_CONFLICT"),
])
def test_specific_server_diagnostics(status, detail, code):
    with pytest.raises(TransferError) as error:
        check_response(httpx.Response(status, json={"detail": detail}))
    assert error.value.details["code"] == code
    assert error.value.details["http_status"] == status


def test_streamed_validation_errors_do_not_echo_input():
    def respond(_):
        return httpx.Response(422, json={"detail": [{"loc": ["query", "title"], "input": "ncmcp_secret", "msg": "Bearer secret"}]})
    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        client.stream("GET", "http://localhost/") as response,
        pytest.raises(TransferError) as error,
    ):
        check_response(response)
    assert "title" in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("body", [b'{"detail":"ncmcp_secret"}', b'<html>Bearer secret</html>', b'x' * 20000])
def test_unknown_error_is_not_misdiagnosed_or_reflected(body):
    with pytest.raises(TransferError) as error:
        check_response(httpx.Response(400, content=body))
    assert "does not establish file corruption" in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_metadata_rejection_does_not_claim_upload_key(database):
    from backend_common.db import AssistantToolExecution

    data = volume()
    params = {"filename": "scan.nii", "title": "claude_case", "sha256": hashlib.sha256(data).hexdigest(), "idempotency_key": "fix-title"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app_for(database)), base_url="http://localhost", headers=headers()) as client:
        bad = await client.put('/api/app/mcp/files/upload', params=params, content=data)
        assert bad.status_code == 400
        with pytest.raises(TransferError) as error:
            check_response(bad)
        assert error.value.details["code"] == "INVALID_CASE_TITLE"
        with database() as db:
            assert db.query(AssistantToolExecution).filter_by(call_id="fix-title").count() == 0
        good = await client.put('/api/app/mcp/files/upload', params={**params, "title": "claude-case"}, content=data)
        assert good.status_code == 200, good.text


@pytest.mark.asyncio
async def test_timeout_does_not_imply_submission_failure():
    class Remote:
        async def call_tool(self, name, arguments):
            raise McpError(types.ErrorData(code=408, message="timeout including secret"))

    result = await call_remote_tool(Remote(), "neurocade_tool_call", {})
    assert result.isError
    assert result.structuredContent is not None
    assert result.structuredContent["error"]["code"] == "TOOL_OUTCOME_UNKNOWN"
    assert "same arguments and idempotency_key" in str(result.content)
    assert "secret" not in str(result.content)
