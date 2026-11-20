"""FastAPI entrypoint for Noteor Sync Server v1.1."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .services.sync_service import MAX_HTTP_CHUNK_SIZE, ApiError, SyncService
from .storage.database import SyncStorage

logger = logging.getLogger("noteor.sync")
MAX_JSON_BODY_BYTES = 16 * 1024 * 1024  # 16 MiB
DEFAULT_DEV_CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def _resolve_cors_origins(cors_origins: list[str] | None) -> list[str]:
    if cors_origins is not None:
        origins = [o.strip() for o in cors_origins if o.strip()]
    else:
        raw = os.environ.get("NOTEOR_CORS_ORIGINS", "")
        origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        logger.warning("NOTEOR_CORS_ORIGINS unset; restricting CORS to localhost dev origins")
        return list(DEFAULT_DEV_CORS_ORIGINS)
    if "*" in origins:
        if os.environ.get("NOTEOR_ENV", "").lower() == "production":
            raise RuntimeError("Wildcard CORS '*' is forbidden when NOTEOR_ENV=production")
        logger.warning("Wildcard CORS '*' enabled; configure explicit origins for production")
    return origins


def create_app(
    db_path: str | Path | None = None,
    blob_dir: str | Path | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    data_dir = Path(os.environ.get("NOTEOR_DATA_DIR", "./data"))
    storage = SyncStorage(
        db_path or (data_dir / "noteor_sync.db"),
        blob_dir or (data_dir / "blobs"),
    )
    service = SyncService(storage)

    app = FastAPI(title="Noteor Sync Server", version="1.1.0")
    origins = _resolve_cors_origins(cors_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Content-Range"],
    )

    @app.middleware("http")
    async def security_and_size_middleware(request: Request, call_next: Any) -> Any:
        raw_cl = request.headers.get("content-length")
        if raw_cl is not None:
            try:
                cl = int(raw_cl)
                if cl < 0:
                    raise ValueError("Negative Content-Length")
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "INVALID_PAYLOAD", "message": "Invalid Content-Length header", "details": {}}},
                )
            is_media_put = request.method == "PUT" and "/media/uploads/" in request.url.path
            max_allowed = MAX_HTTP_CHUNK_SIZE if is_media_put else MAX_JSON_BODY_BYTES
            if cl > max_allowed:
                code = "CHUNK_TOO_LARGE" if is_media_put else "PAYLOAD_TOO_LARGE"
                return JSONResponse(
                    status_code=413,
                    content={"error": {"code": code, "message": f"Request body exceeds {max_allowed} bytes", "details": {}}},
                )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/api/v1/") and "/media/" not in request.url.path:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_PAYLOAD", "message": "Malformed request payload or parameters", "details": {"errors": exc.errors()}}},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_PAYLOAD", "message": str(exc), "details": {}}},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": "1.1.0"}

    @app.get("/api/v1/health")
    def api_health() -> dict[str, Any]:
        return {"status": "ok", "version": "1.1.0", "api_version": 1}

    @app.post("/api/v1/vaults", status_code=201)
    async def create_vault(body: dict[str, Any]) -> dict[str, Any]:
        return service.create_vault(body)

    @app.get("/api/v1/vaults/{vault_id}")
    async def get_vault(
        vault_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        service.authenticate(authorization, vault_id)
        return service.get_vault_info(vault_id)

    @app.post("/api/v1/vaults/{vault_id}/operations:batch")
    async def push_batch(
        vault_id: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        dev = service.authenticate(authorization, vault_id)
        return service.push_operations_batch(vault_id, dev, body)

    @app.get("/api/v1/vaults/{vault_id}/operations")
    async def pull_ops(
        vault_id: str,
        since_seq: str = Query(default="0"),
        limit: int = Query(default=200, ge=1, le=200),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        service.authenticate(authorization, vault_id)
        return service.pull_operations(vault_id, since_seq, limit)

    @app.get("/api/v1/vaults/{vault_id}/keys/epochs")
    async def list_epochs(
        vault_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        dev = service.authenticate(authorization, vault_id)
        return service.list_epoch_keys(vault_id, dev)

    @app.post("/api/v1/vaults/{vault_id}/keys/epochs", status_code=201)
    async def rotate_epoch(
        vault_id: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        dev = service.authenticate(authorization, vault_id)
        return service.rotate_epoch(vault_id, dev, body)

    @app.get("/api/v1/vaults/{vault_id}/devices")
    async def list_devices(
        vault_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        service.authenticate(authorization, vault_id)
        return service.list_devices(vault_id)

    @app.post("/api/v1/vaults/{vault_id}/devices/{device_id}/revoke")
    async def revoke_device(
        vault_id: str,
        device_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        dev = service.authenticate(authorization, vault_id)
        return service.revoke_device(vault_id, dev, device_id)

    @app.post("/api/v1/vaults/{vault_id}/pairing", status_code=201)
    async def create_pairing(
        vault_id: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        dev = service.authenticate(authorization, vault_id)
        return service.create_pairing(vault_id, dev, body)

    @app.post("/api/v1/vaults/{vault_id}/pairing/{pairing_id}/join")
    async def join_pairing(vault_id: str, pairing_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return service.join_pairing(vault_id, pairing_id, body)

    @app.get("/api/v1/vaults/{vault_id}/pairing/{pairing_id}")
    async def get_pairing(vault_id: str, pairing_id: str) -> dict[str, Any]:
        return service.get_pairing_status(vault_id, pairing_id)

    @app.post("/api/v1/vaults/{vault_id}/pairing/{pairing_id}/approve")
    async def approve_pairing(
        vault_id: str,
        pairing_id: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        dev = service.authenticate(authorization, vault_id)
        return service.approve_pairing(vault_id, dev, pairing_id, body)

    @app.post("/api/v1/vaults/{vault_id}/pairing/{pairing_id}/confirm")
    async def confirm_pairing(vault_id: str, pairing_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return service.confirm_pairing(vault_id, pairing_id, body)

    @app.post("/api/v1/vaults/{vault_id}/media/uploads")
    async def init_media(
        vault_id: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        service.authenticate(authorization, vault_id)
        return service.init_media_upload(vault_id, body)

    @app.put("/api/v1/vaults/{vault_id}/media/uploads/{media_id}")
    async def put_media_chunk(
        vault_id: str,
        media_id: str,
        request: Request,
        content_range: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        service.authenticate(authorization, vault_id)
        chunks: list[bytes] = []
        total_bytes = 0
        async for part in request.stream():
            total_bytes += len(part)
            if total_bytes > MAX_HTTP_CHUNK_SIZE:
                raise ApiError(
                    413,
                    "CHUNK_TOO_LARGE",
                    f"HTTP chunk exceeds limit {MAX_HTTP_CHUNK_SIZE}",
                )
            chunks.append(part)
        return service.upload_media_chunk(vault_id, media_id, content_range, b"".join(chunks))

    @app.post("/api/v1/vaults/{vault_id}/media/uploads/{media_id}/complete")
    async def complete_media(
        vault_id: str,
        media_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        service.authenticate(authorization, vault_id)
        return service.complete_media_upload(vault_id, media_id)

    @app.get("/api/v1/vaults/{vault_id}/media/{media_id}")
    async def get_media(
        vault_id: str,
        media_id: str,
        authorization: str | None = Header(default=None),
    ) -> FileResponse:
        service.authenticate(authorization, vault_id)
        path = service.get_media_blob(vault_id, media_id)
        return FileResponse(path, media_type="application/octet-stream")

    @app.delete("/api/v1/vaults/{vault_id}/media/{media_id}")
    async def delete_media(
        vault_id: str,
        media_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        service.authenticate(authorization, vault_id)
        return service.delete_media(vault_id, media_id)

    return app


app = create_app()
