"""Tests unitaires et d'intégration pour Noteor Sync Manager (Option A Docker Compose)."""

from __future__ import annotations

import json
import py_compile
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER_DIR = REPO_ROOT / "sync-server-manager"
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))

import gui_web  # noqa: E402
import manager_core  # noqa: E402


class SyncManagerTests(unittest.TestCase):
    def test_python_files_compile_cleanly(self) -> None:
        for fname in ("manager_core.py", "gui_pyqt.py", "gui_web.py", "main.py"):
            py_compile.compile(str(MANAGER_DIR / fname), doraise=True)

    def test_detect_real_sync_server_dir(self) -> None:
        detected = manager_core.detect_sync_server_dir(REPO_ROOT)
        self.assertIsNotNone(detected)
        self.assertEqual(detected, (REPO_ROOT / "sync-server").resolve())
        self.assertTrue(manager_core.is_valid_sync_server_dir(detected))

    def test_compose_read_and_update_preserves_structure(self) -> None:
        src = REPO_ROOT / "sync-server" / "compose.yaml"
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "compose.yaml"
            shutil.copy2(src, dst)

            initial = manager_core.read_compose_config(dst)
            self.assertEqual(initial.noteor_data_dir, "/data")
            self.assertEqual(initial.host_port, 8787)
            self.assertIn("https://noteor-pwa.pages.dev", initial.noteor_cors_origins)

            merged_cors = manager_core.build_cors_origins(
                "https://mon-noteor.pages.dev/",
                "http://localhost:3000, https://mon-noteor.pages.dev",
            )
            self.assertEqual(merged_cors, "https://mon-noteor.pages.dev,http://localhost:3000")

            new_cfg = manager_core.ComposeConfig(
                noteor_data_dir="/srv/noteor-data",
                noteor_cors_origins=merged_cors,
                host_port=8899,
            )
            manager_core.update_compose_file(dst, new_cfg)

            reloaded = manager_core.read_compose_config(dst)
            self.assertEqual(reloaded.noteor_data_dir, "/srv/noteor-data")
            self.assertEqual(reloaded.noteor_cors_origins, merged_cors)
            self.assertEqual(reloaded.host_port, 8899)

            raw = dst.read_text(encoding="utf-8")
            self.assertIn("caddy:", raw)
            self.assertIn("noteor_sync_data:/data", raw)
            self.assertIn('"127.0.0.1:8899:8787"', raw)

    def test_check_deps_script_json_and_text(self) -> None:
        script = MANAGER_DIR / "scripts" / "check_deps.sh"
        sync_dir = REPO_ROOT / "sync-server"

        res_json = subprocess.run(
            ["bash", str(script), str(sync_dir), "8787", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(res_json.stdout)
        self.assertIn("checks", payload)
        ids = {c["id"]: c for c in payload["checks"]}
        self.assertIn("docker_cli", ids)
        self.assertIn("docker_daemon", ids)
        self.assertIn("docker_compose", ids)
        self.assertIn("curl", ids)
        self.assertIn("sync_dir", ids)
        self.assertTrue(ids["sync_dir"]["ok"])

        report_via_core = manager_core.run_dependency_check(sync_dir, port=8787)
        self.assertEqual(len(report_via_core["checks"]), len(payload["checks"]))

    def test_check_healthz_live_and_offline(self) -> None:
        class MockHealthHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/healthz":
                    body = b'{"status":"ok","version":"1.1.0"}'
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

        srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHealthHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            ok_res = manager_core.check_healthz(port=port)
            self.assertTrue(ok_res["ok"])
            self.assertEqual(ok_res["status_code"], 200)
            self.assertEqual(ok_res["data"]["version"], "1.1.0")
        finally:
            srv.shutdown()
            srv.server_close()

        down_res = manager_core.check_healthz(port=port)
        self.assertFalse(down_res["ok"])
        self.assertEqual(down_res["status_code"], 0)

    def test_web_gui_http_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_sync = Path(tmp) / "sync-server"
            tmp_sync.mkdir()
            shutil.copy2(REPO_ROOT / "sync-server" / "compose.yaml", tmp_sync / "compose.yaml")
            shutil.copy2(REPO_ROOT / "sync-server" / "Dockerfile", tmp_sync / "Dockerfile")
            shutil.copy2(REPO_ROOT / "sync-server" / "pyproject.toml", tmp_sync / "pyproject.toml")
            (tmp_sync / "app").mkdir()
            (tmp_sync / "app" / "main.py").write_text("# stub\n", encoding="utf-8")

            server = gui_web.create_web_server(sync_dir=str(tmp_sync), port=0)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            base = f"http://127.0.0.1:{port}"
            try:
                with urllib.request.urlopen(f"{base}/") as r:
                    html = r.read().decode("utf-8")
                    self.assertIn("Noteor Sync Manager", html)
                    self.assertIn("#4F46E5", html)

                with urllib.request.urlopen(f"{base}/icon.svg") as r:
                    svg = r.read().decode("utf-8")
                    self.assertIn("<svg", svg)

                with urllib.request.urlopen(f"{base}/api/state") as r:
                    st = json.loads(r.read().decode("utf-8"))
                    self.assertEqual(st["host_port"], 8787)

                req = urllib.request.Request(
                    f"{base}/api/config",
                    data=json.dumps(
                        {
                            "sync_dir": str(tmp_sync),
                            "noteor_data_dir": "/data/custom",
                            "noteor_cors_origins": "https://demo-pwa.pages.dev,http://localhost:3000",
                            "host_port": 8790,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req) as r:
                    save_res = json.loads(r.read().decode("utf-8"))
                    self.assertTrue(save_res["ok"])

                updated = manager_core.read_compose_config(tmp_sync / "compose.yaml")
                self.assertEqual(updated.noteor_data_dir, "/data/custom")
                self.assertEqual(updated.host_port, 8790)

                with mock.patch("gui_web.start_sync_container", return_value=(0, "Started noteor-sync-server")):
                    req_start = urllib.request.Request(
                        f"{base}/api/compose/start",
                        data=json.dumps({"sync_dir": str(tmp_sync)}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_start) as r:
                        start_res = json.loads(r.read().decode("utf-8"))
                        self.assertTrue(start_res["ok"])
                        self.assertIn("Started noteor-sync-server", start_res["output"])

                with mock.patch("gui_web.fetch_container_logs", return_value=(0, "INFO: Uvicorn running on 0.0.0.0:8787")):
                    with urllib.request.urlopen(
                        f"{base}/api/logs?sync_dir={tmp_sync}&service=noteor-sync&tail=50"
                    ) as r:
                        logs_res = json.loads(r.read().decode("utf-8"))
                        self.assertTrue(logs_res["ok"])
                        self.assertIn("Uvicorn running", logs_res["output"])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
