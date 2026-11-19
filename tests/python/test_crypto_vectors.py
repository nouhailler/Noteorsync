"""Python unittest suite validating Noteor Sync v1.1 crypto vectors + TS↔Python interop."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sync.desktop import crypto as pyc  # noqa: E402

VECTORS = ROOT / "tests" / "crypto-vectors"


def read_json(subpath: str) -> dict:
    return json.loads((VECTORS / subpath).read_text(encoding="utf-8"))


class TestCryptoVectors(unittest.TestCase):
    def test_argon2id_vectors(self) -> None:
        inputs = read_json("argon2id/inputs.json")
        expected = read_json("argon2id/expected.json")
        for c in inputs["cases"]:
            kek = pyc.derive_argon2id_kek(
                c["password_utf8"],
                bytes.fromhex(c["salt_hex"]),
                memory_kib=c["memory_kib"],
                iterations=c["iterations"],
                parallelism=c["parallelism"],
            )
            self.assertEqual(kek.hex(), expected[f"{c['id']}_hex"])

    def test_hkdf_and_hierarchy(self) -> None:
        inputs = read_json("hkdf/inputs.json")
        expected = read_json("hkdf/expected.json")
        rfc = inputs["rfc5869_a1"]
        okm = pyc.hkdf_sha256(
            bytes.fromhex(rfc["ikm_hex"]),
            bytes.fromhex(rfc["salt_hex"]),
            bytes.fromhex(rfc["info_hex"]),
            rfc["length"],
        )
        self.assertEqual(okm.hex(), expected["rfc5869_a1_okm_hex"])

        nv1 = inputs["noteor_v1"]
        epoch1 = pyc.derive_initial_epoch1_root_key(bytes.fromhex(nv1["vsk_hex"]), nv1["vault_id"])
        self.assertEqual(epoch1.hex(), expected["epoch1_root_hex"])
        k_op, k_media_epoch = pyc.derive_epoch_keys(epoch1, nv1["vault_id"], nv1["key_epoch"])
        self.assertEqual(k_op.hex(), expected["k_op_hex"])
        self.assertEqual(k_media_epoch.hex(), expected["k_media_epoch_hex"])
        k_media = pyc.derive_media_object_key(k_media_epoch, nv1["media_id"])
        self.assertEqual(k_media.hex(), expected["k_media_hex"])

    def test_xchacha20poly1305_and_vsk_envelope(self) -> None:
        inputs = read_json("xchacha20poly1305/inputs.json")
        expected = read_json("xchacha20poly1305/expected.json")
        k_op = bytes.fromhex(inputs["k_op_hex"])
        dec = pyc.decrypt_operation_envelope(k_op, expected["envelope"])
        self.assertEqual(dec, inputs["payload"])
        vsk = pyc.decrypt_vsk_envelope(
            expected["vsk_envelope"], inputs["vault_id"], inputs["vsk_envelope_password"]
        )
        self.assertEqual(vsk.hex(), expected["expected_vsk_hex"])

    def test_secretstream_ntrm1(self) -> None:
        inputs = read_json("secretstream/inputs.json")
        expected = read_json("secretstream/expected.json")
        k_media = bytes.fromhex(inputs["k_media_hex"])
        blob = pyc.from_b64u(expected["encrypted_blob_b64u"])
        plain = pyc.decrypt_media_stream(k_media, inputs["media_id"], inputs["key_epoch"], blob)
        self.assertEqual(pyc.to_b64u(plain), inputs["plaintext_b64u"])

        empty_blob = pyc.encrypt_media_stream(k_media, inputs["media_id"], inputs["key_epoch"], b"")
        self.assertEqual(
            pyc.decrypt_media_stream(k_media, inputs["media_id"], inputs["key_epoch"], empty_blob),
            b"",
        )

    def test_pairing_and_epoch_wrapping(self) -> None:
        inputs = read_json("pairing/inputs.json")
        expected = read_json("pairing/expected.json")
        k_pair = pyc.derive_pairing_key(
            bytes.fromhex(inputs["pairing_secret_hex"]),
            bytes.fromhex(inputs["pairing_nonce_hex"]),
            **inputs["context"],
        )
        self.assertEqual(k_pair.hex(), expected["k_pair_hex"])
        bundle = pyc.decrypt_pairing_bundle(
            k_pair,
            inputs["context"],
            expected["encrypted_bundle"]["nonce"],
            expected["encrypted_bundle"]["ciphertext"],
        )
        self.assertEqual(bundle, inputs["bundle"])

        epoch2 = pyc.unseal_epoch_key_for_device(
            pyc.from_b64u(inputs["device_b_pub_b64u"]),
            pyc.from_b64u(inputs["device_b_priv_b64u"]),
            expected["sealed_epoch2_for_b"],
            inputs["context"]["vault_id"],
            2,
            inputs["context"]["device_b_id"],
        )
        self.assertEqual(epoch2.hex(), inputs["epoch2_root_hex"])

    def test_jcs_edge_cases(self) -> None:
        inputs = read_json("jcs/inputs.json")
        expected = read_json("jcs/expected.json")
        self.assertEqual(
            pyc.canonical_json_bytes(inputs["payload"]).decode("utf-8"),
            expected["canonical_utf8"],
        )

    def test_malformed_vectors_rejected(self) -> None:
        hkdf_exp = read_json("hkdf/expected.json")
        xch_exp = read_json("xchacha20poly1305/expected.json")
        xch_in = read_json("xchacha20poly1305/inputs.json")
        pairing_in = read_json("pairing/inputs.json")
        pairing_exp = read_json("pairing/expected.json")
        bad = read_json("malformed/inputs.json")
        ss_in = read_json("secretstream/inputs.json")
        ss_exp = read_json("secretstream/expected.json")
        k_op = bytes.fromhex(hkdf_exp["k_op_hex"])
        k_media = bytes.fromhex(hkdf_exp["k_media_hex"])

        with self.assertRaises(ValueError):
            pyc.from_b64u(bad["padded_base64url"])
        with self.assertRaises(ValueError):
            pyc.decrypt_operation_envelope(k_op, bad["bad_aad_envelope"])
        with self.assertRaises(ValueError):
            pyc.decrypt_operation_envelope(k_op, bad["bad_epoch_envelope"])
        with self.assertRaises(ValueError):
            pyc.decrypt_operation_envelope(k_op, bad["tampered_tag_envelope"])
        with self.assertRaises(ValueError):
            pyc.decrypt_vsk_envelope(xch_exp["vsk_envelope"], xch_in["vault_id"], bad["wrong_vsk_password"])
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(
                k_media, ss_in["media_id"], ss_in["key_epoch"], pyc.from_b64u(bad["truncated_media_b64u"])
            )
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(
                k_media, ss_in["media_id"], ss_in["key_epoch"], pyc.from_b64u(bad["trailing_media_b64u"])
            )
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(
                k_media, ss_in["media_id"], ss_in["key_epoch"], pyc.from_b64u(bad["bad_ntrm_magic_b64u"])
            )
        with self.assertRaises(ValueError):
            pyc.decrypt_media_stream(
                k_media, bad["wrong_media_id"], ss_in["key_epoch"], pyc.from_b64u(ss_exp["encrypted_blob_b64u"])
            )
        with self.assertRaises(ValueError):
            pyc.unseal_epoch_key_for_device(
                pyc.from_b64u(pairing_in["device_b_pub_b64u"]),
                pyc.from_b64u(pairing_in["device_b_priv_b64u"]),
                pairing_exp["sealed_epoch2_for_b"],
                pairing_in["context"]["vault_id"],
                2,
                bad["wrong_wrap_device_id"],
            )

    def test_bidirectional_ts_generated_ciphertexts_decrypt_in_python(self) -> None:
        """Encrypt fresh randomized artifacts in TS (Node ESM + libsodium-wrappers-sumo) and decrypt in Python."""
        hkdf_in = read_json("hkdf/inputs.json")["noteor_v1"]
        hkdf_exp = read_json("hkdf/expected.json")
        pairing_in = read_json("pairing/inputs.json")
        media_id = hkdf_in["media_id"]

        js_script = """
