import sodium from 'libsodium-wrappers-sumo';
import { hkdf } from '@noble/hashes/hkdf.js';
import { hmac } from '@noble/hashes/hmac.js';
import { sha256 } from '@noble/hashes/sha2.js';
import canonicalize from 'canonicalize';

export const CRYPTO_VERSION = 1;
export const PROTOCOL_VERSION = 1;
export const MEDIA_CRYPTO_VERSION = 1;
export const MEDIA_MAGIC = new Uint8Array([0x4e, 0x54, 0x52, 0x4d]); // "NTRM"
export const MEDIA_CHUNK_SIZE = 4 * 1024 * 1024; // 4 MiB plaintext per secretstream chunk
export const ARGON2ID_MEM_KIB = 65536;
export const ARGON2ID_MEM_BYTES = ARGON2ID_MEM_KIB * 1024; // 67108864
export const ARGON2ID_ITERATIONS = 3;
export const ARGON2ID_PARALLELISM = 1;

const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const UUID_GENERIC_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const B64U_NOPAD_RE = /^[A-Za-z0-9_-]+$/;
const SEQ_RE = /^[1-9][0-9]*$/;

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });

export async function ensureCryptoReady(): Promise<typeof sodium> {
  await sodium.ready;
  return sodium;
}

export function utf8ToBytes(str: string): Uint8Array {
  return encoder.encode(str);
}

export function bytesToUtf8(bytes: Uint8Array): string {
  return decoder.decode(bytes);
}

export function randomBytes(len: number): Uint8Array {
  return sodium.randombytes_buf(len);
}

export function generateUuidV4(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID().toLowerCase();
  }
  const bytes = randomBytes(16);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytesToHex(bytes);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function assertUuid(uuid: string, requireV4 = false): void {
  const re = requireV4 ? UUID_V4_RE : UUID_GENERIC_RE;
  if (!re.test(uuid)) {
    throw new Error(`Invalid canonical lowercase UUID: ${uuid}`);
  }
}

export function uuidToBytes(uuid: string): Uint8Array {
  assertUuid(uuid, false);
  return hexToBytes(uuid.replace(/-/g, ''));
}

export function bytesToUuid(bytes: Uint8Array): string {
  if (bytes.length !== 16) throw new Error(`Expected 16 UUID bytes, got ${bytes.length}`);
  const hex = bytesToHex(bytes);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function bytesToHex(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, '0');
  }
  return out;
}

