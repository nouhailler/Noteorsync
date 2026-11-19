"""SQLite WAL storage & blob filesystem manager for Noteor Sync Server."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def assert_uuid(val: str, field: str = "id") -> str:
    if not isinstance(val, str) or not _UUID_RE.match(val):
        raise ValueError(f"Invalid UUID for {field}")
    return val


class SyncStorage:
    def __init__(self, db_path: str | Path, blob_dir: str | Path) -> None:
        self.db_path = str(db_path)
        self.blob_dir = Path(blob_dir).resolve()
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.db_path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vaults (
                vault_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                current_key_epoch INTEGER NOT NULL DEFAULT 1,
                vsk_recovery_envelope_json TEXT,
                next_server_seq INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                public_key_x25519 TEXT NOT NULL,
                token_hash_sha256 TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'active',
                last_device_seq INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_seen_at TEXT,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_devices_vault ON devices(vault_id);
            CREATE INDEX IF NOT EXISTS idx_devices_token ON devices(token_hash_sha256);

            CREATE TABLE IF NOT EXISTS epoch_keys (
                vault_id TEXT NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
                key_epoch INTEGER NOT NULL,
                target_device_id TEXT NOT NULL,
                wrapped_epoch_key TEXT,
                created_by_device_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (vault_id, key_epoch, target_device_id)
            );

            CREATE TABLE IF NOT EXISTS pairing_sessions (
                pairing_id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
                device_a_id TEXT NOT NULL,
                pairing_nonce TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                device_b_id TEXT,
                device_b_name TEXT,
                device_b_type TEXT,
                device_b_public_key_x25519 TEXT,
                session_nonce_b TEXT,
                secret_proof_hmac TEXT,
                bundle_nonce TEXT,
                encrypted_vault_bundle TEXT,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_operations (
                vault_id TEXT NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
                server_seq INTEGER NOT NULL,
                operation_id TEXT NOT NULL,
                device_id TEXT NOT NULL,
                device_seq INTEGER NOT NULL,
                protocol_version INTEGER NOT NULL,
                crypto_version INTEGER NOT NULL,
                key_epoch INTEGER NOT NULL,
                nonce TEXT NOT NULL,
                ciphertext TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY (vault_id, server_seq),
                UNIQUE (vault_id, operation_id),
                UNIQUE (vault_id, device_id, device_seq)
            );
            CREATE INDEX IF NOT EXISTS idx_sync_ops_cursor ON sync_operations(vault_id, server_seq);

            CREATE TABLE IF NOT EXISTS media_objects (
                vault_id TEXT NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
                media_id TEXT NOT NULL,
                encrypted_size INTEGER NOT NULL,
                encrypted_sha256 TEXT NOT NULL,
                key_epoch INTEGER NOT NULL,
                storage_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'uploading',
                received_bytes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                deleted_at TEXT,
                PRIMARY KEY (vault_id, media_id)
            );
            """
        )

    def media_part_path(self, vault_id: str, media_id: str) -> Path:
        v = assert_uuid(vault_id, "vault_id")
        m = assert_uuid(media_id, "media_id")
        target_dir = (self.blob_dir / v).resolve()
        if target_dir.parent != self.blob_dir:
            raise ValueError("Path traversal rejected")
        target_dir.mkdir(parents=True, exist_ok=True)
        return (target_dir / f"{m}.bin.part").resolve()

    def media_final_path(self, vault_id: str, media_id: str) -> Path:
        v = assert_uuid(vault_id, "vault_id")
        m = assert_uuid(media_id, "media_id")
        target_dir = (self.blob_dir / v).resolve()
        if target_dir.parent != self.blob_dir:
            raise ValueError("Path traversal rejected")
        target_dir.mkdir(parents=True, exist_ok=True)
        return (target_dir / f"{m}.bin").resolve()

    def backup_to(self, dest_db_path: str | Path) -> Path:
        dest = Path(dest_db_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        backup_conn = sqlite3.connect(str(dest))
        try:
            self.conn.backup(backup_conn)
        finally:
            backup_conn.close()
        return dest
