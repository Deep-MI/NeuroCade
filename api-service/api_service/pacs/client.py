"""Bounded DICOMweb discovery and disk-streamed instance retrieval."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from python_multipart.multipart import MultipartParser, parse_options_header

from backend_common.settings import Settings


class PacsError(Exception):
    """Safe, public error category; never include upstream text."""


def uid(value: str) -> str:
    if not value or len(value) > 64 or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))+", value):
        raise PacsError("invalid_uid")
    return value


def value(data: dict, tag: str, default=""):
    values = data.get(tag, {}).get("Value", [])
    result = values[0] if values else default
    return result.get("Alphabetic", "") if isinstance(result, dict) else result


# Initial conversion profiles. Other objects remain visible, never silently converted.
IMAGE_CLASSES = {
    "1.2.840.10008.5.1.4.1.1.2", "1.2.840.10008.5.1.4.1.1.2.1",
    "1.2.840.10008.5.1.4.1.1.4", "1.2.840.10008.5.1.4.1.1.4.1",
    "1.2.840.10008.5.1.4.1.1.128", "1.2.840.10008.5.1.4.1.1.130",
}


def validate_config(settings: Settings) -> None:
    if not settings.pacs_enabled:
        return
    for url in (settings.pacs_base_url, settings.pacs_token_url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("PACS endpoints must be configured HTTPS URLs without credentials, queries or fragments")
    for prefix in (settings.pacs_qido_prefix, settings.pacs_wado_prefix):
        if prefix and (":" in prefix or ".." in prefix or "?" in prefix or "#" in prefix):
            raise ValueError("Invalid PACS service prefix")
    if not settings.pacs_client_id or not settings.pacs_client_secret or not settings.pacs_workspace_ids.strip():
        raise ValueError("PACS requires OAuth credentials and an explicit workspace allowlist")


class TokenProvider:
    def __init__(self):
        self.lock = threading.Lock()
        self.token = ""
        self.expires = 0.0

    def get(self, settings: Settings, *, refresh=False) -> str:
        with self.lock:
            if not refresh and self.token and time.monotonic() < self.expires:
                return self.token
            try:
                with requests.Session() as session:
                    session.trust_env = False
                    with session.post(
                        settings.pacs_token_url,
                        data={"grant_type": "client_credentials", "scope": settings.pacs_scope},
                        auth=(settings.pacs_client_id, settings.pacs_client_secret),
                        timeout=(5, 15), verify=settings.pacs_ca_bundle or True,
                        allow_redirects=False, stream=True,
                    ) as response:
                        if response.status_code != 200:
                            raise PacsError("authentication_failed")
                        raw = bytearray()
                        for chunk in response.iter_content(4096):
                            raw.extend(chunk)
                            if len(raw) > 65536:
                                raise PacsError("authentication_failed")
                        import json
                        payload = json.loads(raw)
                if str(payload.get("token_type", "")).lower() != "bearer":
                    raise PacsError("authentication_failed")
                self.token = str(payload["access_token"])
                self.expires = time.monotonic() + max(0, float(payload["expires_in"]) - 30)
                return self.token
            except (requests.RequestException, ValueError, KeyError):
                raise PacsError("authentication_failed") from None


tokens = TokenProvider()


class PacsClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = settings.pacs_ca_bundle or True

    def close(self):
        self.session.close()

    def _get(self, url, *, params=None, accept="application/dicom+json"):
        for attempt in range(2):
            try:
                response = self.session.get(
                    url, params=params, headers={"Authorization": f"Bearer {tokens.get(self.settings, refresh=bool(attempt))}", "Accept": accept},
                    timeout=(5, 15), stream=True, allow_redirects=False,
                )
            except requests.RequestException:
                raise PacsError("connection_failed") from None
            if response.status_code == 401 and attempt == 0:
                response.close()
                continue
            if response.status_code not in (200, 204):
                response.close()
                raise PacsError("retrieval_failed")
            return response
        raise PacsError("authentication_failed")

    def query(self, path: str, params: dict | None = None) -> list[dict]:
        # Keep bounded JSON decoding and redirect controls consistent with WADO.
        import json
        prefix = self.settings.pacs_qido_prefix
        url = self.settings.pacs_base_url.rstrip("/") + ("/" + prefix.strip("/") if prefix else "") + path
        with self._get(url, params=params) as response:
            if response.status_code == 204:
                return []
            raw = bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw) > 8 * 1024 * 1024:
                    raise PacsError("metadata_limit")
            try:
                payload = json.loads(raw)
            except ValueError:
                raise PacsError("invalid_metadata") from None
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise PacsError("invalid_metadata")
            return payload

    def instances(self, study: str, series: str) -> list[dict]:
        result = []
        while True:
            batch = self.query(f"/studies/{uid(study)}/series/{uid(series)}/instances", {"limit": 100, "offset": len(result)})
            if not batch:
                break
            result.extend(batch)
            if len(result) > self.settings.pacs_max_instances:
                raise PacsError("instance_limit")
            if len({value(item, "00080018") for item in result}) != len(result):
                raise PacsError("invalid_instance_manifest")
        if not result:
            raise PacsError("empty_series")
        return result

    def retrieve(self, study: str, series: str, instance: str, target: Path, check) -> tuple[int, str]:
        prefix = self.settings.pacs_wado_prefix
        url = self.settings.pacs_base_url.rstrip("/") + ("/" + prefix.strip("/") if prefix else "")
        url += f"/studies/{uid(study)}/series/{uid(series)}/instances/{uid(instance)}"
        self.session.headers["Accept"] = 'multipart/related; type="application/dicom"; transfer-syntax=*'
        count = 0
        parts = 0
        ended = False
        digest = hashlib.sha256()
        try:
            with self._get(url, accept='multipart/related; type="application/dicom"; transfer-syntax=*') as response, target.open("wb") as output:
                media, options = parse_options_header(response.headers.get("Content-Type", ""))

                def begin():
                    nonlocal parts
                    parts += 1
                    if parts > 1:
                        raise PacsError("unexpected_instance")

                def data(chunk, start, end):
                    output.write(chunk[start:end])
                    digest.update(chunk[start:end])

                def finish():
                    nonlocal ended
                    ended = True

                parser = None
                if media == b"multipart/related" and options.get(b"boundary"):
                    parser = MultipartParser(options[b"boundary"], {"on_part_begin": begin, "on_part_data": data, "on_end": finish})
                elif media != b"application/dicom":
                    raise PacsError("unsupported_media_type")
                for chunk in response.iter_content(65536):
                    count += len(chunk)
                    check(len(chunk))
                    if count > self.settings.pacs_max_instance_bytes:
                        raise PacsError("instance_size_limit")
                    if parser:
                        parser.write(chunk)
                    else:
                        data(chunk, 0, len(chunk))
                if parser and (not ended or parts != 1):
                    raise PacsError("truncated_instance")
            return count, digest.hexdigest()
        except requests.RequestException:
            raise PacsError("connection_failed") from None
        finally:
            self.session.headers.pop("Accept", None)