export function hexToBytes(hex: string): Uint8Array {
  if (hex.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(hex)) {
    throw new Error('Invalid hex string');
  }
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

export function toBase64Url(bytes: Uint8Array): string {
  return sodium.to_base64(bytes, sodium.base64_variants.URLSAFE_NO_PADDING);
}

export function fromBase64Url(b64u: string): Uint8Array {
  if (!b64u || !B64U_NOPAD_RE.test(b64u)) {
    throw new Error('Invalid unpadded Base64URL encoding');
  }
  const decoded = sodium.from_base64(b64u, sodium.base64_variants.URLSAFE_NO_PADDING);
  // Strict canonical round-trip check to reject non-canonical trailing bits
  if (toBase64Url(decoded) !== b64u) {
    throw new Error('Non-canonical unpadded Base64URL encoding');
  }
  return decoded;
}

export function sha256Bytes(data: Uint8Array): Uint8Array {
  return sha256(data);
}

export function sha256Hex(data: Uint8Array): string {
  return bytesToHex(sha256(data));
}

export function hmacSha256(key: Uint8Array, msg: Uint8Array): Uint8Array {
  return hmac(sha256, key, msg);
}

export function hkdfSha256(
  ikm: Uint8Array,
  salt: Uint8Array,
  info: Uint8Array,
  length = 32,
): Uint8Array {
  return hkdf(sha256, ikm, salt, info, length);
}

export function canonicalJsonBytes(value: unknown): Uint8Array {
  const s = canonicalize(value);
  if (s === undefined) {
    throw new Error('Value cannot be canonicalized to RFC 8785 JSON');
  }
  return utf8ToBytes(s);
}

export async function deriveArgon2idKek(
  passwordUtf8: string,
  salt16: Uint8Array,
  memoryKib = ARGON2ID_MEM_KIB,
  iterations = ARGON2ID_ITERATIONS,
  parallelism = ARGON2ID_PARALLELISM,
): Promise<Uint8Array> {
  await ensureCryptoReady();
  if (salt16.length !== 16) {
    throw new Error(`Argon2id salt must be 16 bytes, got ${salt16.length}`);
  }
  if (parallelism !== 1) {
    throw new Error(`Unsupported Argon2id parallelism=${parallelism}; V1 requires p=1`);
  }
  if (memoryKib < 8192 || iterations < 1) {
    throw new Error('Unsafe Argon2id parameters');
  }
  const passwordBytes = utf8ToBytes(passwordUtf8);
  return sodium.crypto_pwhash(
    32,
    passwordBytes,
    salt16,
    iterations,
    memoryKib * 1024,
    sodium.crypto_pwhash_ALG_ARGON2ID13,
  );
}

export function deriveInitialEpoch1RootKey(vsk: Uint8Array, vaultId: string): Uint8Array {
  if (vsk.length !== 32) throw new Error('VSK must be 32 bytes');
  return hkdfSha256(
    vsk,
    uuidToBytes(vaultId),
    utf8ToBytes('NOTEOR/SYNC/V1/EPOCH-1-SEED'),
    32,
  );
}

export function deriveEpochKeys(
  epochRootKey: Uint8Array,
  vaultId: string,
  keyEpoch: number,
): { kOp: Uint8Array; kMediaEpoch: Uint8Array } {
  if (epochRootKey.length !== 32) throw new Error('EpochRootKey must be 32 bytes');
  if (!Number.isInteger(keyEpoch) || keyEpoch < 1) throw new Error('keyEpoch must be >= 1');
  const salt = uuidToBytes(vaultId);
  const kOp = hkdfSha256(
    epochRootKey,
    salt,
    utf8ToBytes(`NOTEOR/SYNC/V1/OPERATIONS/EPOCH/${keyEpoch}`),
    32,
  );
  const kMediaEpoch = hkdfSha256(
    epochRootKey,
    salt,
    utf8ToBytes(`NOTEOR/SYNC/V1/MEDIA/EPOCH/${keyEpoch}`),
    32,
  );
  return { kOp, kMediaEpoch };
}

export function deriveMediaObjectKey(kMediaEpoch: Uint8Array, mediaId: string): Uint8Array {
  if (kMediaEpoch.length !== 32) throw new Error('kMediaEpoch must be 32 bytes');
  return hkdfSha256(
    kMediaEpoch,
    uuidToBytes(mediaId),
    utf8ToBytes('NOTEOR/SYNC/V1/MEDIA/OBJECT'),
    32,
  );
}

export interface OperationEnvelope {
  protocol_version: number;
  crypto_version: number;
  key_epoch: number;
  vault_id: string;
  device_id: string;
  operation_id: string;
  device_seq: string;
  nonce: string;
  ciphertext: string;
  server_seq?: string;
}

export interface DecryptedOperationPayload<T = Record<string, unknown>> {
  payload_version: number;
  operation_type:
    | 'entity.upsert'
    | 'entity.trash'
    | 'entity.restore'
    | 'entity.delete'
    | 'media.register'
    | 'media.delete';
  entity_type: 'note' | 'folder' | 'category' | 'tag' | 'relation' | 'attachment';
  entity_id: string;
  revision_id: string;
  parent_revision_ids: string[];
  created_at: string;
  data: T;
}

export function validateEnvelopeFields(env: OperationEnvelope): void {
  if (env.protocol_version !== PROTOCOL_VERSION) {
    throw new Error(`Unsupported protocol_version: ${env.protocol_version}`);
  }
  if (env.crypto_version !== CRYPTO_VERSION) {
    throw new Error(`Unsupported crypto_version: ${env.crypto_version}`);
  }
  if (!Number.isInteger(env.key_epoch) || env.key_epoch < 1) {
    throw new Error(`Invalid key_epoch: ${env.key_epoch}`);
  }
  assertUuid(env.vault_id);
  assertUuid(env.device_id);
  assertUuid(env.operation_id);
  if (!SEQ_RE.test(env.device_seq)) {
    throw new Error(`Invalid device_seq: ${env.device_seq}`);
  }
}

export function buildOperationAad(header: {
  protocol_version: number;
  crypto_version: number;
  key_epoch: number;
  vault_id: string;
  device_id: string;
  operation_id: string;
  device_seq: string;
}): Uint8Array {
  return canonicalJsonBytes({
    crypto_version: header.crypto_version,
    device_id: header.device_id,
    device_seq: header.device_seq,
    key_epoch: header.key_epoch,
    operation_id: header.operation_id,
    protocol_version: header.protocol_version,
    vault_id: header.vault_id,
  });
}

export async function aeadEncrypt(
  key32: Uint8Array,
  plaintext: Uint8Array,
  aad: Uint8Array,
  nonceOverride?: Uint8Array,
): Promise<{ nonce: Uint8Array; ciphertext: Uint8Array }> {
  await ensureCryptoReady();
  if (key32.length !== 32) throw new Error('AEAD key must be 32 bytes');
  const nonce = nonceOverride ?? randomBytes(24);
  if (nonce.length !== 24) throw new Error('XChaCha20-Poly1305 nonce must be 24 bytes');
  const ciphertext = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(
    plaintext,
    aad,
    null,
    nonce,
    key32,
  );
  return { nonce, ciphertext };
}

export async function aeadDecrypt(
  key32: Uint8Array,
  nonce24: Uint8Array,
  ciphertext: Uint8Array,
  aad: Uint8Array,
): Promise<Uint8Array> {
  await ensureCryptoReady();
  if (key32.length !== 32) throw new Error('AEAD key must be 32 bytes');
  if (nonce24.length !== 24) throw new Error('XChaCha20-Poly1305 nonce must be 24 bytes');
  if (ciphertext.length < 16) throw new Error('Ciphertext shorter than 16-byte Poly1305 tag');
  return sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
    null,
    ciphertext,
    aad,
    nonce24,
    key32,
  );
}

