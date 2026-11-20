"""Zero-dependency HTTP server wrapper around SyncService (Python stdlib ThreadingHTTPServer).

Implements the exact same REST endpoints and status codes as `app/main.py` (FastAPI),
allowing immediate local startup and full live HTTP integration testing even when
FastAPI/uvicorn are not installed in the sandbox.
"""

from __future__ import annotations

import json
import os
import re
import select
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .services.sync_service import MAX_HTTP_CHUNK_SIZE, ApiError, SyncService
from .storage.database import SyncStorage

MAX_JSON_BODY_BYTES = 16 * 1024 * 1024  # 16 MiB
DEFAULT_DEV_CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def _resolve_cors_origins(cors_origin: str | None = None) -> list[str]:
    raw = cors_origin if cors_origin is not None else os.environ.get("NOTEOR_CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins if origins else list(DEFAULT_DEV_CORS_ORIGINS)


def make_handler(service: SyncService, cors_origin: str | None = None) -> type[BaseHTTPRequestHandler]:
    allowed_origins = _resolve_cors_origins(cors_origin)

    class SyncHttpHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send_cors(self) -> None:
            req_origin = self.headers.get("Origin")
            if "*" in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", "*")
            elif req_origin and req_origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", req_origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Content-Range")

        def _send_json(self, status: int, data: Any, close_conn: bool = False) -> None:
            raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._send_cors()
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            if close_conn:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(raw)
            self.wfile.flush()

        def _drain_unread_bounded(self, max_drain: int = 8 * 1024 * 1024) -> None:
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                return
            drained = 0
            while drained < max_drain:
                readable, _, _ = select.select([self.connection], [], [], 0.05)
                if not readable:
                    break
                try:
                    chunk = self.connection.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                drained += len(chunk)

        def _stream_file(self, status: int, file_path: Path) -> None:
            size = file_path.stat().st_size
            self.send_response(status)
            self._send_cors()
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _read_body(self, max_bytes: int, too_large_code: str) -> bytes:
            raw_len = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_len)
            except ValueError as exc:
                raise ValueError("Invalid Content-Length header") from exc
            if length < 0:
                raise ValueError("Negative Content-Length header")
            if length > max_bytes:
                raise ApiError(
                    413,
                    too_large_code,
                    f"Request body length {length} exceeds limit {max_bytes}",
                )
            return self.rfile.read(length) if length > 0 else b""

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _dispatch(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)
            auth = self.headers.get("Authorization")

            try:
                is_media_put = method == "PUT" and bool(
                    re.match(r"^/api/v1/vaults/([^/]+)/media/uploads/([^/]+)$", path)
                )
                raw_body = self._read_body(
                    MAX_HTTP_CHUNK_SIZE if is_media_put else MAX_JSON_BODY_BYTES,
                    "CHUNK_TOO_LARGE" if is_media_put else "PAYLOAD_TOO_LARGE",
                )

                def _json_body() -> dict[str, Any]:
                    return json.loads(raw_body.decode("utf-8")) if raw_body else {}

                if method == "GET" and path == "/healthz":
                    self._send_json(200, {"status": "ok", "version": "1.1.0"})
                    return

                if method == "GET" and path == "/api/v1/health":
                    self._send_json(200, {"status": "ok", "version": "1.1.0", "api_version": 1})
                    return

                if method == "POST" and path == "/api/v1/vaults":
                    self._send_json(201, service.create_vault(_json_body()))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)$", path)
                if m and method == "GET":
                    vid = m.group(1)
                    service.authenticate(auth, vid)
                    self._send_json(200, service.get_vault_info(vid))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/operations:batch$", path)
                if m and method == "POST":
                    vid = m.group(1)
                    dev = service.authenticate(auth, vid)
                    self._send_json(200, service.push_operations_batch(vid, dev, _json_body()))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/operations$", path)
                if m and method == "GET":
                    vid = m.group(1)
                    service.authenticate(auth, vid)
                    since = qs.get("since_seq", ["0"])[0]
                    limit = int(qs.get("limit", ["200"])[0])
                    self._send_json(200, service.pull_operations(vid, since, limit))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/keys/epochs$", path)
                if m and method == "GET":
                    vid = m.group(1)
                    dev = service.authenticate(auth, vid)
                    self._send_json(200, service.list_epoch_keys(vid, dev))
                    return
                if m and method == "POST":
                    vid = m.group(1)
                    dev = service.authenticate(auth, vid)
                    self._send_json(201, service.rotate_epoch(vid, dev, _json_body()))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/devices$", path)
                if m and method == "GET":
                    vid = m.group(1)
                    service.authenticate(auth, vid)
                    self._send_json(200, service.list_devices(vid))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/devices/([^/]+)/revoke$", path)
                if m and method == "POST":
                    vid, did = m.group(1), m.group(2)
                    dev = service.authenticate(auth, vid)
                    self._send_json(200, service.revoke_device(vid, dev, did))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/pairing$", path)
                if m and method == "POST":
                    vid = m.group(1)
                    dev = service.authenticate(auth, vid)
                    self._send_json(201, service.create_pairing(vid, dev, _json_body()))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/pairing/([^/]+)/join$", path)
                if m and method == "POST":
                    self._send_json(200, service.join_pairing(m.group(1), m.group(2), _json_body()))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/pairing/([^/]+)/approve$", path)
                if m and method == "POST":
                    vid, pid = m.group(1), m.group(2)
                    dev = service.authenticate(auth, vid)
                    self._send_json(200, service.approve_pairing(vid, dev, pid, _json_body()))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/pairing/([^/]+)/confirm$", path)
                if m and method == "POST":
                    self._send_json(200, service.confirm_pairing(m.group(1), m.group(2), _json_body()))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/pairing/([^/]+)$", path)
                if m and method == "GET":
                    self._send_json(200, service.get_pairing_status(m.group(1), m.group(2)))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/media/uploads$", path)
                if m and method == "POST":
                    vid = m.group(1)
                    service.authenticate(auth, vid)
                    self._send_json(200, service.init_media_upload(vid, _json_body()))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/media/uploads/([^/]+)/complete$", path)
                if m and method == "POST":
                    vid, mid = m.group(1), m.group(2)
                    service.authenticate(auth, vid)
                    self._send_json(200, service.complete_media_upload(vid, mid))
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/media/uploads/([^/]+)$", path)
                if m and method == "PUT":
                    vid, mid = m.group(1), m.group(2)
                    service.authenticate(auth, vid)
                    self._send_json(
                        200,
                        service.upload_media_chunk(
                            vid, mid, self.headers.get("Content-Range"), raw_body
                        ),
                    )
                    return

                m = re.match(r"^/api/v1/vaults/([^/]+)/media/([^/]+)$", path)
                if m and method == "GET":
                    vid, mid = m.group(1), m.group(2)
                    service.authenticate(auth, vid)
                    blob_path = service.get_media_blob(vid, mid)
                    self._stream_file(200, blob_path)
                    return
                if m and method == "DELETE":
                    vid, mid = m.group(1), m.group(2)
                    service.authenticate(auth, vid)
                    self._send_json(200, service.delete_media(vid, mid))
                    return

                self._send_json(404, {"error": {"code": "NOT_FOUND", "message": "Route not found", "details": {}}})
            except ApiError as exc:
                is_413 = exc.status == 413
                self._send_json(exc.status, exc.to_dict(), close_conn=is_413)
                if is_413:
                    self._drain_unread_bounded()
            except ValueError as exc:
                self._send_json(400, {"error": {"code": "INVALID_PAYLOAD", "message": str(exc), "details": {}}})
            except Exception as exc:  # pragma: no cover
                self._send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": str(exc), "details": {}}})

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

    return SyncHttpHandler


def start_background_server(
    db_path: str | Path,
    blob_dir: str | Path,
    host: str = "127.0.0.1",
    port: int = 0,
    cors_origin: str | None = None,
) -> tuple[ThreadingHTTPServer, SyncStorage, str]:
    storage = SyncStorage(db_path, blob_dir)
    service = SyncService(storage)
    httpd = ThreadingHTTPServer((host, port), make_handler(service, cors_origin=cors_origin))
    actual_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, storage, f"http://{host}:{actual_port}"


if __name__ == "__main__":
    data_dir = Path(os.environ.get("NOTEOR_DATA_DIR", "./data"))
    storage = SyncStorage(data_dir / "noteor_sync.db", data_dir / "blobs")
    service = SyncService(storage)
    port = int(os.environ.get("PORT", "8787"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(service))
    print(f"Noteor Sync stdlib server listening on http://0.0.0.0:{port}")
    try:
        httpd.serve_forever()
    finally:
        storage.close()
