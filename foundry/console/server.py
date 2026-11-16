"""Run the console on your laptop: python -m foundry.console.server --storyline week12 --port 8080

--storyline blank     the canonical week-12 store, nothing run yet (press "Run Monday" in the page)
--storyline week12 | f11_monday | one_sender   start from the end of a scripted storyline
Standard library only (http.server); one process, one runtime, state kept in memory and in runs/console/.
"""
from __future__ import annotations

import argparse
import json
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from foundry.console.api import ConsoleAPI

STATIC = Path(__file__).resolve().parent / "static"


def make_runtime(storyline: str, folder: Path):
    from foundry.data.generate import canonical_store
    from foundry.orchestrator import TowerRuntime
    from foundry.orchestrator.scenarios import STORYLINES
    from foundry.profile import load_profile
    from foundry.store import Store
    if folder.exists():
        shutil.rmtree(folder)
    if storyline == "blank":
        return TowerRuntime(canonical_store(), load_profile("gfb"), folder=folder)
    if storyline == "blank1":
        return TowerRuntime(Store.from_position(), load_profile("gfb"), folder=folder)
    rt, _ = STORYLINES[storyline][1](folder)
    return rt


def serve(api: ConsoleAPI, port: int) -> None:
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, payload, ctype: str = "application/json") -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path.startswith("/api/"):
                return self._send(*api.handle("GET", u.path, dict(parse_qsl(u.query)), headers=dict(self.headers)))
            f = STATIC / ("index.html" if u.path in ("/", "") else u.path.lstrip("/"))
            if f.is_file() and STATIC in f.resolve().parents:
                ctype = {"html": "text/html", "js": "text/javascript", "css": "text/css", "json": "application/json"}.get(f.suffix[1:], "application/octet-stream")
                return self._send(200, f.read_bytes(), ctype + "; charset=utf-8")
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "body is not JSON"})
            return self._send(*api.handle("POST", u.path, body=body, headers=dict(self.headers)))

        def log_message(self, fmt, *args):
            pass

    print(f"GFB tower console on http://localhost:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--storyline", default="blank", choices=["blank", "blank1", "week12", "f11_monday", "one_sender"])
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--folder", default="runs/console")
    a = ap.parse_args(argv)
    serve(ConsoleAPI(make_runtime(a.storyline, Path(a.folder))), a.port)


if __name__ == "__main__":
    main()