export async function encryptOperationEnvelope(
  kOp: Uint8Array,
  meta: {
    vault_id: string;
    device_id: string;
    operation_id: string;
    device_seq: string;
    key_epoch: number;
    protocol_version?: number;
    crypto_version?: number;
  },
  payload: DecryptedOperationPayload,
  nonceOverride?: Uint8Array,
): Promise<OperationEnvelope> {
  const header = {
    protocol_version: meta.protocol_version ?? PROTOCOL_VERSION,
    crypto_version: meta.crypto_version ?? CRYPTO_VERSION,
    key_epoch: meta.key_epoch,
    vault_id: meta.vault_id,
    device_id: meta.device_id,
    operation_id: meta.operation_id,
    device_seq: meta.device_seq,
  };
  validateEnvelopeFields({ ...header, nonce: '', ciphertext: '' });
  const aad = buildOperationAad(header);
  const plaintext = utf8ToBytes(JSON.stringify(payload));
  const { nonce, ciphertext } = await aeadEncrypt(kOp, plaintext, aad, nonceOverride);
  return {
    ...header,
    nonce: toBase64Url(nonce),
    ciphertext: toBase64Url(ciphertext),
  };
}

export async function decryptOperationEnvelope<T = Record<string, unknown>>(
  kOp: Uint8Array,
  envelope: OperationEnvelope,
): Promise<DecryptedOperationPayload<T>> {
  validateEnvelopeFields(envelope);
  const nonce = fromBase64Url(envelope.nonce);
  const ciphertext = fromBase64Url(envelope.ciphertext);
  if (nonce.length !== 24) throw new Error(`Invalid nonce size: ${nonce.length}`);
  const aad = buildOperationAad(envelope);
  const plaintext = await aeadDecrypt(kOp, nonce, ciphertext, aad);
  const parsed = JSON.parse(bytesToUtf8(plaintext)) as DecryptedOperationPayload<T>;
  if (parsed.payload_version !== 1) {
    throw new Error(`Unsupported payload_version: ${parsed.payload_version}`);
  }
  assertUuid(parsed.entity_id);
  assertUuid(parsed.revision_id);
  for (const parent of parsed.parent_revision_ids ?? []) {
    assertUuid(parent);
  }
  return parsed;
}

export interface VskRecoveryEnvelope {
  envelope_version: number;
  kdf: {
    algorithm: 'argon2id13';
    salt: string;
    memory_kib: number;
    iterations: number;
    parallelism: number;
    output_length: number;
  };
  aead: {
    algorithm: 'xchacha20poly1305-ietf';
    nonce: string;
    ciphertext: string;
  };
}

