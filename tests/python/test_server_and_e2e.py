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

    def test_v11_security_and_protocol_hardening(self) -> None:
        import json
        import socket
        import urllib.request

        # 1. /healthz and /api/v1/health
        with urllib.request.urlopen(f"{self.url}/healthz") as r1:
            self.assertEqual(r1.status, 200)
            self.assertEqual(json.loads(r1.read())["version"], "1.1.0")
        with urllib.request.urlopen(f"{self.url}/api/v1/health") as r2:
            self.assertEqual(r2.status, 200)
            body2 = json.loads(r2.read())
            self.assertEqual(body2["api_version"], 1)

        # 2. Create two isolated vaults -> GET /api/v1/vaults/{vault_id} & VAULT_MISMATCH
        v1 = pyc.generate_uuid_v4()
        d1 = pyc.generate_uuid_v4()
        pk1, _ = pyc.generate_device_keypair()
        c1 = SyncHttpClient(self.url)
        b1 = c1.bootstrap_vault({
            "vault_id": v1, "device_id": d1, "device_name": "D1",
            "device_type": "desktop", "public_key_x25519": pyc.to_b64u(pk1),
        })
        c1.access_token = b1["device_access_token"]
        vinfo = c1.get_vault_info(v1)
        self.assertEqual(vinfo["vault_id"], v1)
        self.assertEqual(vinfo["current_key_epoch"], 1)
        self.assertEqual(vinfo["protocol_version"], 1)

        v2 = pyc.generate_uuid_v4()
        d2 = pyc.generate_uuid_v4()
        pk2, _ = pyc.generate_device_keypair()
        SyncHttpClient(self.url).bootstrap_vault({
            "vault_id": v2, "device_id": d2, "device_name": "D2",
            "device_type": "pwa", "public_key_x25519": pyc.to_b64u(pk2),
        })
        with self.assertRaises(SyncHttpError) as ctx_vm:
            c1.get_vault_info(v2)
        self.assertEqual(ctx_vm.exception.status, 403)
        self.assertEqual(ctx_vm.exception.code, "VAULT_MISMATCH")

        # 3. rotate_epoch must reject extra unknown/revoked device keys
        with self.assertRaises(SyncHttpError) as ctx_wk:
            c1.rotate_epoch(v1, {
                "new_key_epoch": 2,
                "reason": "manual",
                "wrapped_keys": {
                    d1: pyc.to_b64u(os.urandom(48)),
                    d2: pyc.to_b64u(os.urandom(48)),  # foreign/non-active device!
                },
            })
        self.assertEqual(ctx_wk.exception.status, 400)
        self.assertEqual(ctx_wk.exception.code, "INVALID_WRAPPED_KEYS")

        # 4. Media chunked upload bounds: 413 CHUNK_TOO_LARGE, 409 SIZE_EXCEEDED, 400 CHECKSUM_MISMATCH
        m_id = pyc.generate_uuid_v4()
        blob = os.urandom(64)
        wrong_sha = pyc.sha256_hex(b"other-bytes")
        c1.init_media_upload(v1, {
            "media_id": m_id, "encrypted_size": 64, "encrypted_sha256": wrong_sha, "key_epoch": 1, "chunk_size": 64,
        })
        # Chunk > 4 MiB declared
        big_chunk = b"\x00" * (4 * 1024 * 1024 + 1)
        with self.assertRaises(SyncHttpError) as ctx_413:
            c1.upload_media_chunk(v1, m_id, big_chunk, 0, 64)
        self.assertEqual(ctx_413.exception.status, 413)
        self.assertEqual(ctx_413.exception.code, "CHUNK_TOO_LARGE")

        # Upload all 64 bytes -> complete fails CHECKSUM_MISMATCH
        c1.upload_media_chunk(v1, m_id, blob, 0, 64)
        with self.assertRaises(SyncHttpError) as ctx_sha:
            c1.complete_media_upload(v1, m_id)
        self.assertEqual(ctx_sha.exception.status, 400)
        self.assertEqual(ctx_sha.exception.code, "CHECKSUM_MISMATCH")

        # SIZE_EXCEEDED when range end >= total (even with matching start offset)
        m2_id = pyc.generate_uuid_v4()
        c1.init_media_upload(v1, {
            "media_id": m2_id, "encrypted_size": 50, "encrypted_sha256": pyc.sha256_hex(b"x" * 50), "key_epoch": 1,
        })
        with self.assertRaises(SyncHttpError) as ctx_sz:
            c1.upload_media_chunk(v1, m2_id, b"a" * 51, 0, 50)
        self.assertEqual(ctx_sz.exception.status, 409)
        self.assertEqual(ctx_sz.exception.code, "SIZE_EXCEEDED")

        # 5. Pairing duplicate ID & expired session -> 409 PAIRING_EXISTS & 410 PAIRING_EXPIRED
        p_id = pyc.generate_uuid_v4()
        fut = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        c1.create_pairing_session(v1, {
            "pairing_id": p_id, "device_a_id": d1, "pairing_nonce": pyc.to_b64u(os.urandom(16)), "expires_at": fut,
        })
        with self.assertRaises(SyncHttpError) as ctx_pe:
            c1.create_pairing_session(v1, {
                "pairing_id": p_id, "device_a_id": d1, "pairing_nonce": pyc.to_b64u(os.urandom(16)), "expires_at": fut,
            })
        self.assertEqual(ctx_pe.exception.status, 409)
        self.assertEqual(ctx_pe.exception.code, "PAIRING_EXISTS")

        p_exp = pyc.generate_uuid_v4()
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        c1.create_pairing_session(v1, {
            "pairing_id": p_exp, "device_a_id": d1, "pairing_nonce": pyc.to_b64u(os.urandom(16)), "expires_at": past,
        })
        with self.assertRaises(SyncHttpError) as ctx_410:
            c1.get_pairing_status(v1, p_exp)
        self.assertEqual(ctx_410.exception.status, 410)
        self.assertEqual(ctx_410.exception.code, "PAIRING_EXPIRED")

        # 6. Malformed Content-Length handled cleanly as 400 INVALID_PAYLOAD
        host, port = self.httpd.server_address
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"POST /api/v1/vaults HTTP/1.1\r\nHost: localhost\r\nContent-Length: -5\r\nConnection: close\r\n\r\n")
            chunks: list[bytes] = []
            while True:
                part = sock.recv(4096)
                if not part:
                    break
                chunks.append(part)
            raw_resp = b"".join(chunks).decode("utf-8", errors="replace")
        self.assertIn("400", raw_resp.splitlines()[0])
        self.assertIn("INVALID_PAYLOAD", raw_resp)


if __name__ == "__main__":
    unittest.main()
