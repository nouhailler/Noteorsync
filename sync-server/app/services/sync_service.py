"""Noteor Sync Server protocol business logic (v1.1).

Framework-agnostic service class shared cleanly by both FastAPI (`main.py`)
and zero-dependency `ThreadingHTTPServer` (`stdlib_server.py`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..storage.database import SyncStorage, assert_uuid

_SEQ_RE = re.compile(r"^[1-9][0-9]*$")
_B64U_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_BATCH_SIZE = 100
MAX_PAIRING_ATTEMPTS = 5
MAX_HTTP_CHUNK_SIZE = 4 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include timezone offset")
    return dt


def _sha256_hex(raw: bytes | str) -> str:
    b = raw.encode("utf-8") if isinstance(raw, str) else raw
    return hashlib.sha256(b).hexdigest()


def _generate_access_token() -> str:
    import base64

    return "ntr_dev_" + base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class SyncService:
    def __init__(self, storage: SyncStorage) -> None:
        self.storage = storage

    # ── Authentication ────────────────────────────────────────────────────────

    def authenticate(self, auth_header: str | None, expected_vault_id: str | None = None) -> dict[str, Any]:
        if not auth_header or not auth_header.startswith("Bearer "):
            raise ApiError(401, "AUTH_REQUIRED", "Missing Bearer access token")
        token = auth_header[7:].strip()
        if not token:
            raise ApiError(401, "AUTH_REQUIRED", "Empty Bearer token")
        token_hash = _sha256_hex(token)
        row = self.storage.conn.execute(
            "SELECT * FROM devices WHERE token_hash_sha256=?", (token_hash,)
        ).fetchone()
        if not row:
            raise ApiError(401, "AUTH_INVALID", "Invalid device access token")
        if row["status"] != "active":
            raise ApiError(403, "DEVICE_REVOKED", "This device has been revoked")
        if expected_vault_id and row["vault_id"] != expected_vault_id:
            raise ApiError(403, "VAULT_MISMATCH", "Device does not belong to target vault")
        with self.storage.transaction() as c:
            c.execute("UPDATE devices SET last_seen_at=? WHERE device_id=?", (_now_iso(), row["device_id"]))
        return dict(row)

    # ── Vault Creation & Info ─────────────────────────────────────────────────

    def create_vault(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            vault_id = assert_uuid(body["vault_id"], "vault_id")
            device_id = assert_uuid(body["device_id"], "device_id")
        except (KeyError, ValueError) as exc:
            raise ApiError(400, "INVALID_PAYLOAD", str(exc)) from exc

        device_name = str(body.get("device_name") or "Device")[:128]
        device_type = str(body.get("device_type") or "pwa")
        if device_type not in ("desktop", "pwa", "mobile"):
            raise ApiError(400, "INVALID_PAYLOAD", "Invalid device_type")
        pub_key = str(body.get("public_key_x25519") or "")
        if not pub_key or not _B64U_RE.match(pub_key):
            raise ApiError(400, "INVALID_PAYLOAD", "Invalid public_key_x25519")

        token = _generate_access_token()
        token_hash = _sha256_hex(token)
        ts = _now_iso()
        vsk_env = body.get("vsk_recovery_envelope")
        vsk_env_json: str | None = None
        if vsk_env is not None:
            if not isinstance(vsk_env, dict):
                raise ApiError(400, "INVALID_PAYLOAD", "vsk_recovery_envelope must be an object")
            vsk_env_json = json.dumps(vsk_env, separators=(",", ":"), ensure_ascii=False)
            if len(vsk_env_json.encode("utf-8")) > 16384:
                raise ApiError(400, "INVALID_PAYLOAD", "vsk_recovery_envelope exceeds 16 KiB limit")

        with self.storage.transaction() as c:
            if c.execute("SELECT 1 FROM vaults WHERE vault_id=?", (vault_id,)).fetchone():
                raise ApiError(409, "VAULT_EXISTS", "Vault already initialized")
            c.execute(
                "INSERT INTO vaults(vault_id, created_at, current_key_epoch, vsk_recovery_envelope_json) VALUES (?, ?, 1, ?)",
                (vault_id, ts, vsk_env_json),
            )
            c.execute(
                """INSERT INTO devices
                   (device_id, vault_id, device_name, device_type, public_key_x25519, token_hash_sha256, status, last_device_seq, created_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', 0, ?, ?)""",
                (device_id, vault_id, device_name, device_type, pub_key, token_hash, ts, ts),
            )
            c.execute(
                """INSERT INTO epoch_keys
                   (vault_id, key_epoch, target_device_id, wrapped_epoch_key, created_by_device_id, reason, created_at)
                   VALUES (?, 1, ?, NULL, ?, 'initial', ?)""",
                (vault_id, device_id, device_id, ts),
            )
        return {
            "vault_id": vault_id,
            "device_id": device_id,
            "device_access_token": token,
            "current_key_epoch": 1,
        }

    def get_vault_info(self, vault_id: str) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        row = self.storage.conn.execute(
            "SELECT vault_id, current_key_epoch FROM vaults WHERE vault_id=?",
            (vault_id,),
        ).fetchone()
        if not row:
            raise ApiError(404, "VAULT_NOT_FOUND", "Vault not found")
        return {
            "vault_id": row["vault_id"],
            "protocol_version": 1,
            "current_key_epoch": int(row["current_key_epoch"]),
            "status": "active",
        }

    # ── Operations Batch Push & Pull ──────────────────────────────────────────

    def push_operations_batch(
        self, vault_id: str, auth_device: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        ops = body.get("operations")
        if not isinstance(ops, list) or len(ops) == 0:
            raise ApiError(400, "INVALID_PAYLOAD", "operations must be a non-empty list")
        if len(ops) > MAX_BATCH_SIZE:
            raise ApiError(400, "BATCH_TOO_LARGE", f"Max batch size is {MAX_BATCH_SIZE}")

        accepted: list[dict[str, Any]] = []
        with self.storage.transaction() as c:
            v_row = c.execute("SELECT next_server_seq FROM vaults WHERE vault_id=?", (vault_id,)).fetchone()
            if not v_row:
                raise ApiError(404, "VAULT_NOT_FOUND", "Vault not found")
            next_server_seq = int(v_row["next_server_seq"])

            dev_row = c.execute(
                "SELECT last_device_seq FROM devices WHERE device_id=?", (auth_device["device_id"],)
            ).fetchone()
            last_dev_seq = int(dev_row["last_device_seq"])

            for op in ops:
                try:
                    op_vault = assert_uuid(op["vault_id"], "vault_id")
                    op_dev = assert_uuid(op["device_id"], "device_id")
                    op_id = assert_uuid(op["operation_id"], "operation_id")
                    seq_str = str(op["device_seq"])
                    if not _SEQ_RE.match(seq_str):
                        raise ValueError("Invalid device_seq")
                    dev_seq = int(seq_str)
                    proto_ver = int(op["protocol_version"])
                    crypto_ver = int(op["crypto_version"])
                    key_epoch = int(op["key_epoch"])
                    nonce = str(op["nonce"])
                    ciphertext = str(op["ciphertext"])
                    if proto_ver != 1 or crypto_ver != 1 or key_epoch < 1:
                        raise ValueError("Unsupported envelope version/epoch")
                    if not _B64U_RE.match(nonce) or not _B64U_RE.match(ciphertext):
                        raise ValueError("Invalid base64url field")
                except (KeyError, ValueError, TypeError) as exc:
                    raise ApiError(400, "INVALID_PAYLOAD", f"Malformed envelope: {exc}") from exc

                if op_vault != vault_id or op_dev != auth_device["device_id"]:
                    raise ApiError(403, "DEVICE_MISMATCH", "Operation vault_id/device_id does not match caller")

                # Check idempotency / reuse on operation_id
                existing_op = c.execute(
                    "SELECT server_seq, device_seq, nonce, ciphertext FROM sync_operations WHERE vault_id=? AND operation_id=?",
                    (vault_id, op_id),
                ).fetchone()
                if existing_op:
                    if (
                        int(existing_op["device_seq"]) != dev_seq
                        or existing_op["nonce"] != nonce
                        or existing_op["ciphertext"] != ciphertext
                    ):
                        raise ApiError(
                            409,
                            "OPERATION_ID_REUSE",
                            f"operation_id {op_id} reused with different payload",
                        )
                    accepted.append(
                        {"operation_id": op_id, "server_seq": str(existing_op["server_seq"]), "duplicate": True}
                    )
                    continue

                # Check sequence reuse
                existing_seq = c.execute(
                    "SELECT operation_id FROM sync_operations WHERE vault_id=? AND device_id=? AND device_seq=?",
                    (vault_id, op_dev, dev_seq),
                ).fetchone()
                if existing_seq:
                    raise ApiError(
                        409,
                        "DEVICE_SEQUENCE_REUSE",
                        f"device_seq {dev_seq} already used by another operation",
                        {"next_expected_device_seq": str(last_dev_seq + 1)},
                    )

                # Check sequence gap
                if dev_seq != last_dev_seq + 1:
                    raise ApiError(
                        409,
                        "DEVICE_SEQUENCE_GAP",
                        f"Expected device_seq {last_dev_seq + 1}, got {dev_seq}",
                        {"next_expected_device_seq": str(last_dev_seq + 1)},
                    )

                cur_srv_seq = next_server_seq
                next_server_seq += 1
                last_dev_seq = dev_seq
                c.execute(
                    """INSERT INTO sync_operations
                       (vault_id, server_seq, operation_id, device_id, device_seq, protocol_version,
                        crypto_version, key_epoch, nonce, ciphertext, received_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        vault_id,
                        cur_srv_seq,
                        op_id,
                        op_dev,
                        dev_seq,
                        proto_ver,
                        crypto_ver,
                        key_epoch,
                        nonce,
                        ciphertext,
                        _now_iso(),
                    ),
                )
                accepted.append({"operation_id": op_id, "server_seq": str(cur_srv_seq), "duplicate": False})

            c.execute("UPDATE vaults SET next_server_seq=? WHERE vault_id=?", (next_server_seq, vault_id))
            c.execute("UPDATE devices SET last_device_seq=? WHERE device_id=?", (last_dev_seq, auth_device["device_id"]))

        return {
            "accepted": accepted,
            "next_expected_device_seq": str(last_dev_seq + 1),
        }

    def pull_operations(self, vault_id: str, since_seq_raw: str = "0", limit_raw: int = 200) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        try:
            since_seq = int(since_seq_raw)
            limit = int(limit_raw)
            if since_seq < 0 or limit < 1 or limit > 200:
                raise ValueError("Pagination out of bounds")
        except (ValueError, TypeError) as exc:
            raise ApiError(400, "INVALID_PAYLOAD", "Invalid pagination parameters") from exc

        rows = self.storage.conn.execute(
            """SELECT * FROM sync_operations
               WHERE vault_id=? AND server_seq > ?
               ORDER BY server_seq ASC LIMIT ?""",
            (vault_id, since_seq, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        slice_rows = rows[:limit]
        next_since = str(slice_rows[-1]["server_seq"]) if slice_rows else str(since_seq)

        return {
            "operations": [
                {
                    "server_seq": str(r["server_seq"]),
                    "protocol_version": r["protocol_version"],
                    "crypto_version": r["crypto_version"],
                    "key_epoch": r["key_epoch"],
                    "vault_id": r["vault_id"],
                    "device_id": r["device_id"],
                    "operation_id": r["operation_id"],
                    "device_seq": str(r["device_seq"]),
                    "nonce": r["nonce"],
                    "ciphertext": r["ciphertext"],
                    "received_at": r["received_at"],
                }
                for r in slice_rows
            ],
            "next_since_seq": next_since,
            "has_more": has_more,
        }

    # ── Epoch Keys & Devices ──────────────────────────────────────────────────

    def list_epoch_keys(self, vault_id: str, auth_device: dict[str, Any]) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        v = self.storage.conn.execute("SELECT current_key_epoch FROM vaults WHERE vault_id=?", (vault_id,)).fetchone()
        if not v:
            raise ApiError(404, "VAULT_NOT_FOUND", "Vault not found")
        rows = self.storage.conn.execute(
            """SELECT key_epoch, wrapped_epoch_key, created_by_device_id, reason, created_at
               FROM epoch_keys
               WHERE vault_id=? AND target_device_id=?
               ORDER BY key_epoch ASC""",
            (vault_id, auth_device["device_id"]),
        ).fetchall()
        return {
            "current_key_epoch": v["current_key_epoch"],
            "epochs": [dict(r) for r in rows],
        }

    def rotate_epoch(self, vault_id: str, auth_device: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        try:
            new_epoch = int(body["new_key_epoch"])
            reason = str(body.get("reason") or "device_revoked")
            wrapped_keys = body["wrapped_keys"]
            if not isinstance(wrapped_keys, dict):
                raise ValueError("wrapped_keys must be dict")
        except (KeyError, ValueError, TypeError) as exc:
            raise ApiError(400, "INVALID_PAYLOAD", str(exc)) from exc

        ts = _now_iso()
        with self.storage.transaction() as c:
            v = c.execute("SELECT current_key_epoch FROM vaults WHERE vault_id=?", (vault_id,)).fetchone()
            if not v:
                raise ApiError(404, "VAULT_NOT_FOUND", "Vault not found")
            if new_epoch != int(v["current_key_epoch"]) + 1:
                raise ApiError(409, "INVALID_EPOCH", f"Expected key_epoch {int(v['current_key_epoch']) + 1}")

            active_devs = c.execute(
                "SELECT device_id FROM devices WHERE vault_id=? AND status='active'", (vault_id,)
            ).fetchall()
            active_ids = {dev["device_id"] for dev in active_devs}
            for dev_id in active_ids:
                if dev_id not in wrapped_keys or not _B64U_RE.match(str(wrapped_keys[dev_id])):
                    raise ApiError(
                        400,
                        "MISSING_WRAPPED_KEY",
                        f"Missing valid wrapped key for active device {dev_id}",
                    )
            extra_ids = set(wrapped_keys.keys()) - active_ids
            if extra_ids:
                raise ApiError(
                    400,
                    "INVALID_WRAPPED_KEYS",
                    f"wrapped_keys contains unknown or revoked device IDs: {sorted(extra_ids)}",
                )

            for dev_id, wrapped_val in wrapped_keys.items():
                assert_uuid(dev_id, "target_device_id")
                c.execute(
                    """INSERT INTO epoch_keys
                       (vault_id, key_epoch, target_device_id, wrapped_epoch_key, created_by_device_id, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (vault_id, new_epoch, dev_id, str(wrapped_val), auth_device["device_id"], reason, ts),
                )
            c.execute("UPDATE vaults SET current_key_epoch=? WHERE vault_id=?", (new_epoch, vault_id))

        return {"current_key_epoch": new_epoch}

    def list_devices(self, vault_id: str) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        rows = self.storage.conn.execute(
            """SELECT device_id, device_name, device_type, public_key_x25519, status, created_at, last_seen_at, revoked_at
               FROM devices WHERE vault_id=? ORDER BY created_at ASC""",
            (vault_id,),
        ).fetchall()
        return {"devices": [dict(r) for r in rows]}

    def revoke_device(self, vault_id: str, auth_device: dict[str, Any], target_device_id: str) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(target_device_id, "device_id")
        if auth_device["device_id"] == target_device_id:
            raise ApiError(400, "CANNOT_REVOKE_SELF", "Active device cannot revoke itself directly here")
        ts = _now_iso()
        with self.storage.transaction() as c:
            row = c.execute(
                "SELECT status FROM devices WHERE vault_id=? AND device_id=?", (vault_id, target_device_id)
            ).fetchone()
            if not row:
                raise ApiError(404, "DEVICE_NOT_FOUND", "Device not found")
            c.execute(
                "UPDATE devices SET status='revoked', revoked_at=? WHERE vault_id=? AND device_id=?",
                (ts, vault_id, target_device_id),
            )
        return {"status": "revoked", "revoked_at": ts}

    # ── Pairing Lifecycle ─────────────────────────────────────────────────────

    def create_pairing(self, vault_id: str, auth_device: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        try:
            pairing_id = assert_uuid(body["pairing_id"], "pairing_id")
            device_a_id = assert_uuid(body["device_a_id"], "device_a_id")
            pairing_nonce = str(body["pairing_nonce"])
            expires_at = str(body["expires_at"])
            _parse_iso(expires_at)
            if not _B64U_RE.match(pairing_nonce):
                raise ValueError("Invalid pairing_nonce")
        except (KeyError, ValueError) as exc:
            raise ApiError(400, "INVALID_PAYLOAD", str(exc)) from exc
        if device_a_id != auth_device["device_id"]:
            raise ApiError(403, "DEVICE_MISMATCH", "device_a_id must match caller")

        with self.storage.transaction() as c:
            if not c.execute("SELECT 1 FROM vaults WHERE vault_id=?", (vault_id,)).fetchone():
                raise ApiError(404, "VAULT_NOT_FOUND", "Vault not found")
            if c.execute("SELECT 1 FROM pairing_sessions WHERE pairing_id=?", (pairing_id,)).fetchone():
                raise ApiError(409, "PAIRING_EXISTS", "Pairing session already exists")
            c.execute(
                """INSERT INTO pairing_sessions
                   (pairing_id, vault_id, device_a_id, pairing_nonce, expires_at, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'created', ?)""",
                (pairing_id, vault_id, device_a_id, pairing_nonce, expires_at, _now_iso()),
            )
        return {"pairing_id": pairing_id, "status": "created", "expires_at": expires_at}

    def _get_valid_pairing_in_tx(self, c: Any, vault_id: str, pairing_id: str) -> dict[str, Any]:
        row = c.execute(
            "SELECT * FROM pairing_sessions WHERE vault_id=? AND pairing_id=?", (vault_id, pairing_id)
        ).fetchone()
        if not row:
            raise ApiError(404, "PAIRING_NOT_FOUND", "Pairing session not found")
        if _parse_iso(row["expires_at"]) < datetime.now(timezone.utc):
            raise ApiError(410, "PAIRING_EXPIRED", "Pairing session has expired")
        return dict(row)

    def join_pairing(self, vault_id: str, pairing_id: str, body: dict[str, Any]) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(pairing_id, "pairing_id")
        try:
            dev_b_id = assert_uuid(body["device_b_id"], "device_b_id")
            dev_b_name = str(body["device_b_name"])[:128]
            dev_b_type = str(body["device_b_type"])
            pub_b = str(body["device_b_public_key_x25519"])
            nonce_b = str(body["session_nonce_b"])
            proof = str(body["secret_proof_hmac"])
            if dev_b_type not in ("desktop", "pwa", "mobile"):
                raise ValueError("Invalid device_type")
            if not (_B64U_RE.match(pub_b) and _B64U_RE.match(nonce_b) and _B64U_RE.match(proof)):
                raise ValueError("Invalid base64url pairing parameter")
        except (KeyError, ValueError) as exc:
            raise ApiError(400, "INVALID_PAYLOAD", str(exc)) from exc

        with self.storage.transaction() as c:
            sess = self._get_valid_pairing_in_tx(c, vault_id, pairing_id)
            if sess["status"] != "created":
                raise ApiError(409, "PAIRING_INVALID_STATE", f"Pairing status is {sess['status']}")
            if c.execute("SELECT 1 FROM devices WHERE device_id=?", (dev_b_id,)).fetchone():
                raise ApiError(409, "DEVICE_EXISTS", "Device ID already registered")
            c.execute(
                """UPDATE pairing_sessions SET
                   status='joined', device_b_id=?, device_b_name=?, device_b_type=?,
                   device_b_public_key_x25519=?, session_nonce_b=?, secret_proof_hmac=?
                   WHERE pairing_id=?""",
                (dev_b_id, dev_b_name, dev_b_type, pub_b, nonce_b, proof, pairing_id),
            )
        return {
            "status": "joined",
            "device_a_id": sess["device_a_id"],
            "pairing_nonce": sess["pairing_nonce"],
        }

    def get_pairing_status(self, vault_id: str, pairing_id: str) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(pairing_id, "pairing_id")
        with self.storage.transaction() as c:
            sess = self._get_valid_pairing_in_tx(c, vault_id, pairing_id)
        return {
            "pairing_id": sess["pairing_id"],
            "status": sess["status"],
            "device_a_id": sess["device_a_id"],
            "pairing_nonce": sess["pairing_nonce"],
            "device_b_id": sess["device_b_id"],
            "device_b_name": sess["device_b_name"],
            "device_b_type": sess["device_b_type"],
            "device_b_public_key_x25519": sess["device_b_public_key_x25519"],
            "session_nonce_b": sess["session_nonce_b"],
            "secret_proof_hmac": sess["secret_proof_hmac"],
            "bundle_nonce": sess["bundle_nonce"],
            "encrypted_vault_bundle": sess["encrypted_vault_bundle"],
            "expires_at": sess["expires_at"],
        }

    def approve_pairing(
        self, vault_id: str, auth_device: dict[str, Any], pairing_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(pairing_id, "pairing_id")
        bundle_nonce = str(body.get("bundle_nonce") or "")
        enc_bundle = str(body.get("encrypted_vault_bundle") or "")
        if not (_B64U_RE.match(bundle_nonce) and _B64U_RE.match(enc_bundle)):
            raise ApiError(400, "INVALID_PAYLOAD", "Invalid encrypted_vault_bundle/bundle_nonce")

        with self.storage.transaction() as c:
            sess = self._get_valid_pairing_in_tx(c, vault_id, pairing_id)
            if sess["device_a_id"] != auth_device["device_id"]:
                raise ApiError(403, "DEVICE_MISMATCH", "Only initiator device A can approve")
            if sess["status"] != "joined":
                raise ApiError(409, "PAIRING_INVALID_STATE", f"Pairing status is {sess['status']}")
            c.execute(
                "UPDATE pairing_sessions SET status='approved', bundle_nonce=?, encrypted_vault_bundle=? WHERE pairing_id=?",
                (bundle_nonce, enc_bundle, pairing_id),
            )
        return {"status": "approved"}

    def confirm_pairing(self, vault_id: str, pairing_id: str, body: dict[str, Any]) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(pairing_id, "pairing_id")
        try:
            dev_b_id = assert_uuid(body["device_b_id"], "device_b_id")
            confirm_hmac = str(body["confirm_hmac"])
            if not _B64U_RE.match(confirm_hmac):
                raise ValueError("Invalid confirm_hmac format")
        except (KeyError, ValueError) as exc:
            raise ApiError(400, "INVALID_PAYLOAD", str(exc)) from exc

        token = _generate_access_token()
        token_hash = _sha256_hex(token)
        ts = _now_iso()

        mismatch_error = False
        with self.storage.transaction() as c:
            sess = self._get_valid_pairing_in_tx(c, vault_id, pairing_id)
            if sess["status"] != "approved":
                raise ApiError(409, "PAIRING_INVALID_STATE", f"Pairing status is {sess['status']}")
            if sess["device_b_id"] != dev_b_id:
                attempts = int(sess["failed_attempts"]) + 1
                c.execute("UPDATE pairing_sessions SET failed_attempts=? WHERE pairing_id=?", (attempts, pairing_id))
                if attempts >= MAX_PAIRING_ATTEMPTS:
                    c.execute("UPDATE pairing_sessions SET status='locked' WHERE pairing_id=?", (pairing_id,))
                mismatch_error = True
            else:
                if c.execute("SELECT 1 FROM devices WHERE device_id=?", (dev_b_id,)).fetchone():
                    raise ApiError(409, "DEVICE_EXISTS", "Device ID already registered")

                c.execute(
                    """INSERT INTO devices
                       (device_id, vault_id, device_name, device_type, public_key_x25519, token_hash_sha256, status, last_device_seq, created_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'active', 0, ?, ?)""",
                    (
                        dev_b_id,
                        vault_id,
                        sess["device_b_name"],
                        sess["device_b_type"],
                        sess["device_b_public_key_x25519"],
                        token_hash,
                        ts,
                        ts,
                    ),
                )
                c.execute("UPDATE pairing_sessions SET status='confirmed' WHERE pairing_id=?", (pairing_id,))

        if mismatch_error:
            raise ApiError(403, "DEVICE_MISMATCH", "device_b_id mismatch")

        return {"status": "confirmed", "device_access_token": token}

    # ── Media Transfers (Chunked Upload + Streaming Download) ─────────────────

    def init_media_upload(self, vault_id: str, body: dict[str, Any]) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        try:
            media_id = assert_uuid(body["media_id"], "media_id")
            enc_size = int(body["encrypted_size"])
            enc_sha = str(body["encrypted_sha256"]).lower()
            key_epoch = int(body["key_epoch"])
            if enc_size < 49 or not _SHA256_RE.match(enc_sha) or key_epoch < 1:
                raise ValueError("Invalid media metadata")
            if "chunk_size" in body and body["chunk_size"] is not None:
                chunk_size = int(body["chunk_size"])
                if chunk_size < 1 or chunk_size > MAX_HTTP_CHUNK_SIZE:
                    raise ValueError(f"chunk_size must be between 1 and {MAX_HTTP_CHUNK_SIZE}")
        except (KeyError, ValueError, TypeError) as exc:
            raise ApiError(400, "INVALID_PAYLOAD", str(exc)) from exc

        part_path = self.storage.media_part_path(vault_id, media_id)
        with self.storage.transaction() as c:
            if not c.execute("SELECT 1 FROM vaults WHERE vault_id=?", (vault_id,)).fetchone():
                raise ApiError(404, "VAULT_NOT_FOUND", "Vault not found")
            existing = c.execute(
                "SELECT status, received_bytes, encrypted_sha256, encrypted_size FROM media_objects WHERE vault_id=? AND media_id=?",
                (vault_id, media_id),
            ).fetchone()
            if existing:
                if existing["status"] == "completed" and existing["encrypted_sha256"] == enc_sha:
                    return {
                        "media_id": media_id,
                        "status": "completed",
                        "received_bytes": existing["encrypted_size"],
                    }
                if existing["status"] == "uploading" and existing["encrypted_sha256"] == enc_sha:
                    return {
                        "media_id": media_id,
                        "status": "uploading",
                        "received_bytes": existing["received_bytes"],
                    }
            c.execute(
                """INSERT OR REPLACE INTO media_objects
                   (vault_id, media_id, encrypted_size, encrypted_sha256, key_epoch, storage_path, status, received_bytes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'uploading', 0, ?)""",
                (vault_id, media_id, enc_size, enc_sha, key_epoch, str(part_path), _now_iso()),
            )
            if part_path.exists():
                part_path.unlink()
            part_path.write_bytes(b"")
        return {"media_id": media_id, "status": "uploading", "received_bytes": 0}

    def upload_media_chunk(
        self, vault_id: str, media_id: str, content_range: str | None, chunk: bytes
    ) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(media_id, "media_id")
        if not content_range:
            raise ApiError(400, "INVALID_RANGE", "Missing Content-Range header")
        m = re.match(r"^bytes (\d+)-(\d+)/(\d+)$", content_range.strip())
        if not m:
            raise ApiError(400, "INVALID_RANGE", f"Malformed Content-Range: {content_range}")
        start, end, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if len(chunk) == 0 or end < start or (end - start + 1) != len(chunk):
            raise ApiError(400, "INVALID_RANGE", "Content-Range byte count mismatch")
        if len(chunk) > MAX_HTTP_CHUNK_SIZE:
            raise ApiError(
                413,
                "CHUNK_TOO_LARGE",
                f"HTTP chunk length {len(chunk)} exceeds limit {MAX_HTTP_CHUNK_SIZE}",
            )

        with self.storage.transaction() as c:
            row = c.execute(
                "SELECT * FROM media_objects WHERE vault_id=? AND media_id=?", (vault_id, media_id)
            ).fetchone()
            if not row:
                raise ApiError(404, "MEDIA_NOT_FOUND", "Media upload session not initialized")
            if row["status"] == "completed":
                return {"received_bytes": int(row["encrypted_size"])}
            enc_size = int(row["encrypted_size"])
            if total != enc_size or start != int(row["received_bytes"]):
                raise ApiError(
                    409,
                    "RANGE_CONFLICT",
                    f"Expected offset {row['received_bytes']} of total {row['encrypted_size']}",
                )
            if end >= total or start + len(chunk) > enc_size:
                raise ApiError(
                    409,
                    "SIZE_EXCEEDED",
                    f"Chunk range {start}-{end} exceeds declared encrypted_size {enc_size}",
                )
            part_path = self.storage.media_part_path(vault_id, media_id)
            with open(part_path, "ab") as f:
                f.write(chunk)
            new_received = start + len(chunk)
            c.execute(
                "UPDATE media_objects SET received_bytes=? WHERE vault_id=? AND media_id=?",
                (new_received, vault_id, media_id),
            )
        return {"received_bytes": new_received}

    def complete_media_upload(self, vault_id: str, media_id: str) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(media_id, "media_id")
        with self.storage.transaction() as c:
            row = c.execute(
                "SELECT * FROM media_objects WHERE vault_id=? AND media_id=?", (vault_id, media_id)
            ).fetchone()
            if not row:
                raise ApiError(404, "MEDIA_NOT_FOUND", "Media upload not found")
            if row["status"] == "completed":
                return {
                    "media_id": media_id,
                    "status": "completed",
                    "encrypted_sha256": row["encrypted_sha256"],
                }
            part_path = self.storage.media_part_path(vault_id, media_id)
            if not part_path.is_file():
                raise ApiError(400, "SIZE_MISMATCH", f"Expected {row['encrypted_size']} bytes, got 0")
            hasher = hashlib.sha256()
            actual_size = 0
            with open(part_path, "rb") as f:
                while True:
                    blk = f.read(1048576)
                    if not blk:
                        break
                    actual_size += len(blk)
                    hasher.update(blk)
            if actual_size != int(row["encrypted_size"]):
                raise ApiError(
                    400,
                    "SIZE_MISMATCH",
                    f"Expected {row['encrypted_size']} bytes, got {actual_size}",
                )
            got_sha = hasher.hexdigest()
            if not hmac.compare_digest(got_sha, row["encrypted_sha256"]):
                raise ApiError(400, "CHECKSUM_MISMATCH", "Uploaded encrypted_sha256 mismatch")

            final_path = self.storage.media_final_path(vault_id, media_id)
            part_path.replace(final_path)
            c.execute(
                "UPDATE media_objects SET status='completed', storage_path=?, completed_at=? WHERE vault_id=? AND media_id=?",
                (str(final_path), _now_iso(), vault_id, media_id),
            )
        return {"media_id": media_id, "status": "completed", "encrypted_sha256": row["encrypted_sha256"]}

    def get_media_blob(self, vault_id: str, media_id: str) -> Path:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(media_id, "media_id")
        row = self.storage.conn.execute(
            "SELECT * FROM media_objects WHERE vault_id=? AND media_id=? AND status='completed' AND deleted_at IS NULL",
            (vault_id, media_id),
        ).fetchone()
        if not row:
            raise ApiError(404, "MEDIA_NOT_FOUND", "Encrypted media object not found")
        p = Path(row["storage_path"])
        if not p.is_file():
            raise ApiError(404, "MEDIA_NOT_FOUND", "Media file missing on disk")
        return p

    def delete_media(self, vault_id: str, media_id: str) -> dict[str, Any]:
        assert_uuid(vault_id, "vault_id")
        assert_uuid(media_id, "media_id")
        with self.storage.transaction() as c:
            row = c.execute(
                "SELECT storage_path FROM media_objects WHERE vault_id=? AND media_id=?", (vault_id, media_id)
            ).fetchone()
            if not row:
                raise ApiError(404, "MEDIA_NOT_FOUND", "Media not found")
            p = Path(row["storage_path"])
            if p.exists():
                p.unlink()
            c.execute(
                "UPDATE media_objects SET status='deleted', deleted_at=? WHERE vault_id=? AND media_id=?",
                (_now_iso(), vault_id, media_id),
            )
        return {"status": "deleted", "media_id": media_id}