export async function encryptVskEnvelope(
  vsk: Uint8Array,
  vaultId: string,
  password: string,
  saltOverride?: Uint8Array,
  nonceOverride?: Uint8Array,
): Promise<VskRecoveryEnvelope> {
  await ensureCryptoReady();
  if (vsk.length !== 32) throw new Error('VSK must be 32 bytes');
  assertUuid(vaultId);
  const salt = saltOverride ?? randomBytes(16);
  const kek = await deriveArgon2idKek(password, salt);
  try {
    const aad = canonicalJsonBytes({
      envelope_version: 1,
      kdf: 'argon2id13',
      purpose: 'vault-recovery',
      vault_id: vaultId,
    });
    const { nonce, ciphertext } = await aeadEncrypt(kek, vsk, aad, nonceOverride);
    return {
      envelope_version: 1,
      kdf: {
        algorithm: 'argon2id13',
        salt: toBase64Url(salt),
        memory_kib: ARGON2ID_MEM_KIB,
        iterations: ARGON2ID_ITERATIONS,
        parallelism: ARGON2ID_PARALLELISM,
        output_length: 32,
      },
      aead: {
        algorithm: 'xchacha20poly1305-ietf',
        nonce: toBase64Url(nonce),
        ciphertext: toBase64Url(ciphertext),
      },
    };
  } finally {
    kek.fill(0);
  }
}

export async function decryptVskEnvelope(
  envelope: VskRecoveryEnvelope,
  vaultId: string,
  password: string,
): Promise<Uint8Array> {
  if (envelope.envelope_version !== 1 || envelope.kdf.algorithm !== 'argon2id13') {
    throw new Error('Unsupported VSK envelope version or KDF');
  }
  assertUuid(vaultId);
  const salt = fromBase64Url(envelope.kdf.salt);
  const nonce = fromBase64Url(envelope.aead.nonce);
  const ciphertext = fromBase64Url(envelope.aead.ciphertext);
  const kek = await deriveArgon2idKek(
    password,
    salt,
    envelope.kdf.memory_kib,
    envelope.kdf.iterations,
    envelope.kdf.parallelism,
  );
  try {
    const aad = canonicalJsonBytes({
      envelope_version: 1,
      kdf: 'argon2id13',
      purpose: 'vault-recovery',
      vault_id: vaultId,
    });
    const vsk = await aeadDecrypt(kek, nonce, ciphertext, aad);
    if (vsk.length !== 32) throw new Error('Invalid decrypted VSK size');
    return vsk;
  } finally {
    kek.fill(0);
  }
}

// ── Binary Media Stream Format (NTRM1) ─────────────────────────────────────────

export async function encryptMediaStream(
  kMedia: Uint8Array,
  mediaId: string,
  keyEpoch: number,
  plaintext: Uint8Array,
  chunkSize = MEDIA_CHUNK_SIZE,
): Promise<Uint8Array> {
  await ensureCryptoReady();
  if (kMedia.length !== 32) throw new Error('kMedia must be 32 bytes');
  if (!Number.isInteger(keyEpoch) || keyEpoch < 1) throw new Error('Invalid keyEpoch');
  if (!Number.isInteger(chunkSize) || chunkSize < 1) throw new Error('Invalid chunkSize');

  const mediaIdBytes = uuidToBytes(mediaId);
  const { state, header: streamHeader } = sodium.crypto_secretstream_xchacha20poly1305_init_push(kMedia);

  const fileHeader = new Uint8Array(49);
  fileHeader.set(MEDIA_MAGIC, 0);
  fileHeader[4] = MEDIA_CRYPTO_VERSION;
  fileHeader.set(mediaIdBytes, 5);
  const view = new DataView(fileHeader.buffer);
  view.setUint32(21, keyEpoch, false);
  fileHeader.set(streamHeader, 25);

  const frames: Uint8Array[] = [fileHeader];
  const total = plaintext.length;
  let offset = 0;

  // Handle empty file as a single final chunk
  do {
    const end = Math.min(offset + chunkSize, total);
    const slice = plaintext.subarray(offset, end);
    const isFinal = end >= total;
    const tag = isFinal
      ? sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL
      : sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE;
    const cipherChunk = sodium.crypto_secretstream_xchacha20poly1305_push(
      state,
      slice,
      fileHeader,
      tag,
    );
    const frame = new Uint8Array(4 + cipherChunk.length);
    new DataView(frame.buffer).setUint32(0, cipherChunk.length, false);
    frame.set(cipherChunk, 4);
    frames.push(frame);
    offset = end;
  } while (offset < total);

  const totalLen = frames.reduce((acc, f) => acc + f.length, 0);
  const out = new Uint8Array(totalLen);
  let pos = 0;
  for (const f of frames) {
    out.set(f, pos);
    pos += f.length;
  }
  return out;
}

