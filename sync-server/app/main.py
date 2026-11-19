"""FastAPI entrypoint for Noteor Sync Server v1.1."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .services.sync_service import ApiError, SyncService
from .storage.database import SyncStorage


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
    origins = cors_origins or [
        o.strip() for o in os.environ.get("NOTEOR_CORS_ORIGINS", "*").split(",") if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Content-Range"],
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_PAYLOAD", "message": str(exc), "details": {}}},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": "1.1.0"}

    @app.post("/api/v1/vaults", status_code=201)
    async def create_vault(body: dict[str, Any]) -> dict[str, Any]:
        return service.create_vault(body)

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
        chunk = await request.body()
        return service.upload_media_chunk(vault_id, media_id, content_range, chunk)

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
