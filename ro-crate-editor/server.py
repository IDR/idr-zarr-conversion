#!/usr/bin/env python3
"""Local web server for the BIA RO-Crate editor.

Serves the static editor UI and proxies read-only calls to the IDR OMERO JSON
API and NCBI taxonomy services. It can also run the bia-ro-crate-validator on
a temporary directory built from the editor's export.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from idr_client import get_child_files, load_study, ncbi_taxon

RO_CRATE_EDITOR_DIR = os.path.dirname(os.path.abspath(__file__))


def find_bia_validator() -> str | None:
    """Return the bia-ro-crate executable path if it can be located."""
    candidates = [
        os.environ.get("BIA_RO_CRATE_VALIDATOR"),
        shutil.which("bia-ro-crate"),
        os.path.expanduser("~/micromamba/envs/crate/bin/bia-ro-crate"),
        os.path.expanduser("~/.micromamba/envs/crate/bin/bia-ro-crate"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


class Handler(SimpleHTTPRequestHandler):
    # Serve static files from this directory.
    directory = RO_CRATE_EDITOR_DIR

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            self.handle_api()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/validate":
            self.handle_validate()
        else:
            self.send_error(404, "Unknown POST route")

    def handle_api(self):
        parsed = urlparse(self.path)
        route = parsed.path
        qs = parse_qs(parsed.query)
        try:
            if route == "/api/study":
                study_input = (qs.get("q", [""])[0] or qs.get("url", [""])[0]).strip()
                if not study_input:
                    raise ValueError("Provide a study URL or name with ?q= or ?url=")
                data = load_study(study_input)
                self.send_json(data)
            elif route == "/api/files":
                container_type = qs.get("type", [""])[0]
                child_id = int(qs.get("id", ["0"])[0])
                child_name = qs.get("name", [""])[0]
                if not container_type or not child_id:
                    raise ValueError("Provide ?type= and ?id=")
                files = get_child_files(container_type, child_id, child_name)
                self.send_json({"files": files})
            elif route == "/api/ncbi":
                name = qs.get("q", [""])[0]
                if not name:
                    raise ValueError("Provide ?q=<organism name>")
                taxon = ncbi_taxon(name)
                self.send_json(taxon if taxon else {})
            else:
                self.send_error(404, "Unknown API route")
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, status=500)

    def handle_validate(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                raise ValueError("Empty body")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            crate_json = payload.get("ro_crate_metadata")
            file_list = payload.get("file_list")
            if crate_json is None:
                raise ValueError("Provide ro_crate_metadata in the JSON body")

            validator = find_bia_validator()
            if not validator:
                raise RuntimeError(
                    "bia-ro-crate validator not found. "
                    "Install it or set BIA_RO_CRATE_VALIDATOR to the executable path."
                )

            with tempfile.TemporaryDirectory() as tmpdir:
                crate_path = Path(tmpdir) / "ro-crate-metadata.json"
                crate_path.write_text(
                    json.dumps(crate_json, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if file_list:
                    (Path(tmpdir) / "file_list.tsv").write_text(
                        file_list, encoding="utf-8"
                    )

                proc = subprocess.run(
                    [validator, "validate", str(tmpdir), "--report-json"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            # The validator prints a JSON report on stdout and warnings on stderr.
            # Logging lines may appear before the JSON, so extract the JSON object.
            start = proc.stdout.find("{")
            if start == -1:
                raise RuntimeError(
                    f"Validator did not return JSON:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
                )
            try:
                report = json.loads(proc.stdout[start:])
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Validator did not return JSON:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
                ) from exc

            self.send_json({
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "report": report,
                "stderr": proc.stderr,
            })
        except Exception as exc:  # noqa: BLE001
            self.send_json({"error": str(exc)}, status=500)

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"RO-Crate editor server running at http://localhost:{port}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