export async function decryptMediaStream(
  kMedia: Uint8Array,
  expectedMediaId: string,
  expectedKeyEpoch: number,
  encryptedBlob: Uint8Array,
): Promise<Uint8Array> {
  await ensureCryptoReady();
  if (kMedia.length !== 32) throw new Error('kMedia must be 32 bytes');
  if (encryptedBlob.length < 49) {
    throw new Error('Encrypted media blob too short for NTRM1 header');
  }
  const fileHeader = encryptedBlob.subarray(0, 49);
  for (let i = 0; i < 4; i++) {
    if (fileHeader[i] !== MEDIA_MAGIC[i]) throw new Error('Invalid NTRM magic header');
  }
  if (fileHeader[4] !== MEDIA_CRYPTO_VERSION) {
    throw new Error(`Unsupported media_crypto_version: ${fileHeader[4]}`);
  }
  const headerMediaId = bytesToUuid(fileHeader.subarray(5, 21));
  if (headerMediaId !== expectedMediaId) {
    throw new Error(`Media ID mismatch: expected ${expectedMediaId}, got ${headerMediaId}`);
  }
  const headerEpoch = new DataView(fileHeader.buffer, fileHeader.byteOffset, fileHeader.byteLength).getUint32(21, false);
  if (headerEpoch !== expectedKeyEpoch) {
    throw new Error(`Media key_epoch mismatch: expected ${expectedKeyEpoch}, got ${headerEpoch}`);
  }
  const streamHeader = fileHeader.subarray(25, 49);
  const state = sodium.crypto_secretstream_xchacha20poly1305_init_pull(streamHeader, kMedia);

  const chunks: Uint8Array[] = [];
  let pos = 49;
  let seenFinal = false;

  while (pos < encryptedBlob.length) {
    if (seenFinal) {
      throw new Error('Trailing data detected after secretstream TAG_FINAL');
    }
    if (pos + 4 > encryptedBlob.length) {
      throw new Error('Truncated media frame length');
    }
    const frameLen = new DataView(
      encryptedBlob.buffer,
      encryptedBlob.byteOffset + pos,
      4,
    ).getUint32(0, false);
    pos += 4;
    if (frameLen < 17 || pos + frameLen > encryptedBlob.length) {
      throw new Error('Truncated or invalid encrypted media frame');
    }
    const frameBytes = encryptedBlob.subarray(pos, pos + frameLen);
    pos += frameLen;
    const res = sodium.crypto_secretstream_xchacha20poly1305_pull(state, frameBytes, fileHeader);
    if (!res) {
      throw new Error('Media stream authentication failed');
    }
    chunks.push(res.message);
    if (res.tag === sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL) {
      seenFinal = true;
    }
  }

  if (!seenFinal) {
    throw new Error('Incomplete media stream: missing TAG_FINAL');
  }

  const totalOut = chunks.reduce((acc, c) => acc + c.length, 0);
  const out = new Uint8Array(totalOut);
  let outPos = 0;
  for (const c of chunks) {
    out.set(c, outPos);
    outPos += c.length;
  }
  return out;
}

// ── Device X25519 Keypair, Epoch Wrapping & Pairing ────────────────────────────

export async function generateDeviceKeypair(): Promise<{ publicKey: Uint8Array; privateKey: Uint8Array }> {
  await ensureCryptoReady();
  const kp = sodium.crypto_box_keypair();
  return { publicKey: kp.publicKey, privateKey: kp.privateKey };
}

export async function sealEpochKeyForDevice(
  devicePublicKey: Uint8Array,
  params: {
    vault_id: string;
    key_epoch: number;
    target_device_id: string;
    epoch_root_key: Uint8Array;
  },
): Promise<string> {
  await ensureCryptoReady();
  assertUuid(params.vault_id);
  assertUuid(params.target_device_id);
  if (params.epoch_root_key.length !== 32) throw new Error('EpochRootKey must be 32 bytes');
  const payloadBytes = canonicalJsonBytes({
    domain: 'NOTEOR/SYNC/V1/DEVICE-WRAP',
    epoch_root_key_b64u: toBase64Url(params.epoch_root_key),
    key_epoch: params.key_epoch,
    target_device_id: params.target_device_id,
    vault_id: params.vault_id,
  });
  const sealed = sodium.crypto_box_seal(payloadBytes, devicePublicKey);
  return toBase64Url(sealed);
}

