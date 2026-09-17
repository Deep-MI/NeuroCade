"""Local file tools. Binary content travels directly between disk and NeuroCade."""

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from mcp import types

from neurocade_mcp.errors import KNOWN_DETAILS, TITLE_MESSAGE, TransferError


def file_tools(access):
    tools = [types.Tool(name="neurocade_download_file", description="Download an artifact or a complete case ZIP directly to an explicit absolute local destination. Supports resuming interrupted transfers and verifies SHA-256. Never overwrites an existing destination. Ask which files and destination the user wants if unclear.",
        inputSchema={"type": "object", "properties": {"artifact_id": {"type": "string"}, "case_id": {"type": "string"}, "destination": {"type": "string"}}, "required": ["destination"], "additionalProperties": False},
        annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))]
    if access == "standard":
        tools.append(types.Tool(name="neurocade_upload_case", description="Upload an explicitly selected absolute local scan path directly to the paired workspace. Creates a case with optional title, or attaches to case_id. Accepts NIfTI, MGZ, or a DICOM ZIP. Returns case_id; then inspect case/artifacts to find workflow inputs. Reuse the same idempotency_key on retries. Never upload files the user has not selected or authorized.",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}, "title": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$", "minLength": 2, "maxLength": 64, "description": TITLE_MESSAGE + " Omit to derive a name from the filename. Do not combine with case_id."}, "case_id": {"type": "string"}, "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 255}}, "required": ["path", "idempotency_key"], "additionalProperties": False},
            annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)))
    return tools


def client_for(data):
    return httpx.Client(base_url=data["url"].removesuffix("/mcp") + "/api/app/mcp/files/",
        headers={"Authorization": "Bearer " + data["token"], "X-NeuroCade-Installation": data["installation_id"]},
        timeout=httpx.Timeout(1800, connect=10), follow_redirects=False, trust_env=False)


def check_response(response):
    if response.is_error:
        # Streamed download errors must be read too. Bound the error body and
        # never reflect arbitrary server strings, validation input or headers.
        body = bytearray()
        for chunk in response.iter_bytes(4096):
            body.extend(chunk)
            if len(body) > 16384:
                break
        try:
            payload = json.loads(body) if len(body) <= 16384 else None
            detail = payload.get("detail") if isinstance(payload, dict) else None
        except (ValueError, UnicodeError):
            detail = None
        if isinstance(detail, str) and detail in KNOWN_DETAILS:
            code, message = KNOWN_DETAILS[detail]
            raise TransferError(code, message, status=response.status_code)
        if response.status_code == 422 and isinstance(detail, list):
            fields = set()
            for item in detail:
                location = item.get("loc", []) if isinstance(item, dict) else []
                for field in location:
                    if isinstance(field, str) and field in {"title", "filename", "sha256", "idempotency_key", "case_id", "artifact_id"}:
                        fields.add(field)
            if fields:
                raise TransferError("INVALID_PARAMETERS", "Invalid request fields: " + ", ".join(sorted(fields)) + ". Correct these parameters; do not alter the scan based on this error.", status=422)
        messages = {400: "Request rejected. The server supplied no recognized diagnostic. Check request parameters; this does not establish file corruption or a checksum failure.", 401: "Connection revoked or invalid; pair again", 403: "Connection does not permit this action", 404: "Case or artifact unavailable in this workspace", 409: "Request conflicts with existing work or upload identity; inspect the case before retrying", 413: "File exceeds NeuroCade's upload limit", 416: "Download range unavailable; choose a new destination or inspect the partial transfer", 422: "Invalid request parameters; check the tool schema", 429: "Server is busy; retry later using the same upload key or download destination"}
        raise TransferError("HTTP_" + str(response.status_code), messages.get(response.status_code, f"NeuroCade transfer failed (HTTP {response.status_code}). Outcome may be unknown; inspect the case before retrying with the same upload key."), status=response.status_code, retryable=response.status_code == 429 or response.status_code >= 500)
    if response.status_code not in {200, 206}:
        raise ValueError("Unexpected transfer response; redirects are not followed")


def upload(data, args):
    if "title" in args:
        if args.get("case_id"):
            raise TransferError("INVALID_PARAMETERS", "Specify title for a new case OR case_id for an existing case, not both.")
        if not isinstance(args["title"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}[a-z0-9]", args["title"]):
            raise TransferError("INVALID_CASE_TITLE", TITLE_MESSAGE)
    path = Path(args["path"]).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Select an existing absolute local file path")
    with path.open("rb") as handle, client_for(data) as client:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        handle.seek(0)
        params = {"filename": path.name, "sha256": digest, "idempotency_key": args["idempotency_key"]}
        params.update({key: args[key] for key in ("case_id", "title") if key in args})
        response = client.put("upload", params=params, content=iter(lambda: handle.read(1024 * 1024), b""))
        check_response(response)
        return response.json()


def download(data, args):
    if bool(args.get("artifact_id")) == bool(args.get("case_id")):
        raise ValueError("Specify exactly one of artifact_id or case_id")
    destination = Path(args["destination"]).expanduser()
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("Choose an absolute destination that does not already exist")
    if not destination.parent.is_dir():
        raise ValueError("The destination directory does not exist")
    endpoint = "artifacts/" + quote(args["artifact_id"], safe="") if args.get("artifact_id") else "cases/" + quote(args["case_id"], safe="")
    partial = destination.with_name(destination.name + ".neurocade-part")
    metadata = destination.with_name(destination.name + ".neurocade-transfer.json")
    for path in (partial, metadata):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Unsafe partial download path")
    identity = {"installation_id": data["installation_id"], "client_id": data["client_id"], "endpoint": endpoint}
    previous = json.loads(metadata.read_text()) if metadata.exists() else {}
    if partial.exists() and any(previous.get(key) != value for key, value in identity.items()):
        raise ValueError("Partial download belongs to a different file; choose another destination")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-", "If-Range": previous["etag"]} if offset and previous.get("etag") else {}
    with client_for(data) as client, client.stream("GET", endpoint, headers=headers) as response:
        if response.status_code == 416 and partial.exists() and previous.get("sha256"):
            with partial.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() == previous["sha256"]:
                    os.link(partial, destination)
                    partial.unlink()
                    metadata.unlink(missing_ok=True)
                    return {"destination": str(destination), "size_bytes": destination.stat().st_size, "sha256": previous["sha256"]}
        check_response(response)
        digest = response.headers.get("x-checksum-sha256")
        if not digest or len(digest) != 64:
            raise ValueError("Server did not supply a SHA-256 checksum")
        if response.status_code == 206 and (not offset or not response.headers.get("content-range", "").startswith(f"bytes {offset}-")):
            raise ValueError("Unexpected download range")
        fd = os.open(metadata, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({**identity, "etag": response.headers.get("etag"), "sha256": digest}, handle)
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_APPEND if response.status_code == 206 else os.O_TRUNC)
        fd = os.open(partial, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                handle.write(chunk)
    with partial.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != digest:
        partial.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)
        raise ValueError("Download checksum mismatch; retry the download")
    # Atomic, no-overwrite publication even if another process creates destination.
    os.link(partial, destination)
    partial.unlink()
    metadata.unlink(missing_ok=True)
    return {"destination": str(destination), "size_bytes": destination.stat().st_size, "sha256": actual}
