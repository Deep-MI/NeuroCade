"""Exercise release health checks against a server requiring both bridge headers."""

import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


def test_wait_for_http_sends_all_bridge_headers() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            authorized = (
                self.headers.get("Authorization") == "Bearer test-token"
                and self.headers.get("X-NeuroCade-Launch-ID") == "release-test"
            )
            self.send_response(200 if authorized else 403)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = Path(__file__).resolve().parents[1] / "scripts/release/wait_for_http.sh"
    try:
        result = subprocess.run(
            [
                "bash", str(script), f"http://127.0.0.1:{server.server_port}/v1/health",
                "1", "0", "Authorization: Bearer test-token",
                "X-NeuroCade-Launch-ID: release-test",
            ],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