export async function unsealEpochKeyForDevice(
  devicePublicKey: Uint8Array,
  devicePrivateKey: Uint8Array,
  sealedB64u: string,
  expected: { vault_id: string; key_epoch: number; target_device_id: string },
): Promise<Uint8Array> {
  await ensureCryptoReady();
  const sealed = fromBase64Url(sealedB64u);
  const plain = sodium.crypto_box_seal_open(sealed, devicePublicKey, devicePrivateKey);
  const obj = JSON.parse(bytesToUtf8(plain)) as {
    domain: string;
    vault_id: string;
    key_epoch: number;
    target_device_id: string;
    epoch_root_key_b64u: string;
  };
  if (
    obj.domain !== 'NOTEOR/SYNC/V1/DEVICE-WRAP' ||
    obj.vault_id !== expected.vault_id ||
    obj.key_epoch !== expected.key_epoch ||
    obj.target_device_id !== expected.target_device_id
  ) {
    throw new Error('Sealed epoch key domain/context mismatch');
  }
  const key = fromBase64Url(obj.epoch_root_key_b64u);
  if (key.length !== 32) throw new Error('Invalid unsealed epoch root key length');
  return key;
}

export function computeSecretProofHmac(
  pairingSecret: Uint8Array,
  vaultId: string,
  pairingId: string,
  deviceBId: string,
  sessionNonceBB64u: string,
): string {
  const msg = utf8ToBytes(`${vaultId}:${pairingId}:${deviceBId}:${sessionNonceBB64u}`);
  return toBase64Url(hmacSha256(pairingSecret, msg));
}

export function derivePairingKey(
  pairingSecret: Uint8Array,
  pairingNonce: Uint8Array,
  context: {
    vault_id: string;
    pairing_id: string;
    device_a_id: string;
    device_b_id: string;
    session_nonce_b: string;
  },
): Uint8Array {
  const info = utf8ToBytes(
    `NOTEOR/SYNC/V1/PAIRING/${context.vault_id}/${context.pairing_id}/${context.device_a_id}/${context.device_b_id}/${context.session_nonce_b}`,
  );
  return hkdfSha256(pairingSecret, pairingNonce, info, 32);
}

export interface PairingBundlePayload {
  vault_id: string;
  vsk_b64u: string;
  current_key_epoch: number;
  epoch_keys: Record<string, string>; // epoch string -> epoch_root_key_b64u
}

export async function encryptPairingBundle(
  kPair: Uint8Array,
  context: {
    vault_id: string;
    pairing_id: string;
    device_a_id: string;
    device_b_id: string;
    session_nonce_b: string;
  },
  bundle: PairingBundlePayload,
  nonceOverride?: Uint8Array,
): Promise<{ nonce: string; ciphertext: string }> {
  const aad = canonicalJsonBytes({
    device_a_id: context.device_a_id,
    device_b_id: context.device_b_id,
    pairing_id: context.pairing_id,
    pairing_version: 1,
    session_nonce_b: context.session_nonce_b,
    vault_id: context.vault_id,
  });
  const plain = utf8ToBytes(JSON.stringify(bundle));
  const { nonce, ciphertext } = await aeadEncrypt(kPair, plain, aad, nonceOverride);
  return { nonce: toBase64Url(nonce), ciphertext: toBase64Url(ciphertext) };
}

export async function decryptPairingBundle(
  kPair: Uint8Array,
  context: {
    vault_id: string;
    pairing_id: string;
    device_a_id: string;
    device_b_id: string;
    session_nonce_b: string;
  },
  nonceB64u: string,
  ciphertextB64u: string,
): Promise<PairingBundlePayload> {
  const aad = canonicalJsonBytes({
    device_a_id: context.device_a_id,
    device_b_id: context.device_b_id,
    pairing_id: context.pairing_id,
    pairing_version: 1,
    session_nonce_b: context.session_nonce_b,
    vault_id: context.vault_id,
  });
  const plain = await aeadDecrypt(
    kPair,
    fromBase64Url(nonceB64u),
    fromBase64Url(ciphertextB64u),
    aad,
  );
  const parsed = JSON.parse(bytesToUtf8(plain)) as PairingBundlePayload;
  if (parsed.vault_id !== context.vault_id) {
    throw new Error('Pairing bundle vault_id mismatch');
  }
  return parsed;
}

export function computePairingConfirmHmac(
  kPair: Uint8Array,
  pairingId: string,
  deviceBId: string,
  vaultId: string,
): string {
  const msg = utf8ToBytes(`CONFIRM:${pairingId}:${deviceBId}:${vaultId}`);
  return toBase64Url(hmacSha256(kPair, msg));
}
