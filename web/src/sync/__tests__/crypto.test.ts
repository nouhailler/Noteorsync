import { describe, it, expect, beforeAll } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {
  ensureCryptoReady,
  deriveArgon2idKek,
  hkdfSha256,
  deriveInitialEpoch1RootKey,
  deriveEpochKeys,
  deriveMediaObjectKey,
  canonicalJsonBytes,
  buildOperationAad,
  encryptOperationEnvelope,
  decryptOperationEnvelope,
  encryptVskEnvelope,
  decryptVskEnvelope,
  encryptMediaStream,
  decryptMediaStream,
  computeSecretProofHmac,
  derivePairingKey,
  encryptPairingBundle,
  decryptPairingBundle,
  computePairingConfirmHmac,
  sealEpochKeyForDevice,
  unsealEpochKeyForDevice,
  fromBase64Url,
  toBase64Url,
  hexToBytes,
  bytesToHex,
  sha256Hex,
  bytesToUtf8,
  type OperationEnvelope,
  type VskRecoveryEnvelope,
  type PairingBundlePayload,
} from '../crypto';

const VECTORS_DIR = path.resolve(process.cwd(), 'tests/crypto-vectors');

function readJson<T>(subpath: string): T {
  const full = path.join(VECTORS_DIR, subpath);
  return JSON.parse(fs.readFileSync(full, 'utf-8')) as T;
}

