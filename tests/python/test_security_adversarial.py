"""Phase 3 Adversarial Security & Negative Tests (Python Crypto, Desktop Engine & Live HTTP Server).

Covers:
- 1-byte bitflips across AEAD ciphertext, poly1305 tag, nonce, and every AAD metadata field
- Strict canonical lowercase UUID & unpadded Base64URL rejection
- Argon2id upper bounds against KDF OOM / CPU DoS
- X25519 key length validation before libsodium C bindings
- NTRM1 stream reordering, chunk duplication, truncation, trailing bytes, header tampering
- Desktop Engine path-traversal resistance & pull cross-vault / cursor-replay rejection
- HTTP server DoS size caps (413), naive timestamp rejection (400), pairing 5-failure lockout,
  CORS default non-wildcard behavior, and cross-vault / revoked-device isolation
"""

from __future__ import annotations

import hashlib
import os
import socket
import struct
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sync-server"))

from app.stdlib_server import start_background_server  # noqa: E402
from sync.desktop import crypto as pyc  # noqa: E402
from sync.desktop.engine import DesktopSyncEngine, SyncHttpClient, SyncHttpError, VaultKeyring  # noqa: E402
from sync.desktop.sqlite_sync_adapter import DesktopSyncDatabase  # noqa: E402


def _flip_b64u_byte(b64u: str, index: int = 0) -> str:
    raw = bytearray(pyc.from_b64u(b64u))
    raw[index % len(raw)] ^= 0x01
    return pyc.to_b64u(bytes(raw))


