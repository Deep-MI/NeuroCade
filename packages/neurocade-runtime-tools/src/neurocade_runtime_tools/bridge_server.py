"""Authenticated HTTP transport for the native runtime bridge."""

from __future__ import annotations

import hmac
import json
import logging
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .bridge import BridgeRuntime
from .protocol import MAX_REQUEST_BYTES, require_protocol

logger = logging.getLogger(__name__)


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        runtime: BridgeRuntime,
        token: str,
        *,
        launch_id: str,
    ):
        super().__init__(address, BridgeHandler)
        self.runtime = runtime
        self.token = token
        self.launch_id = launch_id


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return False
        launch_id = self.headers.get("X-NeuroCade-Launch-ID", "")
        if not hmac.compare_digest(launch_id.encode(), self.server.launch_id.encode()):
            self._json(HTTPStatus.CONFLICT, {"error": "launch session mismatch"})
            return False
        return True

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise OverflowError("Request body exceeds 1 MiB")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        if self.path == "/v1/health":
            self._json(HTTPStatus.OK, self.server.runtime.health())
            return
        if self.path.startswith("/v1/runs/"):
            run = self.server.runtime.get(self.path.removeprefix("/v1/runs/"))
            if run is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
            else:
                self._json(HTTPStatus.OK, run.public())
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        try:
            body = self._body()
            if self.path == "/v1/runs":
                run, created = self.server.runtime.start(body)
                self._json(HTTPStatus.ACCEPTED if created else HTTPStatus.OK, run.public())
                return
            if self.path == "/v1/capabilities/resolve":
                require_protocol(body)
                self._json(HTTPStatus.OK, self.server.runtime.resolve_capability(body.get("image")))
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except OverflowError as exc:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": str(exc)})
        except FileExistsError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except (ValueError, TypeError, KeyError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:  # noqa: BLE001
            logger.exception("runtime_bridge.request_failed path=%s", self.path)
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "runtime bridge request failed"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        if not self.path.startswith("/v1/runs/"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        run = self.server.runtime.cancel(self.path.removeprefix("/v1/runs/"))
        if run is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
        else:
            self._json(HTTPStatus.OK, run.public())


def read_token(path: Path) -> str:
    if path.stat().st_mode & 0o077:
        raise RuntimeError("Bridge token file permissions must be 0600")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 43:
        raise RuntimeError("Bridge token must contain at least 256 bits")
    return token


def serve_bridge(
    *,
    backend: str,
    data_root: Path,
    image_dir: Path,
    host: str,
    port: int,
    token_file: Path,
    launch_id: str,
) -> int:
    runtime = BridgeRuntime(backend=backend, data_root=data_root, image_dir=image_dir, launch_id=launch_id)
    server = BridgeHTTPServer(
        (host, port),
        runtime,
        read_token(token_file),
        launch_id=launch_id,
    )

    def request_shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        runtime.shutdown()
        server.server_close()
    return 0