import {
  ensureCryptoReady,
  encryptOperationEnvelope,
  encryptMediaStream,
  sealEpochKeyForDevice,
  hexToBytes,
  fromBase64Url,
  toBase64Url,
  utf8ToBytes,
} from './web/src/sync/crypto.ts';

await ensureCryptoReady();
const kOp = hexToBytes(process.env.K_OP_HEX);
const kMedia = hexToBytes(process.env.K_MEDIA_HEX);
const pkB = fromBase64Url(process.env.PK_B_B64U);
const epoch2 = hexToBytes(process.env.EPOCH2_HEX);

const payload = {
  payload_version: 1,
  operation_type: 'entity.upsert',
  entity_type: 'note',
  entity_id: '550e8400-e29b-41d4-a716-446655440055',
  revision_id: '550e8400-e29b-41d4-a716-446655440056',
  parent_revision_ids: ['550e8400-e29b-41d4-a716-446655440011'],
  created_at: '2026-11-19T14:00:00.000Z',
  data: {
    title: 'TS → Python',
    content: 'Accent éàü 🚀 [[Lien|@550e8400-e29b-41d4-a716-446655440010]]',
    category_id: null,
    folder_id: null,
    is_deleted: false,
    deleted_at: null,
    created_at: '2026-11-19T14:00:00.000Z',
    updated_at: '2026-11-19T14:00:00.000Z',
    metadata: {},
  },
};
const env = await encryptOperationEnvelope(kOp, {
  vault_id: process.env.VAULT_ID,
  device_id: process.env.DEV_B,
  operation_id: '550e8400-e29b-41d4-a716-446655440077',
  device_seq: '9',
  key_epoch: 1,
}, payload);

