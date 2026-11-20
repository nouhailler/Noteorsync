"""Noteor Desktop SyncEngine & HTTP Client (Phase 5).

Mirrors `web/src/sync/engine.ts` with 100% protocol and cryptographic parity:
- Vault bootstrap, Epoch key unsealing & rotation
- Chunked NTRM1 media encryption/upload & download/decryption
- Batch push (`operations:batch`) with idempotent ack handling
- Cursor pull (`operations?since_seq=...`) with DAG concurrency & conflict detection
- Missing-dependency deferral queue (`sync_pending_ops`) + iterative draining
- Conflict resolution (`keep_local`, `keep_remote`, `keep_both`, `manual_merge`)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import crypto as pyc
from .sqlite_sync_adapter import (
    DesktopSyncDatabase,
    normalize_tag_name,
    normalize_utc_timestamp,
    now_utc_iso,
)


class SyncHttpError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.details = details or {}


@dataclass
class VaultKeyring:
    vault_id: str
    device_id: str
    current_key_epoch: int = 1
    device_public_key: bytes | None = None
    device_private_key: bytes | None = None
    epoch_root_keys: dict[int, bytes] = field(default_factory=dict)

    @classmethod
    def from_vsk(
        cls,
        vault_id: str,
        device_id: str,
        vsk: bytes,
        current_key_epoch: int = 1,
        device_keypair: tuple[bytes, bytes] | None = None,
        extra_epochs: dict[int, bytes] | None = None,
    ) -> "VaultKeyring":
        keys = {1: pyc.derive_initial_epoch1_root_key(vsk, vault_id)}
        if extra_epochs:
            keys.update(extra_epochs)
        return cls(
            vault_id=vault_id,
            device_id=device_id,
            current_key_epoch=current_key_epoch,
            device_public_key=device_keypair[0] if device_keypair else None,
            device_private_key=device_keypair[1] if device_keypair else None,
            epoch_root_keys=keys,
        )


@dataclass
class SyncResult:
    pushed_count: int = 0
    pulled_count: int = 0
    conflicts_created: int = 0
    media_uploaded: int = 0
    media_downloaded: int = 0


class SyncHttpClient:
    """HTTP client over stdlib urllib (works without external httpx/requests dependencies)."""

    def __init__(
        self,
        base_url: str,
        access_token: str = "",
        opener: Callable[[urllib.request.Request], tuple[int, dict[str, str], bytes]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self._custom_opener = opener

    def _raw_request(
        self,
        method: str,
        path: str,
        body_bytes: bytes | None = None,
        headers: dict[str, str] | None = None,
        require_auth: bool = True,
    ) -> tuple[int, bytes]:
        hdrs = dict(headers or {})
        if require_auth and self.access_token:
            hdrs["Authorization"] = f"Bearer {self.access_token}"
        req = urllib.request.Request(f"{self.base_url}{path}", data=body_bytes, headers=hdrs, method=method)

        if self._custom_opener is not None:
            status, _, data = self._custom_opener(req)
            if status >= 400:
                self._raise_http_error(status, data)
            return status, data

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            err_body = exc.read()
            self._raise_http_error(exc.code, err_body)
        raise RuntimeError("Unreachable")

    @staticmethod
    def _raise_http_error(status: int, data: bytes) -> None:
        try:
            parsed = json.loads(data.decode("utf-8")) if data else {}
        except Exception:
            parsed = {}
        err = parsed.get("error", parsed)
        raise SyncHttpError(
            status,
            err.get("code", f"HTTP_{status}"),
            err.get("message", f"HTTP error {status}"),
            err.get("details", {}),
        )

    def _request_json(
        self, method: str, path: str, body: Any = None, require_auth: bool = True
    ) -> dict[str, Any]:
        hdrs: dict[str, str] = {}
        data: bytes | None = None
        if body is not None:
            hdrs["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        _, raw = self._raw_request(method, path, body_bytes=data, headers=hdrs, require_auth=require_auth)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def bootstrap_vault(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", "/api/v1/vaults", payload, require_auth=False)

    def get_vault_info(self, vault_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/v1/vaults/{vault_id}")

    def push_operations(self, vault_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request_json("POST", f"/api/v1/vaults/{vault_id}/operations:batch", {"operations": operations})

    def pull_operations(self, vault_id: str, since_seq: str, limit: int = 200) -> dict[str, Any]:
        q = urllib.parse.urlencode({"since_seq": since_seq, "limit": str(limit)})
        return self._request_json("GET", f"/api/v1/vaults/{vault_id}/operations?{q}")

    def list_epoch_keys(self, vault_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/v1/vaults/{vault_id}/keys/epochs")

    def rotate_epoch(self, vault_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", f"/api/v1/vaults/{vault_id}/keys/epochs", payload)

    def list_devices(self, vault_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/v1/vaults/{vault_id}/devices")

    def revoke_device(self, vault_id: str, target_device_id: str) -> dict[str, Any]:
        return self._request_json("POST", f"/api/v1/vaults/{vault_id}/devices/{target_device_id}/revoke", {})

    def create_pairing_session(self, vault_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", f"/api/v1/vaults/{vault_id}/pairing", payload)

    def join_pairing_session(self, vault_id: str, pairing_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json(
            "POST", f"/api/v1/vaults/{vault_id}/pairing/{pairing_id}/join", payload, require_auth=False
        )

    def get_pairing_status(self, vault_id: str, pairing_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/v1/vaults/{vault_id}/pairing/{pairing_id}", require_auth=False)

    def approve_pairing_session(self, vault_id: str, pairing_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", f"/api/v1/vaults/{vault_id}/pairing/{pairing_id}/approve", payload)

    def confirm_pairing_session(self, vault_id: str, pairing_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json(
            "POST", f"/api/v1/vaults/{vault_id}/pairing/{pairing_id}/confirm", payload, require_auth=False
        )

    def init_media_upload(self, vault_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", f"/api/v1/vaults/{vault_id}/media/uploads", payload)

    def upload_media_chunk(
        self, vault_id: str, media_id: str, chunk: bytes, start_offset: int, total_size: int
    ) -> dict[str, Any]:
        end_offset = start_offset + len(chunk) - 1
        _, raw = self._raw_request(
            "PUT",
            f"/api/v1/vaults/{vault_id}/media/uploads/{media_id}",
            body_bytes=chunk,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {start_offset}-{end_offset}/{total_size}",
            },
        )
        return json.loads(raw.decode("utf-8")) if raw else {}

    def complete_media_upload(self, vault_id: str, media_id: str) -> dict[str, Any]:
        return self._request_json("POST", f"/api/v1/vaults/{vault_id}/media/uploads/{media_id}/complete", {})

    def download_media_blob(self, vault_id: str, media_id: str) -> bytes:
        _, raw = self._raw_request("GET", f"/api/v1/vaults/{vault_id}/media/{media_id}")
        return raw


class DesktopSyncEngine:
    def __init__(
        self,
        db: DesktopSyncDatabase,
        client: SyncHttpClient,
        keyring: VaultKeyring,
        media_dir: str | Path | None = None,
    ) -> None:
        self.db = db
        self.client = client
        self.keyring = keyring
        self.media_dir = Path(media_dir).resolve() if media_dir else None
        if self.media_dir:
            self.media_dir.mkdir(parents=True, exist_ok=True)

    def _safe_attachment_dest(self, media_id: str, file_name: str) -> Path:
        if not self.media_dir:
            raise ValueError("media_dir is not configured")
        pyc.assert_uuid(media_id)
        base_name = os.path.basename(str(file_name or "blob").replace("\\", "/")).strip()
        if not base_name or base_name in (".", ".."):
            base_name = "blob"
        safe_name = "".join("_" if ch in '<>:"/|?*\x00' else ch for ch in base_name)[:120]
        candidate = (self.media_dir / f"{media_id}_{safe_name}").resolve()
        if not candidate.is_relative_to(self.media_dir):
            raise ValueError("Unsafe media destination path")
        return candidate

    def _ensure_epoch_root_key(self, epoch: int) -> bytes:
        if epoch in self.keyring.epoch_root_keys:
            return self.keyring.epoch_root_keys[epoch]
        self.refresh_epoch_keys()
        if epoch not in self.keyring.epoch_root_keys:
            raise RuntimeError(f"Missing EpochRootKey for epoch {epoch}")
        return self.keyring.epoch_root_keys[epoch]

    def refresh_epoch_keys(self) -> None:
        if not self.keyring.device_public_key or not self.keyring.device_private_key:
            return
        res = self.client.list_epoch_keys(self.keyring.vault_id)
        self.keyring.current_key_epoch = int(res["current_key_epoch"])
        self.db.set_state("current_key_epoch", str(self.keyring.current_key_epoch))
        for rec in res.get("epochs", []):
            ep = int(rec["key_epoch"])
            if rec.get("wrapped_epoch_key") and ep not in self.keyring.epoch_root_keys:
                self.keyring.epoch_root_keys[ep] = pyc.unseal_epoch_key_for_device(
                    self.keyring.device_public_key,
                    self.keyring.device_private_key,
                    rec["wrapped_epoch_key"],
                    self.keyring.vault_id,
                    ep,
                    self.keyring.device_id,
                )

    def sync_now(self, download_media: bool = True) -> SyncResult:
        res = SyncResult()
        self.refresh_epoch_keys()
        self._push_pending_media(res)
        self._push_pending_operations(res)
        self._pull_and_apply_operations(res)
        if download_media:
            self.download_pending_media(res)
        return res

    def _push_pending_media(self, res: SyncResult) -> None:
        rows = self.db.conn.execute(
            "SELECT * FROM sync_operations WHERE status='pending' AND operation_type='media.register' ORDER BY CAST(device_seq AS INTEGER) ASC"
        ).fetchall()
        for op in rows:
            att = self.db.conn.execute(
                "SELECT * FROM attachments WHERE sync_id=?", (op["entity_id"],)
            ).fetchone()
            if not att or att["media_status"] == "uploaded":
                continue
            src_path = Path(att["filepath"])
            if not src_path.is_file():
                continue
            plaintext = src_path.read_bytes()
            epoch = self.keyring.current_key_epoch
            _, k_media_epoch = pyc.derive_epoch_keys(
                self._ensure_epoch_root_key(epoch), self.keyring.vault_id, epoch
            )
            k_media = pyc.derive_media_object_key(k_media_epoch, att["media_id"])
            encrypted = pyc.encrypt_media_stream(k_media, att["media_id"], epoch, plaintext)
            enc_sha = pyc.sha256_hex(encrypted)
            chunk_size = 1024 * 1024

            init_res = self.client.init_media_upload(
                self.keyring.vault_id,
                {
                    "media_id": att["media_id"],
                    "encrypted_size": len(encrypted),
                    "encrypted_sha256": enc_sha,
                    "key_epoch": epoch,
                    "chunk_size": chunk_size,
                },
            )
            if init_res["status"] != "completed":
                offset = int(init_res.get("received_bytes", 0))
                while offset < len(encrypted):
                    end = min(offset + chunk_size, len(encrypted))
                    self.client.upload_media_chunk(
                        self.keyring.vault_id, att["media_id"], encrypted[offset:end], offset, len(encrypted)
                    )
                    offset = end
                self.client.complete_media_upload(self.keyring.vault_id, att["media_id"])

            payload_data = json.loads(op["payload_data_json"])
            payload_data["key_epoch"] = epoch
            payload_data["encrypted_size"] = len(encrypted)
            payload_data["encrypted_sha256"] = enc_sha
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE attachments SET key_epoch=?, encrypted_size=?, encrypted_sha256=?, media_status='uploaded' WHERE id=?",
                    (epoch, len(encrypted), enc_sha, att["id"]),
                )
                c.execute(
                    "UPDATE sync_operations SET key_epoch=?, payload_data_json=? WHERE id=?",
                    (epoch, json.dumps(payload_data, separators=(",", ":"), ensure_ascii=False), op["id"]),
                )
            res.media_uploaded += 1

    def _push_pending_operations(self, res: SyncResult) -> None:
        pending = self.db.conn.execute(
            "SELECT * FROM sync_operations WHERE status='pending' ORDER BY CAST(device_seq AS INTEGER) ASC"
        ).fetchall()
        if not pending:
            return
        epoch = self.keyring.current_key_epoch
        k_op, _ = pyc.derive_epoch_keys(self._ensure_epoch_root_key(epoch), self.keyring.vault_id, epoch)

        for i in range(0, len(pending), 100):
            batch = pending[i : i + 100]
            envelopes = []
            for op in batch:
                payload = {
                    "payload_version": 1,
                    "operation_type": op["operation_type"],
                    "entity_type": op["entity_type"],
                    "entity_id": op["entity_id"],
                    "revision_id": op["revision_id"],
                    "parent_revision_ids": json.loads(op["parent_revision_ids_json"]),
                    "created_at": op["created_at"],
                    "data": json.loads(op["payload_data_json"]),
                }
                env = pyc.encrypt_operation_envelope(
                    k_op,
                    {
                        "vault_id": self.keyring.vault_id,
                        "device_id": self.keyring.device_id,
                        "operation_id": op["operation_id"],
                        "device_seq": op["device_seq"],
                        "key_epoch": epoch,
                    },
                    payload,
                )
                envelopes.append(env)

            resp = self.client.push_operations(self.keyring.vault_id, envelopes)
            ack_map = {a["operation_id"]: a["server_seq"] for a in resp.get("accepted", [])}
            with self.db.transaction() as c:
                for op in batch:
                    s_seq = ack_map.get(op["operation_id"])
                    if s_seq is not None:
                        c.execute(
                            "UPDATE sync_operations SET status='acked', key_epoch=?, server_seq=? WHERE id=?",
                            (epoch, s_seq, op["id"]),
                        )
                        res.pushed_count += 1

    def _pull_and_apply_operations(self, res: SyncResult) -> None:
        cursor = self.db.get_state("last_server_seq", "0") or "0"
        last_seen_seq = int(cursor)
        while True:
            page = self.client.pull_operations(self.keyring.vault_id, cursor, limit=200)
            ops = page.get("operations", [])
            if ops:
                for env in ops:
                    if env.get("vault_id") != self.keyring.vault_id:
                        raise ValueError("Pulled operation vault_id mismatch")
                    srv_seq_int = int(env["server_seq"])
                    if srv_seq_int <= last_seen_seq:
                        raise ValueError(
                            f"Non-monotonic server_seq {srv_seq_int} after {last_seen_seq}"
                        )
                    last_seen_seq = srv_seq_int
                    if env["device_id"] == self.keyring.device_id:
                        res.pulled_count += 1
                        continue
                    ep = int(env["key_epoch"])
                    k_op, _ = pyc.derive_epoch_keys(self._ensure_epoch_root_key(ep), self.keyring.vault_id, ep)
                    payload = pyc.decrypt_operation_envelope(k_op, env)
                    outcome = self.apply_decrypted_operation(
                        env["operation_id"], env["server_seq"], env["device_id"], payload
                    )
                    if outcome == "conflict":
                        res.conflicts_created += 1
                    res.pulled_count += 1
                self._drain_pending_deps(res)
            cursor = page["next_since_seq"]
            self.db.set_state("last_server_seq", cursor)
            if not page.get("has_more"):
                break

    def apply_decrypted_operation(
        self, operation_id: str, server_seq: str, remote_device_id: str, payload: dict[str, Any]
    ) -> str:
        with self.db.transaction() as c:
            seen = c.execute(
                "SELECT 1 FROM sync_revisions WHERE entity_type=? AND entity_id=? AND revision_id=?",
                (payload["entity_type"], payload["entity_id"], payload["revision_id"]),
            ).fetchone()
            if seen:
                return "skipped"

            missing = self._find_missing_deps(c, payload)
            if missing:
                c.execute(
                    """INSERT INTO sync_pending_ops
                       (operation_id, server_seq, device_id, entity_type, entity_id, missing_deps_json, decrypted_payload_json, received_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(operation_id) DO UPDATE SET missing_deps_json=excluded.missing_deps_json""",
                    (
                        operation_id,
                        server_seq,
                        remote_device_id,
                        payload["entity_type"],
                        payload["entity_id"],
                        json.dumps(missing),
                        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                        now_utc_iso(),
                    ),
                )
                return "deferred"

            local_heads = [
                r["revision_id"]
                for r in c.execute(
                    "SELECT revision_id FROM sync_revisions WHERE entity_type=? AND entity_id=? AND is_head=1",
                    (payload["entity_type"], payload["entity_id"]),
                ).fetchall()
            ]
            parent_set = set(payload.get("parent_revision_ids", []))
            if not local_heads or all(h in parent_set for h in local_heads):
                self._apply_fast_forward(c, remote_device_id, payload)
                return "applied"
            return self._handle_concurrent(c, remote_device_id, local_heads, payload)

    def _find_missing_deps(self, c: Any, payload: dict[str, Any]) -> list[str]:
        if payload["operation_type"] in ("entity.delete", "media.delete"):
            return []
        d = payload["data"]
        et = payload["entity_type"]
        missing: list[str] = []
        if et == "note":
            if d.get("category_id") and not c.execute(
                "SELECT 1 FROM categories WHERE sync_id=?", (d["category_id"],)
            ).fetchone():
                missing.append(f"category:{d['category_id']}")
            if d.get("folder_id") and not c.execute(
                "SELECT 1 FROM folders WHERE sync_id=?", (d["folder_id"],)
            ).fetchone():
                missing.append(f"folder:{d['folder_id']}")
        elif et == "category" and d.get("parent_id"):
            if not c.execute("SELECT 1 FROM categories WHERE sync_id=?", (d["parent_id"],)).fetchone():
                missing.append(f"category:{d['parent_id']}")
        elif et == "folder" and d.get("parent_id"):
            if not c.execute("SELECT 1 FROM folders WHERE sync_id=?", (d["parent_id"],)).fetchone():
                missing.append(f"folder:{d['parent_id']}")
        elif et == "relation":
            if not c.execute("SELECT 1 FROM notes WHERE sync_id=?", (d.get("note_id"),)).fetchone():
                missing.append(f"note:{d.get('note_id')}")
            if not c.execute("SELECT 1 FROM tags WHERE sync_id=?", (d.get("tag_id"),)).fetchone():
                missing.append(f"tag:{d.get('tag_id')}")
        elif et == "attachment":
            if not c.execute("SELECT 1 FROM notes WHERE sync_id=?", (d.get("note_id"),)).fetchone():
                missing.append(f"note:{d.get('note_id')}")
        return missing

    def _handle_concurrent(
        self, c: Any, remote_device_id: str, local_heads: list[str], payload: dict[str, Any]
    ) -> str:
        if payload["entity_type"] == "relation":
            self._apply_fast_forward(c, remote_device_id, payload)
            return "applied"

        if payload["entity_type"] == "note":
            local = c.execute("SELECT * FROM notes WHERE sync_id=?", (payload["entity_id"],)).fetchone()
            tomb = c.execute(
                "SELECT deleted_at FROM sync_tombstones WHERE entity_key=?",
                (f"note:{payload['entity_id']}",),
            ).fetchone()
            if local:
                cat_s = self.db._resolve_sync_id(c, "categories", local["category_id"])
                fld_s = self.db._resolve_sync_id(c, "folders", local["folder_id"])
                local_snap = {
                    "title": local["title"],
                    "content": local["content"],
                    "category_id": cat_s,
                    "folder_id": fld_s,
                    "is_deleted": bool(local["is_deleted"]),
                    "deleted_at": local["deleted_at"],
                    "updated_at": local["updated_at"],
                }
            else:
                local_snap = {"deleted": True, "deleted_at": tomb["deleted_at"] if tomb else None}

            d = payload["data"]
            if payload["operation_type"] == "entity.delete":
                remote_snap = {"deleted": True, "deleted_at": d.get("deleted_at", payload["created_at"])}
            else:
                remote_snap = {
                    "title": str(d.get("title", "")),
                    "content": str(d.get("content", "")),
                    "category_id": d.get("category_id"),
                    "folder_id": d.get("folder_id"),
                    "is_deleted": True if payload["operation_type"] == "entity.trash" else bool(d.get("is_deleted")),
                    "deleted_at": d.get("deleted_at"),
                    "updated_at": normalize_utc_timestamp(d.get("updated_at") or payload["created_at"]),
                }

            c.execute(
                """INSERT INTO sync_revisions
                   (entity_type, entity_id, revision_id, parent_revision_ids_json, is_head, operation_type, created_at, device_id)
                   VALUES ('note', ?, ?, ?, 1, ?, ?, ?)""",
                (
                    payload["entity_id"],
                    payload["revision_id"],
                    json.dumps(payload.get("parent_revision_ids", [])),
                    payload["operation_type"],
                    payload["created_at"],
                    remote_device_id,
                ),
            )
            if (
                local
                and payload["operation_type"] != "entity.delete"
                and local_snap["title"] == remote_snap["title"]
                and local_snap["content"] == remote_snap["content"]
                and local_snap["category_id"] == remote_snap["category_id"]
                and local_snap["folder_id"] == remote_snap["folder_id"]
                and local_snap["is_deleted"] == remote_snap["is_deleted"]
            ):
                return "applied"

            c.execute(
                """INSERT INTO sync_conflicts
                   (conflict_id, entity_type, entity_id, local_revision_id, remote_revision_id,
                    local_snapshot_json, remote_snapshot_json, remote_device_id, detected_at, status)
                   VALUES (?, 'note', ?, ?, ?, ?, ?, ?, ?, 'open')""",
                (
                    pyc.generate_uuid_v4(),
                    payload["entity_id"],
                    local_heads[0] if local_heads else "",
                    payload["revision_id"],
                    json.dumps(local_snap, ensure_ascii=False),
                    json.dumps(remote_snap, ensure_ascii=False),
                    remote_device_id,
                    now_utc_iso(),
                ),
            )
            return "conflict"

        c.execute(
            """INSERT INTO sync_conflicts
               (conflict_id, entity_type, entity_id, local_revision_id, remote_revision_id,
                local_snapshot_json, remote_snapshot_json, remote_device_id, detected_at, status)
               VALUES (?, ?, ?, ?, ?, '{"note":"Concurrent update"}', ?, ?, ?, 'open')""",
            (
                pyc.generate_uuid_v4(),
                payload["entity_type"],
                payload["entity_id"],
                local_heads[0] if local_heads else "",
                payload["revision_id"],
                json.dumps(payload["data"], ensure_ascii=False),
                remote_device_id,
                now_utc_iso(),
            ),
        )
        return "conflict"

    def _apply_fast_forward(self, c: Any, remote_device_id: str, payload: dict[str, Any]) -> None:
        et = payload["entity_type"]
        eid = payload["entity_id"]
        c.execute(
            "UPDATE sync_revisions SET is_head=0 WHERE entity_type=? AND entity_id=? AND is_head=1",
            (et, eid),
        )
        c.execute(
            """INSERT INTO sync_revisions
               (entity_type, entity_id, revision_id, parent_revision_ids_json, is_head, operation_type, created_at, device_id)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                et,
                eid,
                payload["revision_id"],
                json.dumps(payload.get("parent_revision_ids", [])),
                payload["operation_type"],
                payload["created_at"],
                remote_device_id,
            ),
        )
        d = payload["data"]
        op = payload["operation_type"]

        if et == "category":
            existing = c.execute("SELECT id FROM categories WHERE sync_id=?", (eid,)).fetchone()
            if op == "entity.delete":
                if existing:
                    c.execute("UPDATE notes SET category_id=NULL WHERE category_id=?", (existing["id"],))
                    c.execute("DELETE FROM categories WHERE id=?", (existing["id"],))
                c.execute(
                    "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'category', ?, ?, ?)",
                    (f"category:{eid}", eid, payload["revision_id"], d.get("deleted_at", payload["created_at"])),
                )
                return
            p_row = (
                c.execute("SELECT id FROM categories WHERE sync_id=?", (d["parent_id"],)).fetchone()
                if d.get("parent_id")
                else None
            )
            p_id = p_row["id"] if p_row else None
            c_at = normalize_utc_timestamp(d.get("created_at") or payload["created_at"])
            u_at = normalize_utc_timestamp(d.get("updated_at") or payload["created_at"])
            if existing:
                c.execute(
                    """UPDATE categories SET revision_id=?, parent_revision_ids_json=?, name=?, parent_id=?,
                       color=?, sort_order=?, updated_at=? WHERE id=?""",
                    (
                        payload["revision_id"],
                        json.dumps(payload.get("parent_revision_ids", [])),
                        str(d.get("name", "")),
                        p_id,
                        str(d.get("color", "#6366F1")),
                        int(d.get("sort_order", 0)),
                        u_at,
                        existing["id"],
                    ),
                )
            else:
                c.execute(
                    """INSERT INTO categories
                       (sync_id, revision_id, parent_revision_ids_json, name, parent_id, color, sort_order, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        eid,
                        payload["revision_id"],
                        json.dumps(payload.get("parent_revision_ids", [])),
                        str(d.get("name", "")),
                        p_id,
                        str(d.get("color", "#6366F1")),
                        int(d.get("sort_order", 0)),
                        c_at,
                        u_at,
                    ),
                )
            return

        if et == "folder":
            existing = c.execute("SELECT id FROM folders WHERE sync_id=?", (eid,)).fetchone()
            if op == "entity.delete":
                if existing:
                    c.execute("UPDATE notes SET folder_id=NULL WHERE folder_id=?", (existing["id"],))
                    c.execute("DELETE FROM folders WHERE id=?", (existing["id"],))
                c.execute(
                    "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'folder', ?, ?, ?)",
                    (f"folder:{eid}", eid, payload["revision_id"], d.get("deleted_at", payload["created_at"])),
                )
                return
            p_row = (
                c.execute("SELECT id FROM folders WHERE sync_id=?", (d["parent_id"],)).fetchone()
                if d.get("parent_id")
                else None
            )
            p_id = p_row["id"] if p_row else None
            c_at = normalize_utc_timestamp(d.get("created_at") or payload["created_at"])
            u_at = normalize_utc_timestamp(d.get("updated_at") or payload["created_at"])
            if existing:
                c.execute(
                    "UPDATE folders SET revision_id=?, parent_revision_ids_json=?, name=?, parent_id=?, updated_at=? WHERE id=?",
                    (
                        payload["revision_id"],
                        json.dumps(payload.get("parent_revision_ids", [])),
                        str(d.get("name", "")),
                        p_id,
                        u_at,
                        existing["id"],
                    ),
                )
            else:
                c.execute(
                    """INSERT INTO folders (sync_id, revision_id, parent_revision_ids_json, name, parent_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        eid,
                        payload["revision_id"],
                        json.dumps(payload.get("parent_revision_ids", [])),
                        str(d.get("name", "")),
                        p_id,
                        c_at,
                        u_at,
                    ),
                )
            return

        if et == "tag":
            existing = c.execute("SELECT id FROM tags WHERE sync_id=?", (eid,)).fetchone()
            if op == "entity.delete":
                if existing:
                    c.execute("DELETE FROM tags WHERE id=?", (existing["id"],))
                return
            norm = normalize_tag_name(str(d.get("name", "")))
            by_name = c.execute("SELECT * FROM tags WHERE name=?", (norm,)).fetchone()
            if by_name and by_name["sync_id"] != eid:
                if eid < by_name["sync_id"]:
                    c.execute(
                        "UPDATE tags SET sync_id=?, revision_id=?, color=? WHERE id=?",
                        (eid, payload["revision_id"], str(d.get("color", by_name["color"])), by_name["id"]),
                    )
                return
            c_at = normalize_utc_timestamp(d.get("created_at") or payload["created_at"])
            u_at = normalize_utc_timestamp(d.get("updated_at") or payload["created_at"])
            if existing:
                c.execute(
                    "UPDATE tags SET revision_id=?, name=?, color=?, updated_at=? WHERE id=?",
                    (payload["revision_id"], norm, str(d.get("color", "#6366F1")), u_at, existing["id"]),
                )
            else:
                c.execute(
                    """INSERT INTO tags (sync_id, revision_id, parent_revision_ids_json, name, color, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        eid,
                        payload["revision_id"],
                        json.dumps(payload.get("parent_revision_ids", [])),
                        norm,
                        str(d.get("color", "#6366F1")),
                        c_at,
                        u_at,
                    ),
                )
            return

        if et == "note":
            existing = c.execute("SELECT id, created_at FROM notes WHERE sync_id=?", (eid,)).fetchone()
            if op == "entity.delete":
                if existing:
                    c.execute("DELETE FROM notes WHERE id=?", (existing["id"],))
                c.execute(
                    "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'note', ?, ?, ?)",
                    (f"note:{eid}", eid, payload["revision_id"], d.get("deleted_at", payload["created_at"])),
                )
                return
            cat_row = (
                c.execute("SELECT id FROM categories WHERE sync_id=?", (d["category_id"],)).fetchone()
                if d.get("category_id")
                else None
            )
            fld_row = (
                c.execute("SELECT id FROM folders WHERE sync_id=?", (d["folder_id"],)).fetchone()
                if d.get("folder_id")
                else None
            )
            is_del = 1 if op == "entity.trash" else (0 if op == "entity.restore" else int(bool(d.get("is_deleted"))))
            del_at = normalize_utc_timestamp(d.get("deleted_at") or payload["created_at"]) if is_del else None
            c_at = normalize_utc_timestamp(d.get("created_at") or (existing["created_at"] if existing else payload["created_at"]))
            u_at = normalize_utc_timestamp(d.get("updated_at") or payload["created_at"])
            meta_json = json.dumps(d.get("metadata") or {}, ensure_ascii=False)
            if existing:
                c.execute(
                    """UPDATE notes SET revision_id=?, parent_revision_ids_json=?, title=?, content=?,
                       category_id=?, folder_id=?, updated_at=?, is_deleted=?, deleted_at=?, metadata_json=?
                       WHERE id=?""",
                    (
                        payload["revision_id"],
                        json.dumps(payload.get("parent_revision_ids", [])),
                        str(d.get("title", "")),
                        str(d.get("content", "")),
                        cat_row["id"] if cat_row else None,
                        fld_row["id"] if fld_row else None,
                        u_at,
                        is_del,
                        del_at,
                        meta_json,
                        existing["id"],
                    ),
                )
            else:
                c.execute(
                    """INSERT INTO notes
                       (sync_id, revision_id, parent_revision_ids_json, title, content, category_id, folder_id,
                        created_at, updated_at, is_deleted, deleted_at, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        eid,
                        payload["revision_id"],
                        json.dumps(payload.get("parent_revision_ids", [])),
                        str(d.get("title", "")),
                        str(d.get("content", "")),
                        cat_row["id"] if cat_row else None,
                        fld_row["id"] if fld_row else None,
                        c_at,
                        u_at,
                        is_del,
                        del_at,
                        meta_json,
                    ),
                )
            return

        if et == "relation":
            existing = c.execute("SELECT note_id, tag_id FROM note_tags WHERE sync_id=?", (eid,)).fetchone()
            if op == "entity.delete":
                if existing:
                    c.execute("DELETE FROM note_tags WHERE sync_id=?", (eid,))
                c.execute(
                    "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'relation', ?, ?, ?)",
                    (f"relation:{eid}", eid, payload["revision_id"], d.get("deleted_at", payload["created_at"])),
                )
                return
            n_row = c.execute("SELECT id FROM notes WHERE sync_id=?", (d.get("note_id"),)).fetchone()
            t_row = c.execute("SELECT id FROM tags WHERE sync_id=?", (d.get("tag_id"),)).fetchone()
            if not n_row or not t_row:
                return
            c.execute(
                """INSERT OR IGNORE INTO note_tags
                   (note_id, tag_id, sync_id, revision_id, parent_revision_ids_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    n_row["id"],
                    t_row["id"],
                    eid,
                    payload["revision_id"],
                    json.dumps(payload.get("parent_revision_ids", [])),
                    normalize_utc_timestamp(d.get("created_at") or payload["created_at"]),
                ),
            )
            return

        if et == "attachment":
            existing = c.execute("SELECT id FROM attachments WHERE sync_id=?", (eid,)).fetchone()
            if op in ("media.delete", "entity.delete"):
                if existing:
                    c.execute("DELETE FROM attachments WHERE id=?", (existing["id"],))
                c.execute(
                    "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'attachment', ?, ?, ?)",
                    (f"attachment:{eid}", eid, payload["revision_id"], d.get("deleted_at", payload["created_at"])),
                )
                return
            n_row = c.execute("SELECT id FROM notes WHERE sync_id=?", (d.get("note_id"),)).fetchone()
            if not n_row:
                return
            c_at = normalize_utc_timestamp(d.get("created_at") or payload["created_at"])
            if existing:
                c.execute(
                    """UPDATE attachments SET revision_id=?, encrypted_size=?, encrypted_sha256=?,
                       key_epoch=?, thumbnail_data_url=? WHERE id=?""",
                    (
                        payload["revision_id"],
                        int(d.get("encrypted_size", 0)),
                        str(d.get("encrypted_sha256", "")),
                        int(d.get("key_epoch", 1)),
                        d.get("thumbnail_data_url"),
                        existing["id"],
                    ),
                )
            else:
                dest_path = (
                    str(self._safe_attachment_dest(str(d["media_id"]), str(d.get("file_name", "blob"))))
                    if self.media_dir
                    else ""
                )
                c.execute(
                    """INSERT INTO attachments
                       (sync_id, media_id, revision_id, parent_revision_ids_json, note_id, type, filename,
                        filepath, duration, created_at, mime_type, original_size, encrypted_size,
                        encrypted_sha256, key_epoch, media_status, thumbnail_data_url)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_download', ?)""",
                    (
                        eid,
                        str(d["media_id"]),
                        payload["revision_id"],
                        json.dumps(payload.get("parent_revision_ids", [])),
                        n_row["id"],
                        str(d.get("media_type", "image")),
                        str(d.get("file_name", "attachment")),
                        dest_path,
                        d.get("duration"),
                        c_at,
                        str(d.get("mime_type", "application/octet-stream")),
                        int(d.get("original_size", 0)),
                        int(d.get("encrypted_size", 0)),
                        str(d.get("encrypted_sha256", "")),
                        int(d.get("key_epoch", 1)),
                        d.get("thumbnail_data_url"),
                    ),
                )

    def _drain_pending_deps(self, res: SyncResult) -> None:
        while True:
            rows = self.db.conn.execute("SELECT * FROM sync_pending_ops ORDER BY id ASC").fetchall()
            progressed = False
            for r in rows:
                outcome = self.apply_decrypted_operation(
                    r["operation_id"],
                    r["server_seq"],
                    r["device_id"],
                    json.loads(r["decrypted_payload_json"]),
                )
                if outcome != "deferred":
                    with self.db.transaction() as c:
                        c.execute("DELETE FROM sync_pending_ops WHERE id=?", (r["id"],))
                    if outcome == "conflict":
                        res.conflicts_created += 1
                    progressed = True
            if not progressed:
                break

    def download_pending_media(self, res: SyncResult) -> None:
        if not self.media_dir:
            return
        rows = self.db.conn.execute(
            "SELECT * FROM attachments WHERE media_status='pending_download'"
        ).fetchall()
        for att in rows:
            epoch = int(att["key_epoch"] or 1)
            _, k_media_epoch = pyc.derive_epoch_keys(
                self._ensure_epoch_root_key(epoch), self.keyring.vault_id, epoch
            )
            k_media = pyc.derive_media_object_key(k_media_epoch, att["media_id"])
            encrypted = self.client.download_media_blob(self.keyring.vault_id, att["media_id"])
            if att["encrypted_sha256"] and pyc.sha256_hex(encrypted) != att["encrypted_sha256"]:
                raise ValueError(f"Downloaded media SHA-256 mismatch for {att['media_id']}")
            plain = pyc.decrypt_media_stream(k_media, att["media_id"], epoch, encrypted)
            pyc.assert_uuid(att["media_id"])
            candidate = Path(att["filepath"]).resolve() if att["filepath"] else (self.media_dir / att["media_id"]).resolve()
            out_path = candidate if candidate.is_relative_to(self.media_dir) else (self.media_dir / att["media_id"]).resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(plain)
            with self.db.transaction() as c:
                c.execute(
                    "UPDATE attachments SET filepath=?, original_size=?, media_status='available' WHERE id=?",
                    (str(out_path), len(plain), att["id"]),
                )
            res.media_downloaded += 1

    def resolve_conflict(
        self,
        conflict_id: str,
        choice: str,
        manual_data: dict[str, str] | None = None,
    ) -> None:
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT * FROM sync_conflicts WHERE conflict_id=? AND status='open'", (conflict_id,)
            ).fetchone()
            if not row:
                return
            local_snap = json.loads(row["local_snapshot_json"])
            remote_snap = json.loads(row["remote_snapshot_json"])
            parents = [p for p in (row["local_revision_id"], row["remote_revision_id"]) if p]
            ts = now_utc_iso()

            if row["entity_type"] == "note":
                note = c.execute("SELECT * FROM notes WHERE sync_id=?", (row["entity_id"],)).fetchone()
                if choice == "keep_both":
                    dup_sync_id = pyc.generate_uuid_v4()
                    dup_title = f"{remote_snap.get('title', 'Note')} (conflit)"
                    dup_content = str(remote_snap.get("content", ""))
                    rev, par = self.db._record_mutation_in_tx(
                        c,
                        operation_type="entity.upsert",
                        entity_type="note",
                        entity_id=dup_sync_id,
                        payload_data={
                            "title": dup_title,
                            "content": dup_content,
                            "category_id": None,
                            "folder_id": None,
                            "is_deleted": False,
                            "deleted_at": None,
                            "created_at": ts,
                            "updated_at": ts,
                            "metadata": {},
                        },
                        created_at=ts,
                        explicit_parents=[],
                    )
                    c.execute(
                        """INSERT INTO notes (sync_id, revision_id, parent_revision_ids_json, title, content, created_at, updated_at, is_deleted)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                        (dup_sync_id, rev, json.dumps(par), dup_title, dup_content, ts, ts),
                    )
                    if note:
                        m_rev, m_par = self.db._record_mutation_in_tx(
                            c,
                            operation_type="entity.upsert",
                            entity_type="note",
                            entity_id=row["entity_id"],
                            payload_data={
                                "title": note["title"],
                                "content": note["content"],
                                "category_id": self.db._resolve_sync_id(c, "categories", note["category_id"]),
                                "folder_id": self.db._resolve_sync_id(c, "folders", note["folder_id"]),
                                "is_deleted": bool(note["is_deleted"]),
                                "deleted_at": note["deleted_at"],
                                "created_at": note["created_at"],
                                "updated_at": ts,
                                "metadata": {},
                            },
                            created_at=ts,
                            explicit_parents=parents,
                        )
                        c.execute(
                            "UPDATE notes SET revision_id=?, parent_revision_ids_json=?, updated_at=? WHERE id=?",
                            (m_rev, json.dumps(m_par), ts, note["id"]),
                        )
                else:
                    if choice == "keep_local":
                        snap = local_snap
                    elif choice == "keep_remote":
                        snap = remote_snap
                    else:
                        snap = {**local_snap, **(manual_data or {}), "is_deleted": False, "deleted_at": None}
                    op_type = (
                        "entity.delete"
                        if snap.get("deleted")
                        else ("entity.trash" if snap.get("is_deleted") else "entity.upsert")
                    )
                    payload_data = (
                        {"deleted_at": ts}
                        if op_type == "entity.delete"
                        else {
                            "title": str(snap.get("title", "")),
                            "content": str(snap.get("content", "")),
                            "category_id": snap.get("category_id"),
                            "folder_id": snap.get("folder_id"),
                            "is_deleted": bool(snap.get("is_deleted")),
                            "deleted_at": ts if snap.get("is_deleted") else None,
                            "created_at": note["created_at"] if note else ts,
                            "updated_at": ts,
                            "metadata": {},
                        }
                    )
                    rev, par = self.db._record_mutation_in_tx(
                        c,
                        operation_type=op_type,
                        entity_type="note",
                        entity_id=row["entity_id"],
                        payload_data=payload_data,
                        created_at=ts,
                        explicit_parents=parents,
                    )
                    if op_type == "entity.delete" and note:
                        c.execute("DELETE FROM notes WHERE id=?", (note["id"],))
                    elif note:
                        c.execute(
                            """UPDATE notes SET revision_id=?, parent_revision_ids_json=?, title=?, content=?,
                               is_deleted=?, deleted_at=?, updated_at=? WHERE id=?""",
                            (
                                rev,
                                json.dumps(par),
                                payload_data["title"],
                                payload_data["content"],
                                int(payload_data["is_deleted"]),
                                payload_data["deleted_at"],
                                ts,
                                note["id"],
                            ),
                        )
                    else:
                        c.execute(
                            """INSERT INTO notes (sync_id, revision_id, parent_revision_ids_json, title, content,
                               is_deleted, deleted_at, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                row["entity_id"],
                                rev,
                                json.dumps(par),
                                payload_data["title"],
                                payload_data["content"],
                                int(payload_data["is_deleted"]),
                                payload_data["deleted_at"],
                                ts,
                                ts,
                            ),
                        )

            c.execute(
                "UPDATE sync_conflicts SET status='resolved', resolution=?, resolved_at=? WHERE id=?",
                (choice, ts, row["id"]),
            )

    def revoke_device_and_rotate_epoch(self, target_device_id: str) -> int:
        self.client.revoke_device(self.keyring.vault_id, target_device_id)
        devs = self.client.list_devices(self.keyring.vault_id)["devices"]
        active = [d for d in devs if d["status"] == "active" and d["device_id"] != target_device_id]
        new_epoch = self.keyring.current_key_epoch + 1
        new_root = os.urandom(32)
        wrapped = {}
        for d in active:
            pub = pyc.from_b64u(d["public_key_x25519"])
            wrapped[d["device_id"]] = pyc.seal_epoch_key_for_device(
                pub, self.keyring.vault_id, new_epoch, d["device_id"], new_root
            )
        resp = self.client.rotate_epoch(
            self.keyring.vault_id,
            {"new_key_epoch": new_epoch, "reason": "device_revoked", "wrapped_keys": wrapped},
        )
        ep = int(resp["current_key_epoch"])
        self.keyring.epoch_root_keys[ep] = new_root
        self.keyring.current_key_epoch = ep
        self.db.set_state("current_key_epoch", str(ep))
        return ep
