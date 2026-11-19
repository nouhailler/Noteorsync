"""Phase 6 & 7 tests: Live Sync Server protocol edge cases, backups, and Desktop E2E sync."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sync-server"))

from app.stdlib_server import start_background_server  # noqa: E402
from sync.desktop import crypto as pyc  # noqa: E402
from sync.desktop.engine import DesktopSyncEngine, SyncHttpClient, SyncHttpError, VaultKeyring  # noqa: E402
from sync.desktop.sqlite_sync_adapter import DesktopSyncDatabase  # noqa: E402


class TestServerAndDesktopE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.httpd, self.storage, self.url = start_background_server(
            self.base / "server.db",
            self.base / "blobs",
        )

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.storage.close()
        self.tmp.cleanup()

    def test_protocol_errors_idempotency_and_backup(self) -> None:
        vault_id = pyc.generate_uuid_v4()
        dev_a = pyc.generate_uuid_v4()
        vsk = os.urandom(32)
        pub_a, _priv_a = pyc.generate_device_keypair()

        anon_client = SyncHttpClient(self.url)
        boot = anon_client.bootstrap_vault(
            {
                "vault_id": vault_id,
                "device_id": dev_a,
                "device_name": "Dev A",
                "device_type": "desktop",
                "public_key_x25519": pyc.to_b64u(pub_a),
            }
        )
        token_a = boot["device_access_token"]
        client_a = SyncHttpClient(self.url, token_a)

        # 401 on bad token
        with self.assertRaises(SyncHttpError) as ctx401:
            SyncHttpClient(self.url, "ntr_dev_wrong").list_devices(vault_id)
        self.assertEqual(ctx401.exception.status, 401)
        self.assertEqual(ctx401.exception.code, "AUTH_INVALID")

        # Prepare two valid encrypted envelopes
        k_op, _ = pyc.derive_epoch_keys(pyc.derive_initial_epoch1_root_key(vsk, vault_id), vault_id, 1)
        op1_id = pyc.generate_uuid_v4()
        op2_id = pyc.generate_uuid_v4()
        env1 = pyc.encrypt_operation_envelope(
            k_op,
            {"vault_id": vault_id, "device_id": dev_a, "operation_id": op1_id, "device_seq": "1", "key_epoch": 1},
            {"payload_version": 1, "operation_type": "entity.upsert", "entity_type": "note", "entity_id": pyc.generate_uuid_v4(), "revision_id": pyc.generate_uuid_v4(), "parent_revision_ids": [], "created_at": "2026-11-19T12:00:00.000Z", "data": {"title": "T1"}},
        )
        env2 = pyc.encrypt_operation_envelope(
            k_op,
            {"vault_id": vault_id, "device_id": dev_a, "operation_id": op2_id, "device_seq": "2", "key_epoch": 1},
            {"payload_version": 1, "operation_type": "entity.upsert", "entity_type": "note", "entity_id": pyc.generate_uuid_v4(), "revision_id": pyc.generate_uuid_v4(), "parent_revision_ids": [], "created_at": "2026-11-19T12:01:00.000Z", "data": {"title": "T2"}},
        )

        # Sequence gap (sending seq 2 before seq 1) -> 409 DEVICE_SEQUENCE_GAP
        with self.assertRaises(SyncHttpError) as ctx_gap:
            client_a.push_operations(vault_id, [env2])
        self.assertEqual(ctx_gap.exception.status, 409)
        self.assertEqual(ctx_gap.exception.code, "DEVICE_SEQUENCE_GAP")
        self.assertEqual(ctx_gap.exception.details.get("next_expected_device_seq"), "1")

        # Valid push + idempotent retry
        push1 = client_a.push_operations(vault_id, [env1])
        self.assertEqual(len(push1["accepted"]), 1)
        push_dup = client_a.push_operations(vault_id, [env1])
        self.assertTrue(push_dup["accepted"][0].get("duplicate"))

        # OPERATION_ID_REUSE when same op1_id reused with different ciphertext
        env1_tampered = dict(env1)
        env1_tampered["ciphertext"] = env2["ciphertext"]
        with self.assertRaises(SyncHttpError) as ctx_reuse:
            client_a.push_operations(vault_id, [env1_tampered])
        self.assertEqual(ctx_reuse.exception.code, "OPERATION_ID_REUSE")

        # DEVICE_SEQUENCE_REUSE when fresh op_id tries device_seq "1"
        env_seq1_diff = pyc.encrypt_operation_envelope(
            k_op,
            {"vault_id": vault_id, "device_id": dev_a, "operation_id": pyc.generate_uuid_v4(), "device_seq": "1", "key_epoch": 1},
            {"payload_version": 1, "operation_type": "entity.upsert", "entity_type": "note", "entity_id": pyc.generate_uuid_v4(), "revision_id": pyc.generate_uuid_v4(), "parent_revision_ids": [], "created_at": "2026-11-19T12:02:00.000Z", "data": {"title": "Conflict seq"}},
        )
        with self.assertRaises(SyncHttpError) as ctx_seq_reuse:
            client_a.push_operations(vault_id, [env_seq1_diff])
        self.assertEqual(ctx_seq_reuse.exception.code, "DEVICE_SEQUENCE_REUSE")

        # SQLite online backup test
        backup_db = self.base / "backup.db"
        self.storage.backup_to(backup_db)
        self.assertTrue(backup_db.is_file())

    def test_full_desktop_to_desktop_e2e_pairing_media_conflict_revocation(self) -> None:
        # ── Device A setup ──
        db_a = DesktopSyncDatabase(self.base / "dev_a.db")
        vault_id = pyc.generate_uuid_v4()
        dev_a = pyc.generate_uuid_v4()
        vsk = os.urandom(32)
        pub_a, priv_a = pyc.generate_device_keypair()

        client_a = SyncHttpClient(self.url)
        boot = client_a.bootstrap_vault(
            {
                "vault_id": vault_id,
                "device_id": dev_a,
                "device_name": "Workstation A",
                "device_type": "desktop",
                "public_key_x25519": pyc.to_b64u(pub_a),
            }
        )
        client_a.access_token = boot["device_access_token"]
        db_a.set_state("vault_id", vault_id)
        db_a.set_state("device_id", dev_a)
        kr_a = VaultKeyring.from_vsk(vault_id, dev_a, vsk, 1, (pub_a, priv_a))
        eng_a = DesktopSyncEngine(db_a, client_a, kr_a, self.base / "media_a")

        # Create category, note, tag & binary attachment on Device A
        cat_id = db_a.save_category(name="Projets", color="#10B981")
        n_id = db_a.save_note(title="Plan Alpha", content="Contenu initial [[Plan Alpha]]", category_id=cat_id)
        db_a.add_tag_to_note(n_id, "Prioritaire")
        raw_blob = b"\x89PNG\r\n\x1a\n" + os.urandom(64 * 1024) + b"END"
        att_src = self.base / "diagram.png"
        att_src.write_bytes(raw_blob)
        db_a.add_attachment(note_id=n_id, media_type="image", filename="diagram.png", filepath=str(att_src), mime_type="image/png")

        res_a1 = eng_a.sync_now()
        self.assertGreaterEqual(res_a1.pushed_count, 4)
        self.assertEqual(res_a1.media_uploaded, 1)

        # ── Pair Device B via live HTTP ──
        pairing_id = pyc.generate_uuid_v4()
        pairing_secret = os.urandom(32)
        pairing_nonce = os.urandom(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        client_a.create_pairing_session(
            vault_id,
            {
                "pairing_id": pairing_id,
                "device_a_id": dev_a,
                "pairing_nonce": pyc.to_b64u(pairing_nonce),
                "expires_at": expires_at,
            },
        )

        dev_b = pyc.generate_uuid_v4()
        pub_b, priv_b = pyc.generate_device_keypair()
        session_nonce_b = pyc.to_b64u(os.urandom(24))
        secret_proof = pyc.compute_secret_proof_hmac(pairing_secret, vault_id, pairing_id, dev_b, session_nonce_b)

        anon_b = SyncHttpClient(self.url)
        join_res = anon_b.join_pairing_session(
            vault_id,
            pairing_id,
            {
                "device_b_id": dev_b,
                "device_b_name": "Laptop B",
                "device_b_type": "desktop",
                "device_b_public_key_x25519": pyc.to_b64u(pub_b),
                "session_nonce_b": session_nonce_b,
                "secret_proof_hmac": secret_proof,
            },
        )
        ctx = {
            "vault_id": vault_id,
            "pairing_id": pairing_id,
            "device_a_id": join_res["device_a_id"],
            "device_b_id": dev_b,
            "session_nonce_b": session_nonce_b,
        }
        k_pair = pyc.derive_pairing_key(
            pairing_secret,
            pyc.from_b64u(join_res["pairing_nonce"]),
            vault_id,
            pairing_id,
            join_res["device_a_id"],
            dev_b,
            session_nonce_b,
        )

        # Device A approves
        st = client_a.get_pairing_status(vault_id, pairing_id)
        self.assertEqual(st["status"], "joined")
        bundle = {
            "vault_id": vault_id,
            "vsk_b64u": pyc.to_b64u(vsk),
            "current_key_epoch": kr_a.current_key_epoch,
            "epoch_keys": {"1": pyc.to_b64u(kr_a.epoch_root_keys[1])},
        }
        enc_bundle = pyc.encrypt_pairing_bundle(k_pair, ctx, bundle)
        client_a.approve_pairing_session(
            vault_id,
            pairing_id,
            {"bundle_nonce": enc_bundle["nonce"], "encrypted_vault_bundle": enc_bundle["ciphertext"]},
        )

        # Device B decrypts bundle and confirms
        st2 = anon_b.get_pairing_status(vault_id, pairing_id)
        dec_bundle = pyc.decrypt_pairing_bundle(k_pair, ctx, st2["bundle_nonce"], st2["encrypted_vault_bundle"])
        confirm_hmac = pyc.compute_pairing_confirm_hmac(k_pair, pairing_id, dev_b, vault_id)
        conf_res = anon_b.confirm_pairing_session(
            vault_id, pairing_id, {"device_b_id": dev_b, "confirm_hmac": confirm_hmac}
        )

        client_b = SyncHttpClient(self.url, conf_res["device_access_token"])
        db_b = DesktopSyncDatabase(self.base / "dev_b.db")
        db_b.set_state("vault_id", vault_id)
        db_b.set_state("device_id", dev_b)
        kr_b = VaultKeyring.from_vsk(vault_id, dev_b, pyc.from_b64u(dec_bundle["vsk_b64u"]), 1, (pub_b, priv_b))
        eng_b = DesktopSyncEngine(db_b, client_b, kr_b, self.base / "media_b")

        res_b1 = eng_b.sync_now()
        self.assertGreaterEqual(res_b1.pulled_count, 4)
        self.assertEqual(res_b1.media_downloaded, 1)

        note_b = db_b.conn.execute("SELECT * FROM notes WHERE title='Plan Alpha'").fetchone()
        self.assertIsNotNone(note_b)
        att_b = db_b.conn.execute("SELECT * FROM attachments WHERE note_id=?", (note_b["id"],)).fetchone()
        self.assertEqual(att_b["media_status"], "available")
        self.assertEqual(Path(att_b["filepath"]).read_bytes(), raw_blob)

        # ── Concurrent offline edits -> conflict -> resolution ──
        db_a.save_note(title="Plan Alpha", content="Version Device A", note_id=n_id, category_id=cat_id)
        db_b.save_note(title="Plan Alpha", content="Version Device B", note_id=note_b["id"], category_id=note_b["category_id"])

        eng_a.sync_now()
        res_b2 = eng_b.sync_now()
        self.assertEqual(res_b2.conflicts_created, 1)

        c_row = db_b.conn.execute("SELECT * FROM sync_conflicts WHERE status='open'").fetchone()
        eng_b.resolve_conflict(
            c_row["conflict_id"],
            "manual_merge",
            {"title": "Plan Alpha Fusionné", "content": "Version A + Version B"},
        )
        eng_b.sync_now()
        eng_a.sync_now()
        a_final = db_a.conn.execute("SELECT * FROM notes WHERE id=?", (n_id,)).fetchone()
        self.assertEqual(a_final["title"], "Plan Alpha Fusionné")
        self.assertEqual(a_final["content"], "Version A + Version B")

        # ── Revocation & Epoch 2 key rotation ──
        new_ep = eng_a.revoke_device_and_rotate_epoch(dev_b)
        self.assertEqual(new_ep, 2)

        with self.assertRaises(SyncHttpError) as ctx_revoked:
            eng_b.sync_now()
        self.assertEqual(ctx_revoked.exception.status, 403)
        self.assertEqual(ctx_revoked.exception.code, "DEVICE_REVOKED")

        # Device A continues syncing cleanly under Epoch 2
        db_a.save_note(title="Secret Epoch 2", content="Inaccessible au périphérique révoqué")
        res_a2 = eng_a.sync_now()
        self.assertGreaterEqual(res_a2.pushed_count, 1)

        db_a.close()
        db_b.close()


if __name__ == "__main__":
    unittest.main()