const mediaPlain = utf8ToBytes('Stream from TypeScript PWA to Desktop Python!');
const mediaBlob = await encryptMediaStream(kMedia, process.env.MEDIA_ID, 1, mediaPlain, 16);

const sealedEpoch2 = await sealEpochKeyForDevice(pkB, {
  vault_id: process.env.VAULT_ID,
  key_epoch: 2,
  target_device_id: process.env.DEV_B,
  epoch_root_key: epoch2,
});

console.log(JSON.stringify({
  payload,
  env,
  mediaPlainB64u: toBase64Url(mediaPlain),
  mediaBlobB64u: toBase64Url(mediaBlob),
  sealedEpoch2,
}));
"""
        env_vars = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "K_OP_HEX": hkdf_exp["k_op_hex"],
            "K_MEDIA_HEX": hkdf_exp["k_media_hex"],
            "PK_B_B64U": pairing_in["device_b_pub_b64u"],
            "EPOCH2_HEX": pairing_in["epoch2_root_hex"],
            "VAULT_ID": hkdf_in["vault_id"],
            "DEV_B": pairing_in["context"]["device_b_id"],
            "MEDIA_ID": media_id,
        }
        proc = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", js_script],
            cwd=str(ROOT),
            env=env_vars,
            capture_output=True,
            text=True,
            check=True,
        )
        res = json.loads(proc.stdout.strip())
        dec_payload = pyc.decrypt_operation_envelope(bytes.fromhex(hkdf_exp["k_op_hex"]), res["env"])
        self.assertEqual(dec_payload, res["payload"])

        dec_media = pyc.decrypt_media_stream(
            bytes.fromhex(hkdf_exp["k_media_hex"]),
            media_id,
            1,
            pyc.from_b64u(res["mediaBlobB64u"]),
        )
        self.assertEqual(pyc.to_b64u(dec_media), res["mediaPlainB64u"])

        dec_epoch2 = pyc.unseal_epoch_key_for_device(
            pyc.from_b64u(pairing_in["device_b_pub_b64u"]),
            pyc.from_b64u(pairing_in["device_b_priv_b64u"]),
            res["sealedEpoch2"],
            hkdf_in["vault_id"],
            2,
            pairing_in["context"]["device_b_id"],
        )
        self.assertEqual(dec_epoch2.hex(), pairing_in["epoch2_root_hex"])


if __name__ == "__main__":
    unittest.main()
