"""Deterministic generator for Noteor Sync v1.1 cross-language crypto test vectors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sync.desktop import crypto as pyc  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent


def write_pair(subdir: str, inputs: dict, expected: dict) -> None:
    d = OUT_DIR / subdir
    d.mkdir(parents=True, exist_ok=True)
    (d / "inputs.json").write_text(json.dumps(inputs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    vault_id = "550e8400-e29b-41d4-a716-446655440000"
    device_a_id = "550e8400-e29b-41d4-a716-446655440001"
    device_b_id = "550e8400-e29b-41d4-a716-446655440009"
    operation_id = "550e8400-e29b-41d4-a716-446655440002"
    entity_id = "550e8400-e29b-41d4-a716-446655440010"
    revision_id = "550e8400-e29b-41d4-a716-446655440011"
    media_id = "550e8400-e29b-41d4-a716-446655440022"
    pairing_id = "550e8400-e29b-41d4-a716-446655440030"

    # 1. Argon2id (Section 13.3 + Unicode UTF-8 test)
    salt1 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    pw1 = "Noteor test password"
    kek1 = pyc.derive_argon2id_kek(pw1, salt1)
    pw_unicode = "Café-résumé🔐"
    kek_unicode = pyc.derive_argon2id_kek(pw_unicode, salt1)
    write_pair(
        "argon2id",
        {
            "cases": [
                {
                    "id": "spec_13_3",
                    "password_utf8": pw1,
                    "salt_hex": salt1.hex(),
                    "algorithm": "argon2id13",
                    "memory_kib": 65536,
                    "iterations": 3,
                    "parallelism": 1,
                    "output_length": 32,
                },
                {
                    "id": "unicode_utf8_no_norm",
                    "password_utf8": pw_unicode,
                    "salt_hex": salt1.hex(),
                    "algorithm": "argon2id13",
                    "memory_kib": 65536,
                    "iterations": 3,
                    "parallelism": 1,
                    "output_length": 32,
                },
            ]
        },
        {
            "spec_13_3_hex": kek1.hex(),
            "unicode_utf8_no_norm_hex": kek_unicode.hex(),
        },
    )

    # 2. HKDF-SHA-256 (RFC 5869 A.1 + Noteor hierarchy)
    rfc_ikm = bytes.fromhex("0b" * 22)
    rfc_salt = bytes.fromhex("000102030405060708090a0b0c")
    rfc_info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    rfc_okm = pyc.hkdf_sha256(rfc_ikm, rfc_salt, rfc_info, 42)
    assert rfc_okm.hex() == "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"

    vsk = bytes.fromhex("10" * 32)
    epoch1_root = pyc.derive_initial_epoch1_root_key(vsk, vault_id)
    k_op, k_media_epoch = pyc.derive_epoch_keys(epoch1_root, vault_id, 1)
    k_media = pyc.derive_media_object_key(k_media_epoch, media_id)
    write_pair(
        "hkdf",
        {
            "rfc5869_a1": {
                "ikm_hex": rfc_ikm.hex(),
                "salt_hex": rfc_salt.hex(),
                "info_hex": rfc_info.hex(),
                "length": 42,
            },
            "noteor_v1": {
                "vsk_hex": vsk.hex(),
                "vault_id": vault_id,
                "key_epoch": 1,
                "media_id": media_id,
            },
        },
        {
            "rfc5869_a1_okm_hex": rfc_okm.hex(),
            "epoch1_root_hex": epoch1_root.hex(),
            "k_op_hex": k_op.hex(),
            "k_media_epoch_hex": k_media_epoch.hex(),
            "k_media_hex": k_media.hex(),
        },
    )

    # 3. XChaCha20-Poly1305 Operation Envelope + AAD RFC 8785 + VSK Envelope
    op_nonce = bytes.fromhex("20" * 24)
    payload = {
        "payload_version": 1,
        "operation_type": "entity.upsert",
        "entity_type": "note",
        "entity_id": entity_id,
        "revision_id": revision_id,
        "parent_revision_ids": [],
        "created_at": "2026-11-19T12:41:00.000Z",
        "data": {
            "title": "Note test interop",
            "content": "Bonjour Noteor [[Cible|@" + media_id + "]]",
            "category_id": None,
            "folder_id": None,
            "is_deleted": False,
            "deleted_at": None,
            "created_at": "2026-11-19T12:41:00.000Z",
            "updated_at": "2026-11-19T12:41:00.000Z",
            "metadata": {},
        },
    }
    meta = {
        "protocol_version": 1,
        "crypto_version": 1,
        "key_epoch": 1,
        "vault_id": vault_id,
        "device_id": device_a_id,
        "operation_id": operation_id,
        "device_seq": "184",
    }
    aad_bytes = pyc.build_operation_aad(meta)
    envelope = pyc.encrypt_operation_envelope(k_op, meta, payload, nonce_override=op_nonce)
    vsk_env = pyc.encrypt_vsk_envelope(
        vsk,
        vault_id,
        pw1,
        salt_override=salt1,
        nonce_override=bytes.fromhex("30" * 24),
    )
    write_pair(
        "xchacha20poly1305",
        {
            "k_op_hex": k_op.hex(),
            "meta": meta,
            "nonce_hex": op_nonce.hex(),
            "payload": payload,
            "vsk_envelope_password": pw1,
            "vault_id": vault_id,
        },
        {
            "aad_utf8": aad_bytes.decode("utf-8"),
            "envelope": envelope,
            "vsk_envelope": vsk_env,
            "expected_vsk_hex": vsk.hex(),
        },
    )

    # 4. NTRM1 Binary Secretstream (multi-chunk plaintext)
    media_plaintext = b"NOTEOR_AUDIO_HEADER_" + (bytes(range(256)) * 20) + b"_END"
    media_blob = pyc.encrypt_media_stream(k_media, media_id, 1, media_plaintext, chunk_size=1024)
    write_pair(
        "secretstream",
        {
            "k_media_hex": k_media.hex(),
            "media_id": media_id,
            "key_epoch": 1,
            "chunk_size": 1024,
            "plaintext_b64u": pyc.to_b64u(media_plaintext),
        },
        {
            "encrypted_blob_b64u": pyc.to_b64u(media_blob),
            "encrypted_size": len(media_blob),
            "encrypted_sha256": pyc.sha256_hex(media_blob),
        },
    )

    # 5. Pairing & Epoch Key Wrapping (Epoch 2 rotation)
    pairing_secret = bytes.fromhex("44" * 32)
    pairing_nonce = bytes.fromhex("55" * 32)
    session_nonce_b = pyc.to_b64u(bytes.fromhex("66" * 24))
    ctx = {
        "vault_id": vault_id,
        "pairing_id": pairing_id,
        "device_a_id": device_a_id,
        "device_b_id": device_b_id,
        "session_nonce_b": session_nonce_b,
    }
    secret_proof = pyc.compute_secret_proof_hmac(pairing_secret, vault_id, pairing_id, device_b_id, session_nonce_b)
    k_pair = pyc.derive_pairing_key(
        pairing_secret, pairing_nonce, vault_id, pairing_id, device_a_id, device_b_id, session_nonce_b
    )
    bundle = {
        "vault_id": vault_id,
        "vsk_b64u": pyc.to_b64u(vsk),
        "current_key_epoch": 1,
        "epoch_keys": {"1": pyc.to_b64u(epoch1_root)},
    }
    sealed_pair = pyc.encrypt_pairing_bundle(k_pair, ctx, bundle, nonce_override=bytes.fromhex("77" * 24))
    confirm_hmac = pyc.compute_pairing_confirm_hmac(k_pair, pairing_id, device_b_id, vault_id)

    pk_b, sk_b = pyc.generate_device_keypair()
    epoch2_root = bytes.fromhex("88" * 32)
    sealed_epoch2_for_b = pyc.seal_epoch_key_for_device(pk_b, vault_id, 2, device_b_id, epoch2_root)
    write_pair(
        "pairing",
        {
            "pairing_secret_hex": pairing_secret.hex(),
            "pairing_nonce_hex": pairing_nonce.hex(),
            "context": ctx,
            "bundle_nonce_hex": "77" * 24,
            "bundle": bundle,
            "device_b_pub_b64u": pyc.to_b64u(pk_b),
            "device_b_priv_b64u": pyc.to_b64u(sk_b),
            "epoch2_root_hex": epoch2_root.hex(),
        },
        {
            "secret_proof_hmac": secret_proof,
            "k_pair_hex": k_pair.hex(),
            "encrypted_bundle": sealed_pair,
            "confirm_hmac": confirm_hmac,
            "sealed_epoch2_for_b": sealed_epoch2_for_b,
        },
    )

    # 6. RFC 8785 JCS edge-case vectors (UTF-16 ordering, integer-valued floats, exponents)
    jcs_payload = {
        "\uffff": 1,
        "\U0001f680": 2,
        "b": 3,
        "a": 4,
        "é": 5,
        "nums": [0, 1.0, -1.0, 3.14, 1e-6, 9e-7, 1e-7, 1e20, 1e21, -1e21],
        "nested": {"z": True, "a": None, "m": False},
    }
    write_pair(
        "jcs",
        {"payload": jcs_payload},
        {"canonical_utf8": pyc.canonical_json_bytes(jcs_payload).decode("utf-8")},
    )

    # 7. Malformed & negative cases
    bad_aad_env = {**envelope, "device_seq": "185"}
    bad_epoch_env = {**envelope, "key_epoch": 2}
    ct_bytes = bytearray(pyc.from_b64u(envelope["ciphertext"]))
    ct_bytes[-1] ^= 0xFF
    tampered_ct_env = {**envelope, "ciphertext": pyc.to_b64u(bytes(ct_bytes))}
    bad_ntrm_magic = b"XXXX" + media_blob[4:]
    write_pair(
        "malformed",
        {
            "padded_base64url": envelope["nonce"] + "=",
            "bad_aad_envelope": bad_aad_env,
            "bad_epoch_envelope": bad_epoch_env,
            "tampered_tag_envelope": tampered_ct_env,
            "truncated_media_b64u": pyc.to_b64u(media_blob[:-5]),
            "trailing_media_b64u": pyc.to_b64u(media_blob + b"\x00\x00\x00\x01x"),
            "bad_ntrm_magic_b64u": pyc.to_b64u(bad_ntrm_magic),
            "wrong_media_id": "550e8400-e29b-41d4-a716-446655440099",
            "wrong_vsk_password": "Wrong password!",
            "wrong_wrap_device_id": "550e8400-e29b-41d4-a716-4466554400fe",
        },
        {
            "must_fail_all": True,
        },
    )
    print("Generated all crypto test vectors in", OUT_DIR)


if __name__ == "__main__":
    main()

