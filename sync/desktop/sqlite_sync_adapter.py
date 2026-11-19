"""Noteor Desktop SQLite WAL Sync Adapter & Migration (Phase 4).

Provides:
- Idempotent SQLite v1 -> v2 schema migration (`sync_id`, `revision_id`, `parent_revision_ids`,
  UTC RFC 3339 timestamps, `[[Title|123]]` -> `[[Title|@uuid]]`, tag NFC+casefold deduplication).
- Local sync metadata tables (`sync_operations`, `sync_state`, `sync_revisions`,
  `sync_conflicts`, `sync_pending_ops`, `sync_tombstones`).
- Atomic `BEGIN IMMEDIATE` transactions wrapping business mutations + outbox journal insertion.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .crypto import generate_uuid_v4

_NUMERIC_WIKILINK_RE = re.compile(r"\[\[([^\]|\[\n]+)\|(\d+)\]\]")
_UUID_WIKILINK_RE = re.compile(r"\[\[([^\]|\[\n]+)\|@([0-9a-f-]{36})\]\]", re.IGNORECASE)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def normalize_utc_timestamp(ts: str | None) -> str:
    if not ts:
        return now_utc_iso()
    s = ts.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", s):
        return s
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", s):
        return s[:-1] + ".000Z"
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", s):
        return s.replace(" ", "T") + ".000Z"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except ValueError:
        return now_utc_iso()


def normalize_tag_name(name: str) -> str:
    return unicodedata.normalize("NFC", name).strip().casefold()


def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    if not _column_exists(conn, table, col):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


class DesktopSyncDatabase:
    """Thread-safe SQLite WAL database adapter compatible with Noteor Desktop v1 & Sync v2."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.db_path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.ensure_schema_and_migrate()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def ensure_schema_and_migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                color TEXT NOT NULL DEFAULT '#6366F1',
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#6366F1'
            );
            CREATE TABLE IF NOT EXISTS note_tags (
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (note_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL DEFAULT '',
                duration REAL,
                created_at TEXT NOT NULL
            );

            -- Sync metadata & durability tables (v2)
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL UNIQUE,
                device_seq TEXT NOT NULL,
                key_epoch INTEGER NOT NULL DEFAULT 1,
                operation_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                parent_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                payload_data_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                media_id TEXT,
                media_local_path TEXT,
                server_seq TEXT,
                error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sync_ops_status ON sync_operations(status, id);
            CREATE INDEX IF NOT EXISTS idx_sync_ops_entity ON sync_operations(entity_type, entity_id);

            CREATE TABLE IF NOT EXISTS sync_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                parent_revision_ids_json TEXT NOT NULL DEFAULT '[]',
                is_head INTEGER NOT NULL DEFAULT 1,
                operation_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                device_id TEXT NOT NULL,
                UNIQUE(entity_type, entity_id, revision_id)
            );
            CREATE INDEX IF NOT EXISTS idx_sync_rev_head ON sync_revisions(entity_type, entity_id, is_head);

            CREATE TABLE IF NOT EXISTS sync_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conflict_id TEXT NOT NULL UNIQUE,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                local_revision_id TEXT NOT NULL,
                remote_revision_id TEXT NOT NULL,
                local_snapshot_json TEXT NOT NULL,
                remote_snapshot_json TEXT NOT NULL,
                remote_device_id TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                resolution TEXT,
                resolved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sync_conflicts_status ON sync_conflicts(status, entity_type, entity_id);

            CREATE TABLE IF NOT EXISTS sync_pending_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL UNIQUE,
                server_seq TEXT NOT NULL,
                device_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                missing_deps_json TEXT NOT NULL,
                decrypted_payload_json TEXT NOT NULL,
                received_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_tombstones (
                entity_key TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                deleted_at TEXT NOT NULL
            );
            """
        )

        with self.transaction() as c:

            # Add v2 sync columns cleanly on business tables
            for tbl in ("categories", "folders", "notes", "tags", "note_tags", "attachments"):
                _add_column_if_missing(c, tbl, "sync_id", "TEXT")
                _add_column_if_missing(c, tbl, "revision_id", "TEXT")
                _add_column_if_missing(c, tbl, "parent_revision_ids_json", "TEXT NOT NULL DEFAULT '[]'")

            _add_column_if_missing(c, "categories", "created_at", "TEXT")
            _add_column_if_missing(c, "categories", "updated_at", "TEXT")
            _add_column_if_missing(c, "folders", "updated_at", "TEXT")
            _add_column_if_missing(c, "notes", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
            _add_column_if_missing(c, "tags", "created_at", "TEXT")
            _add_column_if_missing(c, "tags", "updated_at", "TEXT")
            _add_column_if_missing(c, "note_tags", "created_at", "TEXT")
            _add_column_if_missing(c, "attachments", "media_id", "TEXT")
            _add_column_if_missing(c, "attachments", "mime_type", "TEXT")
            _add_column_if_missing(c, "attachments", "original_size", "INTEGER")
            _add_column_if_missing(c, "attachments", "encrypted_size", "INTEGER")
            _add_column_if_missing(c, "attachments", "encrypted_sha256", "TEXT")
            _add_column_if_missing(c, "attachments", "key_epoch", "INTEGER")
            _add_column_if_missing(c, "attachments", "media_status", "TEXT DEFAULT 'local_only'")
            _add_column_if_missing(c, "attachments", "thumbnail_data_url", "TEXT")

            self._migrate_existing_rows_in_tx(c)

            for tbl in ("categories", "folders", "notes", "tags", "note_tags", "attachments"):
                c.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{tbl}_sync_id ON {tbl}(sync_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_attachments_media_id ON attachments(media_id)")

    def _migrate_existing_rows_in_tx(self, c: sqlite3.Connection) -> None:
        ts_now = now_utc_iso()

        # Categories
        for row in c.execute("SELECT * FROM categories").fetchall():
            sync_id = row["sync_id"] or generate_uuid_v4()
            rev_id = row["revision_id"] or generate_uuid_v4()
            created_at = normalize_utc_timestamp(row["created_at"] or ts_now)
            updated_at = normalize_utc_timestamp(row["updated_at"] or created_at)
            c.execute(
                "UPDATE categories SET sync_id=?, revision_id=?, created_at=?, updated_at=? WHERE id=?",
                (sync_id, rev_id, created_at, updated_at, row["id"]),
            )
            self._seed_rev_if_absent(c, "category", sync_id, rev_id, "entity.upsert", updated_at)

        # Folders
        for row in c.execute("SELECT * FROM folders").fetchall():
            sync_id = row["sync_id"] or generate_uuid_v4()
            rev_id = row["revision_id"] or generate_uuid_v4()
            created_at = normalize_utc_timestamp(row["created_at"])
            updated_at = normalize_utc_timestamp(row["updated_at"] or created_at)
            c.execute(
                "UPDATE folders SET sync_id=?, revision_id=?, created_at=?, updated_at=? WHERE id=?",
                (sync_id, rev_id, created_at, updated_at, row["id"]),
            )
            self._seed_rev_if_absent(c, "folder", sync_id, rev_id, "entity.upsert", updated_at)

        # Tags (NFC + casefold normalization + deduplication)
        canonical_by_norm: dict[str, int] = {}
        for row in c.execute("SELECT * FROM tags ORDER BY id ASC").fetchall():
            norm = normalize_tag_name(row["name"])
            if norm in canonical_by_norm:
                keep_id = canonical_by_norm[norm]
                for link in c.execute("SELECT note_id FROM note_tags WHERE tag_id=?", (row["id"],)).fetchall():
                    c.execute(
                        "INSERT OR IGNORE INTO note_tags(note_id, tag_id) VALUES (?, ?)",
                        (link["note_id"], keep_id),
                    )
                c.execute("DELETE FROM note_tags WHERE tag_id=?", (row["id"],))
                c.execute("DELETE FROM tags WHERE id=?", (row["id"],))
                continue
            sync_id = row["sync_id"] or generate_uuid_v4()
            rev_id = row["revision_id"] or generate_uuid_v4()
            created_at = normalize_utc_timestamp(row["created_at"] or ts_now)
            updated_at = normalize_utc_timestamp(row["updated_at"] or ts_now)
            c.execute(
                "UPDATE tags SET name=?, sync_id=?, revision_id=?, created_at=?, updated_at=? WHERE id=?",
                (norm, sync_id, rev_id, created_at, updated_at, row["id"]),
            )
            canonical_by_norm[norm] = row["id"]
            self._seed_rev_if_absent(c, "tag", sync_id, rev_id, "entity.upsert", updated_at)

        # Notes pass 1: ensure sync_ids
        note_sync_map: dict[int, str] = {}
        notes = c.execute("SELECT * FROM notes").fetchall()
        for row in notes:
            sync_id = row["sync_id"] or generate_uuid_v4()
            note_sync_map[row["id"]] = sync_id

        # Notes pass 2: upgrade [[Title|123]] -> [[Title|@uuid]] and normalize UTC timestamps
        for row in notes:
            sync_id = note_sync_map[row["id"]]
            rev_id = row["revision_id"] or generate_uuid_v4()
            created_at = normalize_utc_timestamp(row["created_at"])
            updated_at = normalize_utc_timestamp(row["updated_at"])
            deleted_at = normalize_utc_timestamp(row["deleted_at"]) if row["deleted_at"] else None

            def _repl(m: re.Match[str]) -> str:
                title, num_s = m.group(1), m.group(2)
                target = note_sync_map.get(int(num_s))
                return f"[[{title}|@{target}]]" if target else m.group(0)

            content = _NUMERIC_WIKILINK_RE.sub(_repl, row["content"] or "")
            c.execute(
                """UPDATE notes SET sync_id=?, revision_id=?, content=?, created_at=?, updated_at=?, deleted_at=?
                   WHERE id=?""",
                (sync_id, rev_id, content, created_at, updated_at, deleted_at, row["id"]),
            )
            op_type = "entity.trash" if row["is_deleted"] else "entity.upsert"
            self._seed_rev_if_absent(c, "note", sync_id, rev_id, op_type, updated_at)

        # Note-Tags
        for row in c.execute("SELECT * FROM note_tags").fetchall():
            sync_id = row["sync_id"] or generate_uuid_v4()
            rev_id = row["revision_id"] or generate_uuid_v4()
            created_at = normalize_utc_timestamp(row["created_at"] or ts_now)
            c.execute(
                "UPDATE note_tags SET sync_id=?, revision_id=?, created_at=? WHERE note_id=? AND tag_id=?",
                (sync_id, rev_id, created_at, row["note_id"], row["tag_id"]),
            )
            self._seed_rev_if_absent(c, "relation", sync_id, rev_id, "entity.upsert", created_at)

        # Attachments
        for row in c.execute("SELECT * FROM attachments").fetchall():
            sync_id = row["sync_id"] or generate_uuid_v4()
            media_id = row["media_id"] or generate_uuid_v4()
            rev_id = row["revision_id"] or generate_uuid_v4()
            created_at = normalize_utc_timestamp(row["created_at"])
            c.execute(
                "UPDATE attachments SET sync_id=?, media_id=?, revision_id=?, created_at=? WHERE id=?",
                (sync_id, media_id, rev_id, created_at, row["id"]),
            )
            self._seed_rev_if_absent(c, "attachment", sync_id, rev_id, "media.register", created_at)

    def _seed_rev_if_absent(
        self,
        c: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        revision_id: str,
        op_type: str,
        created_at: str,
    ) -> None:
        c.execute(
            """INSERT OR IGNORE INTO sync_revisions
               (entity_type, entity_id, revision_id, parent_revision_ids_json, is_head, operation_type, created_at, device_id)
               VALUES (?, ?, ?, '[]', 1, ?, ?, 'local')""",
            (entity_type, entity_id, revision_id, op_type, created_at),
        )

    # ── Sync State & Outbox Helpers ────────────────────────────────────────────

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.transaction() as c:
            c.execute(
                "INSERT INTO sync_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _outbox_context(self, c: sqlite3.Connection) -> tuple[bool, str, int]:
        v = c.execute("SELECT value FROM sync_state WHERE key='vault_id'").fetchone()
        if not v or not v["value"]:
            return False, "local", 1
        d = c.execute("SELECT value FROM sync_state WHERE key='device_id'").fetchone()
        e = c.execute("SELECT value FROM sync_state WHERE key='current_key_epoch'").fetchone()
        return True, (d["value"] if d else "local"), (int(e["value"]) if e else 1)

    def _next_device_seq(self, c: sqlite3.Connection) -> str:
        row = c.execute("SELECT value FROM sync_state WHERE key='local_device_seq'").fetchone()
        nxt = str(int(row["value"]) + 1 if row else 1)
        c.execute(
            "INSERT INTO sync_state(key, value) VALUES ('local_device_seq', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (nxt,),
        )
        return nxt

    def _record_mutation_in_tx(
        self,
        c: sqlite3.Connection,
        *,
        operation_type: str,
        entity_type: str,
        entity_id: str,
        payload_data: dict[str, Any],
        created_at: str,
        explicit_parents: list[str] | None = None,
        media_id: str | None = None,
        media_local_path: str | None = None,
    ) -> tuple[str, list[str]]:
        enabled, device_id, epoch = self._outbox_context(c)
        if explicit_parents is not None:
            parents = explicit_parents
        else:
            heads = c.execute(
                "SELECT revision_id FROM sync_revisions WHERE entity_type=? AND entity_id=? AND is_head=1",
                (entity_type, entity_id),
            ).fetchall()
            parents = [h["revision_id"] for h in heads]

        rev_id = generate_uuid_v4()
        c.execute(
            "UPDATE sync_revisions SET is_head=0 WHERE entity_type=? AND entity_id=? AND is_head=1",
            (entity_type, entity_id),
        )
        c.execute(
            """INSERT INTO sync_revisions
               (entity_type, entity_id, revision_id, parent_revision_ids_json, is_head, operation_type, created_at, device_id)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            (entity_type, entity_id, rev_id, json.dumps(parents), operation_type, created_at, device_id),
        )

        if enabled:
            seq = self._next_device_seq(c)
            c.execute(
                """INSERT INTO sync_operations
                   (operation_id, device_seq, key_epoch, operation_type, entity_type, entity_id,
                    revision_id, parent_revision_ids_json, created_at, payload_data_json, status,
                    media_id, media_local_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    generate_uuid_v4(),
                    seq,
                    epoch,
                    operation_type,
                    entity_type,
                    entity_id,
                    rev_id,
                    json.dumps(parents),
                    created_at,
                    json.dumps(payload_data, separators=(",", ":"), ensure_ascii=False),
                    media_id,
                    media_local_path,
                ),
            )
        return rev_id, parents

    def _resolve_sync_id(self, c: sqlite3.Connection, table: str, local_id: int | None) -> str | None:
        if not local_id:
            return None
        row = c.execute(f"SELECT sync_id FROM {table} WHERE id=?", (local_id,)).fetchone()
        return row["sync_id"] if row else None

    # ── Business Mutations (Atomic SQLite + Outbox) ────────────────────────────

    def save_note(
        self,
        *,
        note_id: int | None = None,
        title: str,
        content: str,
        category_id: int | None = None,
        folder_id: int | None = None,
        is_deleted: bool = False,
        deleted_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self.transaction() as c:
            ts = now_utc_iso()
            meta = metadata or {}
            if note_id is not None:
                existing = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
                if not existing:
                    raise ValueError(f"Note {note_id} not found")
                sync_id = existing["sync_id"] or generate_uuid_v4()
                created_at = normalize_utc_timestamp(existing["created_at"])
                cat_sync = self._resolve_sync_id(c, "categories", category_id)
                fld_sync = self._resolve_sync_id(c, "folders", folder_id)
                op_type = "entity.trash" if is_deleted else "entity.upsert"
                payload = {
                    "title": title,
                    "content": content,
                    "category_id": cat_sync,
                    "folder_id": fld_sync,
                    "is_deleted": bool(is_deleted),
                    "deleted_at": deleted_at,
                    "created_at": created_at,
                    "updated_at": ts,
                    "metadata": meta,
                }
                rev_id, parents = self._record_mutation_in_tx(
                    c,
                    operation_type=op_type,
                    entity_type="note",
                    entity_id=sync_id,
                    payload_data=payload,
                    created_at=ts,
                )
                c.execute(
                    """UPDATE notes SET sync_id=?, revision_id=?, parent_revision_ids_json=?,
                       title=?, content=?, category_id=?, folder_id=?, is_deleted=?, deleted_at=?,
                       updated_at=?, metadata_json=? WHERE id=?""",
                    (
                        sync_id,
                        rev_id,
                        json.dumps(parents),
                        title,
                        content,
                        category_id,
                        folder_id,
                        int(is_deleted),
                        deleted_at,
                        ts,
                        json.dumps(meta),
                        note_id,
                    ),
                )
                return note_id

            sync_id = generate_uuid_v4()
            cat_sync = self._resolve_sync_id(c, "categories", category_id)
            fld_sync = self._resolve_sync_id(c, "folders", folder_id)
            op_type = "entity.trash" if is_deleted else "entity.upsert"
            payload = {
                "title": title,
                "content": content,
                "category_id": cat_sync,
                "folder_id": fld_sync,
                "is_deleted": bool(is_deleted),
                "deleted_at": deleted_at,
                "created_at": ts,
                "updated_at": ts,
                "metadata": meta,
            }
            rev_id, parents = self._record_mutation_in_tx(
                c,
                operation_type=op_type,
                entity_type="note",
                entity_id=sync_id,
                payload_data=payload,
                created_at=ts,
            )
            cur = c.execute(
                """INSERT INTO notes
                   (sync_id, revision_id, parent_revision_ids_json, title, content, category_id,
                    folder_id, created_at, updated_at, is_deleted, deleted_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sync_id,
                    rev_id,
                    json.dumps(parents),
                    title,
                    content,
                    category_id,
                    folder_id,
                    ts,
                    ts,
                    int(is_deleted),
                    deleted_at,
                    json.dumps(meta),
                ),
            )
            return int(cur.lastrowid)

    def trash_note(self, note_id: int) -> None:
        with self.transaction() as c:
            row = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not row:
                return
            ts = now_utc_iso()
            sync_id = row["sync_id"]
            payload = {
                "title": row["title"],
                "content": row["content"],
                "category_id": self._resolve_sync_id(c, "categories", row["category_id"]),
                "folder_id": self._resolve_sync_id(c, "folders", row["folder_id"]),
                "is_deleted": True,
                "deleted_at": ts,
                "created_at": row["created_at"],
                "updated_at": ts,
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
            rev_id, parents = self._record_mutation_in_tx(
                c,
                operation_type="entity.trash",
                entity_type="note",
                entity_id=sync_id,
                payload_data=payload,
                created_at=ts,
            )
            c.execute(
                """UPDATE notes SET is_deleted=1, deleted_at=?, updated_at=?,
                   revision_id=?, parent_revision_ids_json=? WHERE id=?""",
                (ts, ts, rev_id, json.dumps(parents), note_id),
            )

    def restore_note(self, note_id: int) -> None:
        with self.transaction() as c:
            row = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not row:
                return
            ts = now_utc_iso()
            sync_id = row["sync_id"]
            payload = {
                "title": row["title"],
                "content": row["content"],
                "category_id": self._resolve_sync_id(c, "categories", row["category_id"]),
                "folder_id": self._resolve_sync_id(c, "folders", row["folder_id"]),
                "is_deleted": False,
                "deleted_at": None,
                "created_at": row["created_at"],
                "updated_at": ts,
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
            rev_id, parents = self._record_mutation_in_tx(
                c,
                operation_type="entity.restore",
                entity_type="note",
                entity_id=sync_id,
                payload_data=payload,
                created_at=ts,
            )
            c.execute(
                """UPDATE notes SET is_deleted=0, deleted_at=NULL, updated_at=?,
                   revision_id=?, parent_revision_ids_json=? WHERE id=?""",
                (ts, rev_id, json.dumps(parents), note_id),
            )

    def permanently_delete_note(self, note_id: int) -> None:
        with self.transaction() as c:
            row = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not row:
                return
            ts = now_utc_iso()
            for att in c.execute("SELECT * FROM attachments WHERE note_id=?", (note_id,)).fetchall():
                if att["sync_id"]:
                    rev_id, _ = self._record_mutation_in_tx(
                        c,
                        operation_type="media.delete",
                        entity_type="attachment",
                        entity_id=att["sync_id"],
                        payload_data={"media_id": att["media_id"], "deleted_at": ts},
                        created_at=ts,
                        media_id=att["media_id"],
                    )
                    c.execute(
                        "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'attachment', ?, ?, ?)",
                        (f"attachment:{att['sync_id']}", att["sync_id"], rev_id, ts),
                    )
            for link in c.execute("SELECT * FROM note_tags WHERE note_id=?", (note_id,)).fetchall():
                if link["sync_id"]:
                    rev_id, _ = self._record_mutation_in_tx(
                        c,
                        operation_type="entity.delete",
                        entity_type="relation",
                        entity_id=link["sync_id"],
                        payload_data={"deleted_at": ts},
                        created_at=ts,
                    )
                    c.execute(
                        "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'relation', ?, ?, ?)",
                        (f"relation:{link['sync_id']}", link["sync_id"], rev_id, ts),
                    )
            rev_id, _ = self._record_mutation_in_tx(
                c,
                operation_type="entity.delete",
                entity_type="note",
                entity_id=row["sync_id"],
                payload_data={"deleted_at": ts},
                created_at=ts,
            )
            c.execute(
                "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'note', ?, ?, ?)",
                (f"note:{row['sync_id']}", row["sync_id"], rev_id, ts),
            )
            c.execute("DELETE FROM notes WHERE id=?", (note_id,))

    def save_category(
        self, *, name: str, color: str = "#6366F1", sort_order: int = 0, parent_id: int | None = None
    ) -> int:
        with self.transaction() as c:
            ts = now_utc_iso()
            sync_id = generate_uuid_v4()
            parent_sync = self._resolve_sync_id(c, "categories", parent_id)
            rev_id, parents = self._record_mutation_in_tx(
                c,
                operation_type="entity.upsert",
                entity_type="category",
                entity_id=sync_id,
                payload_data={
                    "name": name,
                    "parent_id": parent_sync,
                    "color": color,
                    "sort_order": sort_order,
                    "created_at": ts,
                    "updated_at": ts,
                },
                created_at=ts,
            )
            cur = c.execute(
                """INSERT INTO categories
                   (sync_id, revision_id, parent_revision_ids_json, name, parent_id, color, sort_order, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sync_id, rev_id, json.dumps(parents), name, parent_id, color, sort_order, ts, ts),
            )
            return int(cur.lastrowid)

    def delete_category(self, category_id: int) -> None:
        with self.transaction() as c:
            cat = c.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
            if not cat:
                return
            ts = now_utc_iso()
            for n in c.execute("SELECT * FROM notes WHERE category_id=?", (category_id,)).fetchall():
                payload = {
                    "title": n["title"],
                    "content": n["content"],
                    "category_id": None,
                    "folder_id": self._resolve_sync_id(c, "folders", n["folder_id"]),
                    "is_deleted": bool(n["is_deleted"]),
                    "deleted_at": n["deleted_at"],
                    "created_at": n["created_at"],
                    "updated_at": ts,
                    "metadata": json.loads(n["metadata_json"] or "{}"),
                }
                rev_id, parents = self._record_mutation_in_tx(
                    c,
                    operation_type="entity.trash" if n["is_deleted"] else "entity.upsert",
                    entity_type="note",
                    entity_id=n["sync_id"],
                    payload_data=payload,
                    created_at=ts,
                )
                c.execute(
                    "UPDATE notes SET category_id=NULL, updated_at=?, revision_id=?, parent_revision_ids_json=? WHERE id=?",
                    (ts, rev_id, json.dumps(parents), n["id"]),
                )
            rev_id, _ = self._record_mutation_in_tx(
                c,
                operation_type="entity.delete",
                entity_type="category",
                entity_id=cat["sync_id"],
                payload_data={"deleted_at": ts},
                created_at=ts,
            )
            c.execute(
                "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'category', ?, ?, ?)",
                (f"category:{cat['sync_id']}", cat["sync_id"], rev_id, ts),
            )
            c.execute("DELETE FROM categories WHERE id=?", (category_id,))

    def save_folder(self, *, name: str, parent_id: int | None = None) -> int:
        with self.transaction() as c:
            ts = now_utc_iso()
            sync_id = generate_uuid_v4()
            parent_sync = self._resolve_sync_id(c, "folders", parent_id)
            rev_id, parents = self._record_mutation_in_tx(
                c,
                operation_type="entity.upsert",
                entity_type="folder",
                entity_id=sync_id,
                payload_data={"name": name, "parent_id": parent_sync, "created_at": ts, "updated_at": ts},
                created_at=ts,
            )
            cur = c.execute(
                """INSERT INTO folders
                   (sync_id, revision_id, parent_revision_ids_json, name, parent_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (sync_id, rev_id, json.dumps(parents), name, parent_id, ts, ts),
            )
            return int(cur.lastrowid)

    def add_tag_to_note(self, note_id: int, tag_name: str, color: str = "#6366F1") -> tuple[int, str]:
        norm = normalize_tag_name(tag_name)
        if not norm:
            raise ValueError("Tag name cannot be empty")
        with self.transaction() as c:
            ts = now_utc_iso()
            note = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not note:
                raise ValueError(f"Note {note_id} not found")
            tag = c.execute("SELECT * FROM tags WHERE name=?", (norm,)).fetchone()
            if not tag:
                tag_sync_id = generate_uuid_v4()
                t_rev, t_parents = self._record_mutation_in_tx(
                    c,
                    operation_type="entity.upsert",
                    entity_type="tag",
                    entity_id=tag_sync_id,
                    payload_data={"name": norm, "color": color, "created_at": ts, "updated_at": ts},
                    created_at=ts,
                )
                t_cur = c.execute(
                    """INSERT INTO tags (sync_id, revision_id, parent_revision_ids_json, name, color, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (tag_sync_id, t_rev, json.dumps(t_parents), norm, color, ts, ts),
                )
                tag_id = int(t_cur.lastrowid)
            else:
                tag_id = int(tag["id"])
                tag_sync_id = tag["sync_id"]

            rel = c.execute("SELECT * FROM note_tags WHERE note_id=? AND tag_id=?", (note_id, tag_id)).fetchone()
            if rel:
                return tag_id, rel["sync_id"]

            rel_sync_id = generate_uuid_v4()
            r_rev, r_parents = self._record_mutation_in_tx(
                c,
                operation_type="entity.upsert",
                entity_type="relation",
                entity_id=rel_sync_id,
                payload_data={
                    "relation_type": "note_tag",
                    "note_id": note["sync_id"],
                    "tag_id": tag_sync_id,
                    "created_at": ts,
                },
                created_at=ts,
            )
            c.execute(
                """INSERT INTO note_tags (note_id, tag_id, sync_id, revision_id, parent_revision_ids_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (note_id, tag_id, rel_sync_id, r_rev, json.dumps(r_parents), ts),
            )
            return tag_id, rel_sync_id

    def remove_tag_from_note(self, note_id: int, tag_id: int) -> None:
        with self.transaction() as c:
            rel = c.execute("SELECT * FROM note_tags WHERE note_id=? AND tag_id=?", (note_id, tag_id)).fetchone()
            if not rel:
                return
            ts = now_utc_iso()
            if rel["sync_id"]:
                rev_id, _ = self._record_mutation_in_tx(
                    c,
                    operation_type="entity.delete",
                    entity_type="relation",
                    entity_id=rel["sync_id"],
                    payload_data={"deleted_at": ts},
                    created_at=ts,
                )
                c.execute(
                    "INSERT OR REPLACE INTO sync_tombstones VALUES (?, 'relation', ?, ?, ?)",
                    (f"relation:{rel['sync_id']}", rel["sync_id"], rev_id, ts),
                )
            c.execute("DELETE FROM note_tags WHERE note_id=? AND tag_id=?", (note_id, tag_id))

    def add_attachment(
        self,
        *,
        note_id: int,
        media_type: str,
        filename: str,
        filepath: str,
        mime_type: str = "application/octet-stream",
        original_size: int = 0,
        duration: float | None = None,
        thumbnail_data_url: str | None = None,
    ) -> int:
        with self.transaction() as c:
            note = c.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not note:
                raise ValueError(f"Note {note_id} not found")
            ts = now_utc_iso()
            sync_id = generate_uuid_v4()
            media_id = generate_uuid_v4()
            _, _, epoch = self._outbox_context(c)
            payload = {
                "note_id": note["sync_id"],
                "media_id": media_id,
                "media_type": media_type,
                "media_crypto_version": 1,
                "key_epoch": epoch,
                "encrypted_size": 0,
                "encrypted_sha256": "",
                "original_size": original_size,
                "mime_type": mime_type,
                "file_name": filename,
                "duration": duration,
                "thumbnail_data_url": thumbnail_data_url,
                "created_at": ts,
            }
            rev_id, parents = self._record_mutation_in_tx(
                c,
                operation_type="media.register",
                entity_type="attachment",
                entity_id=sync_id,
                payload_data=payload,
                created_at=ts,
                media_id=media_id,
                media_local_path=filepath,
            )
            cur = c.execute(
                """INSERT INTO attachments
                   (sync_id, media_id, revision_id, parent_revision_ids_json, note_id, type, filename,
                    filepath, duration, created_at, mime_type, original_size, key_epoch, media_status, thumbnail_data_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_upload', ?)""",
                (
                    sync_id,
                    media_id,
                    rev_id,
                    json.dumps(parents),
                    note_id,
                    media_type,
                    filename,
                    filepath,
                    duration,
                    ts,
                    mime_type,
                    original_size,
                    epoch,
                    thumbnail_data_url,
                ),
            )
            return int(cur.lastrowid)

    def enqueue_existing_snapshot(self) -> int:
        """Queue initial sync operations for existing local data when joining/creating a vault."""
        with self.transaction() as c:
            enabled, _, epoch = self._outbox_context(c)
            if not enabled:
                return 0
            existing = {
                (r["entity_type"], r["entity_id"])
                for r in c.execute("SELECT entity_type, entity_id FROM sync_operations").fetchall()
            }
            existing.update(
                (r["entity_type"], r["entity_id"])
                for r in c.execute(
                    "SELECT entity_type, entity_id FROM sync_revisions WHERE device_id != 'local'"
                ).fetchall()
            )
            queued = 0

            for cat in c.execute("SELECT * FROM categories ORDER BY id ASC").fetchall():
                if ("category", cat["sync_id"]) in existing:
                    continue
                self._record_mutation_in_tx(
                    c,
                    operation_type="entity.upsert",
                    entity_type="category",
                    entity_id=cat["sync_id"],
                    payload_data={
                        "name": cat["name"],
                        "parent_id": self._resolve_sync_id(c, "categories", cat["parent_id"]),
                        "color": cat["color"],
                        "sort_order": cat["sort_order"],
                        "created_at": cat["created_at"],
                        "updated_at": cat["updated_at"],
                    },
                    created_at=cat["updated_at"],
                    explicit_parents=[],
                )
                queued += 1

            for fld in c.execute("SELECT * FROM folders ORDER BY id ASC").fetchall():
                if ("folder", fld["sync_id"]) in existing:
                    continue
                self._record_mutation_in_tx(
                    c,
                    operation_type="entity.upsert",
                    entity_type="folder",
                    entity_id=fld["sync_id"],
                    payload_data={
                        "name": fld["name"],
                        "parent_id": self._resolve_sync_id(c, "folders", fld["parent_id"]),
                        "created_at": fld["created_at"],
                        "updated_at": fld["updated_at"],
                    },
                    created_at=fld["updated_at"],
                    explicit_parents=[],
                )
                queued += 1

            for tag in c.execute("SELECT * FROM tags ORDER BY id ASC").fetchall():
                if ("tag", tag["sync_id"]) in existing:
                    continue
                self._record_mutation_in_tx(
                    c,
                    operation_type="entity.upsert",
                    entity_type="tag",
                    entity_id=tag["sync_id"],
                    payload_data={
                        "name": tag["name"],
                        "color": tag["color"],
                        "created_at": tag["created_at"],
                        "updated_at": tag["updated_at"],
                    },
                    created_at=tag["updated_at"],
                    explicit_parents=[],
                )
                queued += 1

            for note in c.execute("SELECT * FROM notes ORDER BY id ASC").fetchall():
                if ("note", note["sync_id"]) in existing:
                    continue
                self._record_mutation_in_tx(
                    c,
                    operation_type="entity.trash" if note["is_deleted"] else "entity.upsert",
                    entity_type="note",
                    entity_id=note["sync_id"],
                    payload_data={
                        "title": note["title"],
                        "content": note["content"],
                        "category_id": self._resolve_sync_id(c, "categories", note["category_id"]),
                        "folder_id": self._resolve_sync_id(c, "folders", note["folder_id"]),
                        "is_deleted": bool(note["is_deleted"]),
                        "deleted_at": note["deleted_at"],
                        "created_at": note["created_at"],
                        "updated_at": note["updated_at"],
                        "metadata": json.loads(note["metadata_json"] or "{}"),
                    },
                    created_at=note["updated_at"],
                    explicit_parents=[],
                )
                queued += 1

            for nt in c.execute("SELECT * FROM note_tags").fetchall():
                if ("relation", nt["sync_id"]) in existing:
                    continue
                n_sync = self._resolve_sync_id(c, "notes", nt["note_id"])
                t_sync = self._resolve_sync_id(c, "tags", nt["tag_id"])
                if not n_sync or not t_sync:
                    continue
                self._record_mutation_in_tx(
                    c,
                    operation_type="entity.upsert",
                    entity_type="relation",
                    entity_id=nt["sync_id"],
                    payload_data={
                        "relation_type": "note_tag",
                        "note_id": n_sync,
                        "tag_id": t_sync,
                        "created_at": nt["created_at"],
                    },
                    created_at=nt["created_at"],
                    explicit_parents=[],
                )
                queued += 1

            for att in c.execute("SELECT * FROM attachments ORDER BY id ASC").fetchall():
                if ("attachment", att["sync_id"]) in existing:
                    continue
                n_sync = self._resolve_sync_id(c, "notes", att["note_id"])
                if not n_sync:
                    continue
                self._record_mutation_in_tx(
                    c,
                    operation_type="media.register",
                    entity_type="attachment",
                    entity_id=att["sync_id"],
                    payload_data={
                        "note_id": n_sync,
                        "media_id": att["media_id"],
                        "media_type": att["type"],
                        "media_crypto_version": 1,
                        "key_epoch": epoch,
                        "encrypted_size": att["encrypted_size"] or 0,
                        "encrypted_sha256": att["encrypted_sha256"] or "",
                        "original_size": att["original_size"] or 0,
                        "mime_type": att["mime_type"] or "application/octet-stream",
                        "file_name": att["filename"],
                        "duration": att["duration"],
                        "thumbnail_data_url": att["thumbnail_data_url"],
                        "created_at": att["created_at"],
                    },
                    created_at=att["created_at"],
                    explicit_parents=[],
                    media_id=att["media_id"],
                    media_local_path=att["filepath"],
                )
                queued += 1
            return queued
