"""Serve the built NeuroCade client with SPA route fallback."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class SpaHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, **kwargs):
        self.static_root = Path(directory).resolve()
        super().__init__(*args, directory=directory, **kwargs)

    def send_head(self):
        """Serve index.html for missing GET paths so client-side routes load."""
        path = self.translate_path(self.path)
        requested = Path(path)
        if not requested.exists() and self.command in {"GET", "HEAD"}:
            index = self.static_root / "index.html"
            if index.exists():
                self.path = "/index.html"
        return super().send_head()

    def end_headers(self) -> None:
        """Attach basic browser security headers to every response."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()


def main() -> None:
    """Start a local HTTP server for a built NeuroCade client bundle."""
    parser = argparse.ArgumentParser(description="Serve the built NeuroCade client with SPA fallback.")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()

    directory = Path(args.directory).resolve()
    if not (directory / "index.html").is_file():
        raise SystemExit(f"Static client build missing: {directory / 'index.html'}")

    def handler(*handler_args, **handler_kwargs):
        return SpaHandler(*handler_args, directory=str(directory), **handler_kwargs)

    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving NeuroCade static client from {directory} at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