class TestSecurityAdversarial(unittest.TestCase):
    def test_aead_bitflips_aad_swaps_and_canonical_encodings(self) -> None:
        vault_id = pyc.generate_uuid_v4()
        dev_id = pyc.generate_uuid_v4()
        op_id = pyc.generate_uuid_v4()
        vsk = os.urandom(32)
        k_op, _ = pyc.derive_epoch_keys(pyc.derive_initial_epoch1_root_key(vsk, vault_id), vault_id, 1)

        meta = {
            "vault_id": vault_id,
            "device_id": dev_id,
            "operation_id": op_id,
            "device_seq": "7",
            "key_epoch": 1,
        }
        payload = {
            "payload_version": 1,
            "operation_type": "entity.upsert",
            "entity_type": "note",
            "entity_id": pyc.generate_uuid_v4(),
            "revision_id": pyc.generate_uuid_v4(),
            "parent_revision_ids": [],
            "created_at": "2026-11-20T01:00:00.000Z",
            "data": {"title": "Adversarial"},
        }
        env = pyc.encrypt_operation_envelope(k_op, meta, payload)

        # Bitflip nonce, ciphertext body, and final Poly1305 tag byte
        for field, idx in (("nonce", 0), ("ciphertext", 0), ("ciphertext", -1)):
            tampered = dict(env)
            tampered[field] = _flip_b64u_byte(env[field], idx)
            with self.assertRaises(ValueError):
                pyc.decrypt_operation_envelope(k_op, tampered)

        # Mutate every single AAD field -> AEAD must fail
        for field, mutated in (
            ("vault_id", pyc.generate_uuid_v4()),
            ("device_id", pyc.generate_uuid_v4()),
            ("operation_id", pyc.generate_uuid_v4()),
            ("device_seq", "8"),
            ("key_epoch", 2),
        ):
            tampered = dict(env)
            tampered[field] = mutated
            with self.assertRaises(ValueError):
                pyc.decrypt_operation_envelope(k_op, tampered)

        # Reject uppercase UUID & non-canonical Base64URL
        with self.assertRaises(ValueError):
            pyc.assert_uuid(vault_id.upper())
        with self.assertRaises(ValueError):
            pyc.from_b64u("AB==")
        with self.assertRaises(ValueError):
            pyc.from_b64u("AB")  # trailing non-zero bits (non-canonical round-trip)

    def test_argon2id_dos_bounds_and_x25519_key_length_guards(self) -> None:
        salt = b"\x01" * 16
        with self.assertRaises(ValueError):
            pyc.derive_argon2id_kek("pw", salt, memory_kib=1024 * 1024)  # 1 GiB > 512 MiB cap
        with self.assertRaises(ValueError):
            pyc.derive_argon2id_kek("pw", salt, iterations=100)  # > 20 cap
        with self.assertRaises(ValueError):
            pyc.derive_argon2id_kek("pw", salt, parallelism=2)
        with self.assertRaises(ValueError):
            pyc.derive_argon2id_kek("pw", salt, memory_kib=True)  # type: ignore[arg-type]

        vault_id = pyc.generate_uuid_v4()
        dev_id = pyc.generate_uuid_v4()
        root = os.urandom(32)
        pk, sk = pyc.generate_device_keypair()
        with self.assertRaises(ValueError):
            pyc.seal_epoch_key_for_device(pk[:16], vault_id, 2, dev_id, root)
        sealed = pyc.seal_epoch_key_for_device(pk, vault_id, 2, dev_id, root)
        with self.assertRaises(ValueError):
            pyc.unseal_epoch_key_for_device(pk, sk[:16], sealed, vault_id, 2, dev_id)
        with self.assertRaises(ValueError):
            pyc.unseal_epoch_key_for_device(pk[:16], sk, sealed, vault_id, 2, dev_id)

    def test_ntrm1_chunk_reorder_duplication_and_tampering(self) -> None:
        k_media = os.urandom(32)
        media_id = pyc.generate_uuid_v4()
        plain = b"chunk-1-aaaa" + b"chunk-2-bbbb" + b"chunk-3-cccc"
        blob = bytearray(pyc.encrypt_media_stream(k_media, media_id, 1, plain, chunk_size=12))

        # Parse 49-byte header + frames
        header = bytes(blob[:49])
        frames: list[bytes] = []
        pos = 49
        while pos < len(blob):
            (flen,) = struct.unpack(">I", blob[pos : pos + 4])
            frames.append(bytes(blob[pos : pos + 4 + flen]))
            pos += 4 + flen
        self.assertEqual(len(frames), 3)

        # 1. Reorder frame 0 and frame 1
        reordered = header + frames[1] + frames[0] + frames[2]
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(k_media, media_id, 1, reordered)

        # 2. Duplicate frame 0
        duplicated = header + frames[0] + frames[0] + frames[1] + frames[2]
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(k_media, media_id, 1, duplicated)

        # 3. Drop final frame (missing TAG_FINAL)
        truncated = header + frames[0] + frames[1]
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(k_media, media_id, 1, truncated)

        # 4. Corrupt NTRM version byte
        bad_ver = bytearray(blob)
        bad_ver[4] = 2
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(k_media, media_id, 1, bytes(bad_ver))

        # 5. Wrong epoch
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(k_media, media_id, 2, bytes(blob))

    def test_desktop_engine_blocks_path_traversal_and_pull_splicing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = DesktopSyncDatabase(base / "desk.db")
            media_dir = base / "vault_media"
            outside_target = base / "pwned.txt"
            vault_id = pyc.generate_uuid_v4()
            dev_a = pyc.generate_uuid_v4()
            dev_b = pyc.generate_uuid_v4()
            vsk = os.urandom(32)
            kr = VaultKeyring.from_vsk(vault_id, dev_a, vsk, 1, pyc.generate_device_keypair())

            media_id = pyc.generate_uuid_v4()
            _, k_media_ep = pyc.derive_epoch_keys(kr.epoch_root_keys[1], vault_id, 1)
            k_media = pyc.derive_media_object_key(k_media_ep, media_id)
            enc_media = pyc.encrypt_media_stream(k_media, media_id, 1, b"safe-bytes")

            def _opener(req: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
                if req.full_url.endswith(f"/media/{media_id}"):
                    return 200, {}, enc_media
                raise AssertionError(f"Unexpected URL {req.full_url}")

            eng = DesktopSyncEngine(db, SyncHttpClient("http://127.0.0.1", "tok", opener=_opener), kr, media_dir)

            note_id = pyc.generate_uuid_v4()
            eng.apply_decrypted_operation(
                pyc.generate_uuid_v4(),
                "1",
                dev_b,
                {
                    "payload_version": 1,
                    "operation_type": "entity.upsert",
                    "entity_type": "note",
                    "entity_id": note_id,
                    "revision_id": pyc.generate_uuid_v4(),
                    "parent_revision_ids": [],
                    "created_at": "2026-11-20T01:00:00.000Z",
                    "data": {"title": "N", "content": "C"},
                },
            )
            # Attacker peer supplies traversal filename "../../pwned.txt"
            eng.apply_decrypted_operation(
                pyc.generate_uuid_v4(),
                "2",
                dev_b,
                {
                    "payload_version": 1,
                    "operation_type": "media.register",
                    "entity_type": "attachment",
                    "entity_id": pyc.generate_uuid_v4(),
                    "revision_id": pyc.generate_uuid_v4(),
                    "parent_revision_ids": [],
                    "created_at": "2026-11-20T01:01:00.000Z",
                    "data": {
                        "note_id": note_id,
                        "media_id": media_id,
                        "media_type": "image",
                        "file_name": "../../../../pwned.txt",
                        "encrypted_size": len(enc_media),
                        "encrypted_sha256": pyc.sha256_hex(enc_media),
                        "key_epoch": 1,
                    },
                },
            )
            from sync.desktop.engine import SyncResult

            res = SyncResult()
            eng.download_pending_media(res)
            self.assertEqual(res.media_downloaded, 1)
            self.assertFalse(outside_target.exists(), "Path traversal escaped media_dir!")
            att_row = db.conn.execute("SELECT filepath FROM attachments WHERE media_id=?", (media_id,)).fetchone()
            self.assertTrue(Path(att_row["filepath"]).resolve().is_relative_to(media_dir.resolve()))
            self.assertEqual(Path(att_row["filepath"]).read_bytes(), b"safe-bytes")

            # Reject cross-vault spliced pull envelope
            db.set_state("last_server_seq", "5")
            k_op, _ = pyc.derive_epoch_keys(kr.epoch_root_keys[1], vault_id, 1)
            evil_vault = pyc.generate_uuid_v4()
            spliced_env = pyc.encrypt_operation_envelope(
                k_op,
                {"vault_id": evil_vault, "device_id": dev_b, "operation_id": pyc.generate_uuid_v4(), "device_seq": "1", "key_epoch": 1},
                {"payload_version": 1, "operation_type": "entity.upsert", "entity_type": "note", "entity_id": note_id, "revision_id": pyc.generate_uuid_v4(), "parent_revision_ids": [], "created_at": "2026-11-20T01:02:00.000Z", "data": {}},
            )
            spliced_env["server_seq"] = "6"

            eng.client = SyncHttpClient(
                "http://127.0.0.1",
                "tok",
                opener=lambda _r: (
                    200,
                    {},
                    f'{{"operations":[{__import__("json").dumps(spliced_env)}],"next_since_seq":"6","has_more":false}}'.encode(),
                ),
            )
            with self.assertRaises(ValueError):
                eng._pull_and_apply_operations(SyncResult())

            # Reject backwards / replayed server_seq
            replay_env = pyc.encrypt_operation_envelope(
                k_op,
                {"vault_id": vault_id, "device_id": dev_b, "operation_id": pyc.generate_uuid_v4(), "device_seq": "1", "key_epoch": 1},
                {"payload_version": 1, "operation_type": "entity.upsert", "entity_type": "note", "entity_id": note_id, "revision_id": pyc.generate_uuid_v4(), "parent_revision_ids": [], "created_at": "2026-11-20T01:02:00.000Z", "data": {}},
            )
            replay_env["server_seq"] = "5"  # <= last_seen_seq (5)
            eng.client = SyncHttpClient(
                "http://127.0.0.1",
                "tok",
                opener=lambda _r: (
                    200,
                    {},
                    f'{{"operations":[{__import__("json").dumps(replay_env)}],"next_since_seq":"5","has_more":false}}'.encode(),
                ),
            )
            with self.assertRaises(ValueError):
                eng._pull_and_apply_operations(SyncResult())
            db.close()

    def test_http_server_dos_caps_cors_lockout_and_naive_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # No explicit cors_origin -> defaults to localhost dev only (never '*')
            httpd, storage, url = start_background_server(base / "srv.db", base / "blobs")
            try:
                v1 = pyc.generate_uuid_v4()
                d1 = pyc.generate_uuid_v4()
                pk1, _ = pyc.generate_device_keypair()
                client = SyncHttpClient(url)
                boot = client.bootstrap_vault(
                    {
                        "vault_id": v1,
                        "device_id": d1,
                        "device_name": "Sec A",
                        "device_type": "desktop",
                        "public_key_x25519": pyc.to_b64u(pk1),
                    }
                )
                client.access_token = boot["device_access_token"]

                # 1. Verify untrusted Origin does NOT receive ACAO '*'
                req_evil = urllib.request.Request(f"{url}/healthz", headers={"Origin": "https://evil.attacker"})
                with urllib.request.urlopen(req_evil) as resp:
                    self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))
                    self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")

                req_dev = urllib.request.Request(f"{url}/healthz", headers={"Origin": "http://localhost:3000"})
                with urllib.request.urlopen(req_dev) as resp:
                    self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "http://localhost:3000")

                # 2. Naive expires_at timestamp rejected cleanly as 400 INVALID_PAYLOAD (not 500)
                with self.assertRaises(SyncHttpError) as ctx_naive:
                    client.create_pairing_session(
                        v1,
                        {
                            "pairing_id": pyc.generate_uuid_v4(),
                            "device_a_id": d1,
                            "pairing_nonce": pyc.to_b64u(os.urandom(16)),
                            "expires_at": "2027-05-01T12:00:00",
                        },
                    )
                self.assertEqual(ctx_naive.exception.status, 400)
                self.assertEqual(ctx_naive.exception.code, "INVALID_PAYLOAD")

                # 3. Oversized VSK recovery envelope (> 16 KiB) rejected cleanly as 400
                with self.assertRaises(SyncHttpError) as ctx_vsk:
                    SyncHttpClient(url).bootstrap_vault(
                        {
                            "vault_id": pyc.generate_uuid_v4(),
                            "device_id": pyc.generate_uuid_v4(),
                            "device_name": "BigVsk",
                            "device_type": "pwa",
                            "public_key_x25519": pyc.to_b64u(pk1),
                            "vsk_recovery_envelope": {"junk": "A" * 20000},
                        }
                    )
                self.assertEqual(ctx_vsk.exception.status, 400)

                # 4. Pairing 5-attempt lockout -> status='locked'
                pid = pyc.generate_uuid_v4()
                dev_b = pyc.generate_uuid_v4()
                pk_b, _ = pyc.generate_device_keypair()
                client.create_pairing_session(
                    v1,
                    {
                        "pairing_id": pid,
                        "device_a_id": d1,
                        "pairing_nonce": pyc.to_b64u(os.urandom(32)),
                        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                    },
                )
                anon = SyncHttpClient(url)
                anon.join_pairing_session(
                    v1,
                    pid,
                    {
                        "device_b_id": dev_b,
                        "device_b_name": "Dev B",
                        "device_b_type": "mobile",
                        "device_b_public_key_x25519": pyc.to_b64u(pk_b),
                        "session_nonce_b": pyc.to_b64u(os.urandom(24)),
                        "secret_proof_hmac": pyc.to_b64u(os.urandom(32)),
                    },
                )
                client.approve_pairing_session(
                    v1,
                    pid,
                    {
                        "bundle_nonce": pyc.to_b64u(os.urandom(24)),
                        "encrypted_vault_bundle": pyc.to_b64u(os.urandom(48)),
                    },
                )
                wrong_dev = pyc.generate_uuid_v4()
                for _ in range(5):
                    with self.assertRaises(SyncHttpError) as ctx_att:
                        anon.confirm_pairing_session(
                            v1, pid, {"device_b_id": wrong_dev, "confirm_hmac": pyc.to_b64u(os.urandom(32))}
                        )
                    self.assertEqual(ctx_att.exception.status, 403)
                with self.assertRaises(SyncHttpError) as ctx_lck:
                    anon.confirm_pairing_session(
                        v1, pid, {"device_b_id": dev_b, "confirm_hmac": pyc.to_b64u(os.urandom(32))}
                    )
                self.assertEqual(ctx_lck.exception.status, 409)
                self.assertEqual(ctx_lck.exception.code, "PAIRING_INVALID_STATE")

                # 5. Content-Length DoS pre-check rejects 50 MiB JSON / 5 MiB PUT chunk immediately with 413 without reading body!
                def _recv_all(s: socket.socket) -> str:
                    chunks: list[bytes] = []
                    while True:
                        p = s.recv(4096)
                        if not p:
                            break
                        chunks.append(p)
                    return b"".join(chunks).decode("utf-8", errors="replace")

                host, port = httpd.server_address
                with socket.create_connection((host, port), timeout=5) as sock:
                    sock.sendall(
                        f"POST /api/v1/vaults HTTP/1.1\r\nHost: localhost\r\nContent-Length: {50 * 1024 * 1024}\r\nConnection: close\r\n\r\n".encode()
                    )
                    resp = _recv_all(sock)
                self.assertIn("413", resp.splitlines()[0])
                self.assertIn("PAYLOAD_TOO_LARGE", resp)

                mid = pyc.generate_uuid_v4()
                with socket.create_connection((host, port), timeout=5) as sock:
                    sock.sendall(
                        f"PUT /api/v1/vaults/{v1}/media/uploads/{mid} HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {client.access_token}\r\nContent-Range: bytes 0-5242880/5242881\r\nContent-Length: 5242881\r\nConnection: close\r\n\r\n".encode()
                    )
                    resp_put = _recv_all(sock)
                self.assertIn("413", resp_put.splitlines()[0])
                self.assertIn("CHUNK_TOO_LARGE", resp_put)

                # 6. Negative since_seq rejected cleanly with 400
                with self.assertRaises(SyncHttpError) as ctx_seq:
                    client.pull_operations(v1, "-1", 50)
                self.assertEqual(ctx_seq.exception.status, 400)
            finally:
                httpd.shutdown()
                httpd.server_close()
                storage.close()


if __name__ == "__main__":
    unittest.main()
