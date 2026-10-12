"""Serve only public review scenarios on loopback, excluding frozen source files."""

from __future__ import annotations

import argparse
import stat
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from render_report_review import SCENARIOS, safe


class ReviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parts = unquote(urlsplit(self.path).path).strip("/").split("/")
        if (
            len(parts) < 2
            or parts[0] not in SCENARIOS
            or any(part in ("", ".", "..", "frozen") or "\\" in part for part in parts)
        ):
            self.send_error(404)
            return
        try:
            candidate = safe(Path(self.directory).joinpath(*parts))
            info = candidate.stat()
            allowed = (
                stat.S_ISREG(info.st_mode)
                and info.st_nlink == 1
                and candidate.suffix in (".html", ".json", ".md", ".svg", ".png", ".woff2")
            )
        except (OSError, RuntimeError):
            allowed = False
        if not allowed:
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        self.send_error(405)

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path, help="New output of render_report_review.py")
    parser.add_argument(
        "--port", type=int, default=0, help="Loopback port; zero selects a free port"
    )
    args = parser.parse_args()
    directory = safe(args.review).resolve()
    if not (directory / "review-manifest.json").is_file():
        parser.error("A frozen review-manifest.json is required")
    handler = partial(ReviewHandler, directory=str(directory))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"Review URL: http://127.0.0.1:{server.server_port}", flush=True)
        print("Only public scenarios are served. Stop this server with Ctrl+C.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
