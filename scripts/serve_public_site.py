"""Serve the packaged site on loopback with the public host's GeoJSON MIME type."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[1] / "frontend/dist")
    args = parser.parse_args()
    SimpleHTTPRequestHandler.extensions_map = SimpleHTTPRequestHandler.extensions_map | {
        ".geojson": "application/json; charset=utf-8",
    }
    handler = partial(SimpleHTTPRequestHandler, directory=str(args.dist))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