describe('Noteor Sync v1.1 Cross-Language Crypto Vectors (TS ↔ Python)', () => {
  beforeAll(async () => {
    await ensureCryptoReady();
  });

  it('matches Python Argon2id13 vectors (Section 13.3 + UTF-8 Unicode)', async () => {
    const inputs = readJson<{
      cases: Array<{
        id: string;
        password_utf8: string;
        salt_hex: string;
        memory_kib: number;
        iterations: number;
        parallelism: number;
      }>;
    }>('argon2id/inputs.json');
    const expected = readJson<Record<string, string>>('argon2id/expected.json');

    for (const c of inputs.cases) {
      const kek = await deriveArgon2idKek(
        c.password_utf8,
        hexToBytes(c.salt_hex),
        c.memory_kib,
        c.iterations,
        c.parallelism,
      );
      expect(bytesToHex(kek)).toBe(expected[`${c.id}_hex`]);
    }
  });

  it('matches RFC 5869 A.1 and Noteor v1 HKDF key hierarchy', () => {
    const inputs = readJson<{
      rfc5869_a1: { ikm_hex: string; salt_hex: string; info_hex: string; length: number };
      noteor_v1: { vsk_hex: string; vault_id: string; key_epoch: number; media_id: string };
    }>('hkdf/inputs.json');
    const expected = readJson<{
      rfc5869_a1_okm_hex: string;
      epoch1_root_hex: string;
      k_op_hex: string;
      k_media_epoch_hex: string;
      k_media_hex: string;
    }>('hkdf/expected.json');

    const okm = hkdfSha256(
      hexToBytes(inputs.rfc5869_a1.ikm_hex),
      hexToBytes(inputs.rfc5869_a1.salt_hex),
      hexToBytes(inputs.rfc5869_a1.info_hex),
      inputs.rfc5869_a1.length,
    );
    expect(bytesToHex(okm)).toBe(expected.rfc5869_a1_okm_hex);

    const epoch1Root = deriveInitialEpoch1RootKey(
      hexToBytes(inputs.noteor_v1.vsk_hex),
      inputs.noteor_v1.vault_id,
    );
    expect(bytesToHex(epoch1Root)).toBe(expected.epoch1_root_hex);

    const { kOp, kMediaEpoch } = deriveEpochKeys(
      epoch1Root,
      inputs.noteor_v1.vault_id,
      inputs.noteor_v1.key_epoch,
    );
    expect(bytesToHex(kOp)).toBe(expected.k_op_hex);
    expect(bytesToHex(kMediaEpoch)).toBe(expected.k_media_epoch_hex);

    const kMedia = deriveMediaObjectKey(kMediaEpoch, inputs.noteor_v1.media_id);
    expect(bytesToHex(kMedia)).toBe(expected.k_media_hex);
  });

  it('encrypts/decrypts XChaCha20-Poly1305 envelopes and VSK recovery bit-identically with Python', async () => {
    const inputs = readJson<{
      k_op_hex: string;
      meta: {
        protocol_version: number;
        crypto_version: number;
        key_epoch: number;
        vault_id: string;
        device_id: string;
        operation_id: string;
        device_seq: string;
      };
      nonce_hex: string;
      payload: any;
      vsk_envelope_password: string;
      vault_id: string;
    }>('xchacha20poly1305/inputs.json');

    const expected = readJson<{
      aad_utf8: string;
      envelope: OperationEnvelope;
      vsk_envelope: VskRecoveryEnvelope;
      expected_vsk_hex: string;
    }>('xchacha20poly1305/expected.json');

    const kOp = hexToBytes(inputs.k_op_hex);
    const aad = buildOperationAad(inputs.meta);
    expect(bytesToUtf8(aad)).toBe(expected.aad_utf8);

    // Decrypt Python-produced operation envelope
    const decFromPy = await decryptOperationEnvelope(kOp, expected.envelope);
    expect(decFromPy).toEqual(inputs.payload);

    // Re-encrypt deterministically in TS and confirm identical ciphertext/nonce (assuming compact JSON order matches)
    const envFromTs = await encryptOperationEnvelope(
      kOp,
      inputs.meta,
      inputs.payload,
      hexToBytes(inputs.nonce_hex),
    );
    expect(envFromTs.nonce).toBe(expected.envelope.nonce);
    expect(envFromTs.ciphertext).toBe(expected.envelope.ciphertext);

    // Decrypt Python VSK envelope and produce matching VSK envelope
    const decVsk = await decryptVskEnvelope(
      expected.vsk_envelope,
      inputs.vault_id,
      inputs.vsk_envelope_password,
    );
    expect(bytesToHex(decVsk)).toBe(expected.expected_vsk_hex);

    const reVskEnv = await encryptVskEnvelope(
      decVsk,
      inputs.vault_id,
      inputs.vsk_envelope_password,
      fromBase64Url(expected.vsk_envelope.kdf.salt),
      fromBase64Url(expected.vsk_envelope.aead.nonce),
    );
    expect(reVskEnv).toEqual(expected.vsk_envelope);
  });

  it('decrypts Python NTRM1 secretstream media and round-trips TS-encrypted NTRM1 stream', async () => {
    const inputs = readJson<{
      k_media_hex: string;
      media_id: string;
      key_epoch: number;
      chunk_size: number;
      plaintext_b64u: string;
    }>('secretstream/inputs.json');
    const expected = readJson<{
      encrypted_blob_b64u: string;
      encrypted_size: number;
      encrypted_sha256: string;
    }>('secretstream/expected.json');

    const kMedia = hexToBytes(inputs.k_media_hex);
    const pyBlob = fromBase64Url(expected.encrypted_blob_b64u);
    expect(pyBlob.length).toBe(expected.encrypted_size);
    expect(sha256Hex(pyBlob)).toBe(expected.encrypted_sha256);

    const plainFromPy = await decryptMediaStream(kMedia, inputs.media_id, inputs.key_epoch, pyBlob);
    expect(toBase64Url(plainFromPy)).toBe(inputs.plaintext_b64u);

    // TS encrypt -> TS decrypt (including empty file edge case)
    const tsBlob = await encryptMediaStream(kMedia, inputs.media_id, inputs.key_epoch, plainFromPy, 1024);
    const roundTrip = await decryptMediaStream(kMedia, inputs.media_id, inputs.key_epoch, tsBlob);
    expect(toBase64Url(roundTrip)).toBe(inputs.plaintext_b64u);

    const emptyBlob = await encryptMediaStream(kMedia, inputs.media_id, inputs.key_epoch, new Uint8Array(0));
    const emptyPlain = await decryptMediaStream(kMedia, inputs.media_id, inputs.key_epoch, emptyBlob);
    expect(emptyPlain.length).toBe(0);
  });

  it('interoperates on pairing HMAC/HKDF/AEAD bundle and X25519 sealed epoch keys', async () => {
    const inputs = readJson<{
      pairing_secret_hex: string;
      pairing_nonce_hex: string;
      context: {
        vault_id: string;
        pairing_id: string;
        device_a_id: string;
        device_b_id: string;
        session_nonce_b: string;
      };
      bundle_nonce_hex: string;
      bundle: PairingBundlePayload;
      device_b_pub_b64u: string;
      device_b_priv_b64u: string;
      epoch2_root_hex: string;
    }>('pairing/inputs.json');

    const expected = readJson<{
      secret_proof_hmac: string;
      k_pair_hex: string;
      encrypted_bundle: { nonce: string; ciphertext: string };
      confirm_hmac: string;
      sealed_epoch2_for_b: string;
    }>('pairing/expected.json');

    const pairingSecret = hexToBytes(inputs.pairing_secret_hex);
    const pairingNonce = hexToBytes(inputs.pairing_nonce_hex);

    const proof = computeSecretProofHmac(
      pairingSecret,
      inputs.context.vault_id,
      inputs.context.pairing_id,
      inputs.context.device_b_id,
      inputs.context.session_nonce_b,
    );
    expect(proof).toBe(expected.secret_proof_hmac);

    const kPair = derivePairingKey(pairingSecret, pairingNonce, inputs.context);
    expect(bytesToHex(kPair)).toBe(expected.k_pair_hex);

    const decryptedBundle = await decryptPairingBundle(
      kPair,
      inputs.context,
      expected.encrypted_bundle.nonce,
      expected.encrypted_bundle.ciphertext,
    );
    expect(decryptedBundle).toEqual(inputs.bundle);

    const reSealed = await encryptPairingBundle(
      kPair,
      inputs.context,
      inputs.bundle,
      hexToBytes(inputs.bundle_nonce_hex),
    );
    expect(reSealed).toEqual(expected.encrypted_bundle);

    expect(
      computePairingConfirmHmac(
        kPair,
        inputs.context.pairing_id,
        inputs.context.device_b_id,
        inputs.context.vault_id,
      ),
    ).toBe(expected.confirm_hmac);

    // Unseal Python X25519 epoch 2 key in TS
    const pkB = fromBase64Url(inputs.device_b_pub_b64u);
    const skB = fromBase64Url(inputs.device_b_priv_b64u);
    const epoch2Key = await unsealEpochKeyForDevice(pkB, skB, expected.sealed_epoch2_for_b, {
      vault_id: inputs.context.vault_id,
      key_epoch: 2,
      target_device_id: inputs.context.device_b_id,
    });
    expect(bytesToHex(epoch2Key)).toBe(inputs.epoch2_root_hex);

    // Seal in TS and unseal in TS (context mismatch must throw)
    const tsSealed = await sealEpochKeyForDevice(pkB, {
      vault_id: inputs.context.vault_id,
      key_epoch: 2,
      target_device_id: inputs.context.device_b_id,
      epoch_root_key: epoch2Key,
    });
    const tsUnsealed = await unsealEpochKeyForDevice(pkB, skB, tsSealed, {
      vault_id: inputs.context.vault_id,
      key_epoch: 2,
      target_device_id: inputs.context.device_b_id,
    });
    expect(bytesToHex(tsUnsealed)).toBe(inputs.epoch2_root_hex);
    await expect(
      unsealEpochKeyForDevice(pkB, skB, tsSealed, {
        vault_id: inputs.context.vault_id,
        key_epoch: 3,
        target_device_id: inputs.context.device_b_id,
      }),
    ).rejects.toThrow();
  });

  it('canonicalizes RFC 8785 JCS edge cases identically to Python', () => {
    const inputs = readJson<{ payload: unknown }>('jcs/inputs.json');
    const expected = readJson<{ canonical_utf8: string }>('jcs/expected.json');
    expect(bytesToUtf8(canonicalJsonBytes(inputs.payload))).toBe(expected.canonical_utf8);
  });

  it('rejects all malformed, padded, or tampered vectors', async () => {
    const hkdfExp = readJson<{ k_op_hex: string; k_media_hex: string }>('hkdf/expected.json');
    const xchExp = readJson<{ vsk_envelope: VskRecoveryEnvelope }>('xchacha20poly1305/expected.json');
    const xchIn = readJson<{ vault_id: string }>('xchacha20poly1305/inputs.json');
    const pairingIn = readJson<{
      context: { vault_id: string };
      device_b_pub_b64u: string;
      device_b_priv_b64u: string;
    }>('pairing/inputs.json');
    const pairingExp = readJson<{ sealed_epoch2_for_b: string }>('pairing/expected.json');
    const bad = readJson<{
      padded_base64url: string;
      bad_aad_envelope: OperationEnvelope;
      bad_epoch_envelope: OperationEnvelope;
      tampered_tag_envelope: OperationEnvelope;
      truncated_media_b64u: string;
      trailing_media_b64u: string;
      bad_ntrm_magic_b64u: string;
      wrong_media_id: string;
      wrong_vsk_password: string;
      wrong_wrap_device_id: string;
    }>('malformed/inputs.json');
    const ssIn = readJson<{ media_id: string; key_epoch: number }>('secretstream/inputs.json');
    const ssExp = readJson<{ encrypted_blob_b64u: string }>('secretstream/expected.json');

    const kOp = hexToBytes(hkdfExp.k_op_hex);
    const kMedia = hexToBytes(hkdfExp.k_media_hex);

    expect(() => fromBase64Url(bad.padded_base64url)).toThrow();
    await expect(decryptOperationEnvelope(kOp, bad.bad_aad_envelope)).rejects.toThrow();
    await expect(decryptOperationEnvelope(kOp, bad.bad_epoch_envelope)).rejects.toThrow();
    await expect(decryptOperationEnvelope(kOp, bad.tampered_tag_envelope)).rejects.toThrow();
    await expect(
      decryptVskEnvelope(xchExp.vsk_envelope, xchIn.vault_id, bad.wrong_vsk_password),
    ).rejects.toThrow();
    await expect(
      decryptMediaStream(kMedia, ssIn.media_id, ssIn.key_epoch, fromBase64Url(bad.truncated_media_b64u)),
    ).rejects.toThrow();
    await expect(
      decryptMediaStream(kMedia, ssIn.media_id, ssIn.key_epoch, fromBase64Url(bad.trailing_media_b64u)),
    ).rejects.toThrow();
    await expect(
      decryptMediaStream(kMedia, ssIn.media_id, ssIn.key_epoch, fromBase64Url(bad.bad_ntrm_magic_b64u)),
    ).rejects.toThrow();
    await expect(
      decryptMediaStream(kMedia, bad.wrong_media_id, ssIn.key_epoch, fromBase64Url(ssExp.encrypted_blob_b64u)),
    ).rejects.toThrow();
    await expect(
      unsealEpochKeyForDevice(
        fromBase64Url(pairingIn.device_b_pub_b64u),
        fromBase64Url(pairingIn.device_b_priv_b64u),
        pairingExp.sealed_epoch2_for_b,
        {
          vault_id: pairingIn.context.vault_id,
          key_epoch: 2,
          target_device_id: bad.wrong_wrap_device_id,
        },
      ),
    ).rejects.toThrow();
  });
});
