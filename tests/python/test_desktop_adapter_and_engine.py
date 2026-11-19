"""Phase 4 & 5 tests for Desktop SQLite WAL adapter, migrations, and offline SyncEngine."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sync.desktop import crypto as pyc  # noqa: E402
from sync.desktop.engine import DesktopSyncEngine, SyncHttpClient, VaultKeyring  # noqa: E402
from sync.desktop.sqlite_sync_adapter import DesktopSyncDatabase  # noqa: E402


class TestDesktopAdapterAndEngine(unittest.TestCase):
    def test_v1_migration_uuid_wikilinks_tag_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            raw = sqlite3.connect(db_path)
            raw.executescript(
                """
                CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, parent_id INTEGER, color TEXT NOT NULL, sort_order INTEGER NOT NULL);
                CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, parent_id INTEGER, created_at TEXT NOT NULL);
                CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, content TEXT NOT NULL, category_id INTEGER, folder_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, is_deleted INTEGER NOT NULL DEFAULT 0, deleted_at TEXT);
                CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, color TEXT NOT NULL);
                CREATE TABLE note_tags (note_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY (note_id, tag_id));
                CREATE TABLE attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER NOT NULL, type TEXT NOT NULL, filename TEXT NOT NULL, filepath TEXT NOT NULL, duration REAL, created_at TEXT NOT NULL);
                INSERT INTO notes (id, title, content, created_at, updated_at, is_deleted) VALUES (1, 'Target', 'Body', '2026-11-18 09:00:00', '2026-11-18 09:05:00', 0);
                INSERT INTO notes (id, title, content, created_at, updated_at, is_deleted) VALUES (2, 'Source', 'Link [[Target|1]]', '2026-11-18 09:10:00', '2026-11-18 09:15:00', 0);
                INSERT INTO tags (id, name, color) VALUES (1, 'Café', '#111111'), (2, 'café ', '#222222');
                INSERT INTO note_tags (note_id, tag_id) VALUES (1, 1), (1, 2);
                """
            )
            raw.commit()
            raw.close()

            adapter = DesktopSyncDatabase(db_path)
            n1 = adapter.conn.execute("SELECT * FROM notes WHERE id=1").fetchone()
            n2 = adapter.conn.execute("SELECT * FROM notes WHERE id=2").fetchone()
            self.assertEqual(n1["created_at"], "2026-11-18T09:00:00.000Z")
            self.assertEqual(n2["content"], f"Link [[Target|@{n1['sync_id']}]]")

            tags = adapter.conn.execute("SELECT * FROM tags").fetchall()
            self.assertEqual(len(tags), 1)
            self.assertEqual(tags[0]["name"], "café")
            links = adapter.conn.execute("SELECT * FROM note_tags WHERE note_id=1").fetchall()
            self.assertEqual(len(links), 1)
            adapter.close()

    def test_offline_outbox_deps_and_dag_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = DesktopSyncDatabase(Path(tmp) / "desktop.db")
            vault_id = pyc.generate_uuid_v4()
            dev_a = pyc.generate_uuid_v4()
            dev_b = pyc.generate_uuid_v4()
            adapter.set_state("vault_id", vault_id)
            adapter.set_state("device_id", dev_a)

            keyring = VaultKeyring.from_vsk(vault_id, dev_a, b"\x01" * 32)
            engine = DesktopSyncEngine(adapter, SyncHttpClient("http://localhost"), keyring, Path(tmp) / "media")

            cat_id = adapter.save_category(name="Cat1")
            n_id = adapter.save_note(title="Local Note", content="V1", category_id=cat_id)
            adapter.add_tag_to_note(n_id, "Travail")
            adapter.trash_note(n_id)
            adapter.restore_note(n_id)

            ops = adapter.conn.execute("SELECT * FROM sync_operations ORDER BY id ASC").fetchall()
            self.assertEqual([o["device_seq"] for o in ops], [str(i + 1) for i in range(len(ops))])

            # Incoming concurrent remote edit vs local
            n_row = adapter.conn.execute("SELECT * FROM notes WHERE id=?", (n_id,)).fetchone()
            remote_rev = pyc.generate_uuid_v4()
            outcome = engine.apply_decrypted_operation(
                pyc.generate_uuid_v4(),
                "10",
                dev_b,
                {
                    "payload_version": 1,
                    "operation_type": "entity.upsert",
                    "entity_type": "note",
                    "entity_id": n_row["sync_id"],
                    "revision_id": remote_rev,
                    "parent_revision_ids": [],  # Missing local head ancestry => conflict
                    "created_at": "2026-11-19T12:00:00.000Z",
                    "data": {
                        "title": "Local Note",
                        "content": "Remote Edit",
                        "category_id": None,
                        "folder_id": None,
                        "is_deleted": False,
                        "deleted_at": None,
                        "created_at": n_row["created_at"],
                        "updated_at": "2026-11-19T12:00:00.000Z",
                        "metadata": {},
                    },
                },
            )
            self.assertEqual(outcome, "conflict")
            conflicts = adapter.conn.execute("SELECT * FROM sync_conflicts WHERE status='open'").fetchall()
            self.assertEqual(len(conflicts), 1)
            engine.resolve_conflict(conflicts[0]["conflict_id"], "manual_merge", {"title": "Merged", "content": "V1 + Remote Edit"})
            after = adapter.conn.execute("SELECT * FROM notes WHERE id=?", (n_id,)).fetchone()
            self.assertEqual(after["content"], "V1 + Remote Edit")
            adapter.close()

    def test_local_offline_without_sync_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = DesktopSyncDatabase(Path(tmp) / "offline_only.db")
            self.assertIsNone(adapter.get_state("vault_id"))
            cat_id = adapter.save_category(name="Personnel", color="#4F46E5")
            folder_id = adapter.save_folder(name="Journal")
            note_id = adapter.save_note(
                title="Note locale autonome",
                content="Contenu complet avec [[Wikilink]]",
                category_id=cat_id,
                folder_id=folder_id,
            )
            adapter.add_tag_to_note(note_id, "Hors-ligne")
            adapter.trash_note(note_id)
            self.assertEqual(adapter.conn.execute("SELECT is_deleted FROM notes WHERE id=?", (note_id,)).fetchone()["is_deleted"], 1)
            adapter.restore_note(note_id)
            row = adapter.conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            self.assertEqual(row["is_deleted"], 0)
            self.assertEqual(row["title"], "Note locale autonome")
            # No sync operations queued when vault_id is unset (pure local mode)
            self.assertEqual(adapter.conn.execute("SELECT COUNT(*) FROM sync_operations").fetchone()[0], 0)
            adapter.close()


if __name__ == "__main__":
    unittest.main()
