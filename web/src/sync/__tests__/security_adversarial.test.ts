import 'fake-indexeddb/auto';
import { describe, it, expect, beforeAll, beforeEach } from 'vitest';
import { db } from '../../db';
import {
  ensureCryptoReady,
  generateUuidV4,
  assertUuid,
  fromBase64Url,
  toBase64Url,
  deriveArgon2idKek,
  deriveInitialEpoch1RootKey,
  deriveEpochKeys,
  deriveMediaObjectKey,
  encryptOperationEnvelope,
  decryptOperationEnvelope,
  encryptMediaStream,
  decryptMediaStream,
  generateDeviceKeypair,
  sealEpochKeyForDevice,
  unsealEpochKeyForDevice,
} from '../crypto';
import { PwaSyncEngine, SyncHttpClient, buildKeyringFromVsk } from '../engine';

function flipB64uByte(b64u: string, index = 0): string {
  const bytes = fromBase64Url(b64u);
  const pos = ((index % bytes.length) + bytes.length) % bytes.length;
  bytes[pos] ^= 0x01;
  return toBase64Url(bytes);
}

describe('Phase 3: Adversarial Security & Negative Tests (TS)', () => {
  beforeAll(async () => {
    await ensureCryptoReady();
  });

  beforeEach(async () => {
    await db.delete();
    await db.open();
  });

  it('rejects AEAD bitflips in ciphertext/tag/nonce and all AAD field mutations', async () => {
    const vaultId = generateUuidV4();
    const deviceId = generateUuidV4();
    const opId = generateUuidV4();
    const vsk = new Uint8Array(32).fill(7);
    const { kOp } = deriveEpochKeys(deriveInitialEpoch1RootKey(vsk, vaultId), vaultId, 1);

    const env = await encryptOperationEnvelope(
      kOp,
      { vault_id: vaultId, device_id: deviceId, operation_id: opId, device_seq: '1', key_epoch: 1 },
      {
        payload_version: 1,
        operation_type: 'entity.upsert',
        entity_type: 'note',
        entity_id: generateUuidV4(),
        revision_id: generateUuidV4(),
        parent_revision_ids: [],
        created_at: '2026-11-20T01:00:00.000Z',
        data: { title: 'Test' },
      },
    );

    await expect(
      decryptOperationEnvelope(kOp, { ...env, nonce: flipB64uByte(env.nonce, 0) }),
    ).rejects.toThrow();
    await expect(
      decryptOperationEnvelope(kOp, { ...env, ciphertext: flipB64uByte(env.ciphertext, 0) }),
    ).rejects.toThrow();
    await expect(
      decryptOperationEnvelope(kOp, { ...env, ciphertext: flipB64uByte(env.ciphertext, -1) }),
    ).rejects.toThrow();

    await expect(
      decryptOperationEnvelope(kOp, { ...env, vault_id: generateUuidV4() }),
    ).rejects.toThrow();
    await expect(
      decryptOperationEnvelope(kOp, { ...env, device_id: generateUuidV4() }),
    ).rejects.toThrow();
    await expect(
      decryptOperationEnvelope(kOp, { ...env, operation_id: generateUuidV4() }),
    ).rejects.toThrow();
    await expect(
      decryptOperationEnvelope(kOp, { ...env, device_seq: '2' }),
    ).rejects.toThrow();
    await expect(
      decryptOperationEnvelope(kOp, { ...env, key_epoch: 2 }),
    ).rejects.toThrow();

    expect(() => assertUuid(vaultId.toUpperCase())).toThrow();
    expect(() => fromBase64Url('AB==')).toThrow();
    expect(() => fromBase64Url('AB')).toThrow(); // non-canonical trailing bits
  });

  it('enforces Argon2id DoS bounds and X25519 key lengths', async () => {
    const salt = new Uint8Array(16).fill(2);
    await expect(deriveArgon2idKek('pw', salt, 1024 * 1024, 3, 1)).rejects.toThrow();
    await expect(deriveArgon2idKek('pw', salt, 8192, 50, 1)).rejects.toThrow();
    await expect(deriveArgon2idKek('pw', salt, 8192, 1, 2)).rejects.toThrow();
    await expect(deriveArgon2idKek('pw', salt, 8192.5, 1, 1)).rejects.toThrow();

    const kp = await generateDeviceKeypair();
    const vaultId = generateUuidV4();
    const devId = generateUuidV4();
    const root = new Uint8Array(32).fill(9);

    await expect(
      sealEpochKeyForDevice(kp.publicKey.subarray(0, 16), {
        vault_id: vaultId,
        key_epoch: 2,
        target_device_id: devId,
        epoch_root_key: root,
      }),
    ).rejects.toThrow();

    const sealed = await sealEpochKeyForDevice(kp.publicKey, {
      vault_id: vaultId,
      key_epoch: 2,
      target_device_id: devId,
      epoch_root_key: root,
    });
    await expect(
      unsealEpochKeyForDevice(kp.publicKey, kp.privateKey.subarray(0, 16), sealed, {
        vault_id: vaultId,
        key_epoch: 2,
        target_device_id: devId,
      }),
    ).rejects.toThrow();
  });

  it('rejects reordered, duplicated, truncated, and corrupted NTRM1 media streams', async () => {
    const kMedia = new Uint8Array(32).fill(4);
    const mediaId = generateUuidV4();
    const plain = new TextEncoder().encode('chunk-1-aaaa' + 'chunk-2-bbbb' + 'chunk-3-cccc');
    const blob = await encryptMediaStream(kMedia, mediaId, 1, plain, 12);

    const header = blob.subarray(0, 49);
    const frames: Uint8Array[] = [];
    let pos = 49;
    while (pos < blob.length) {
      const flen = new DataView(blob.buffer, blob.byteOffset + pos, 4).getUint32(0, false);
      frames.push(blob.subarray(pos, pos + 4 + flen));
      pos += 4 + flen;
    }
    expect(frames.length).toBe(3);

    const concat = (...parts: Uint8Array[]) => {
      const total = parts.reduce((a, b) => a + b.length, 0);
      const out = new Uint8Array(total);
      let off = 0;
      for (const p of parts) {
        out.set(p, off);
        off += p.length;
      }
      return out;
    };

    await expect(
      decryptMediaStream(kMedia, mediaId, 1, concat(header, frames[1], frames[0], frames[2])),
    ).rejects.toThrow();
    await expect(
      decryptMediaStream(kMedia, mediaId, 1, concat(header, frames[0], frames[0], frames[1], frames[2])),
    ).rejects.toThrow();
    await expect(
      decryptMediaStream(kMedia, mediaId, 1, concat(header, frames[0], frames[1])),
    ).rejects.toThrow();

    const badVer = new Uint8Array(blob);
    badVer[4] = 2;
    await expect(decryptMediaStream(kMedia, mediaId, 1, badVer)).rejects.toThrow();
  });

  it('rejects cross-vault splicing and non-monotonic server_seq during pull', async () => {
    const vaultId = generateUuidV4();
    const devA = generateUuidV4();
    const devB = generateUuidV4();
    const vsk = new Uint8Array(32).fill(3);
    await db.sync_state.put({ key: 'vault_id', value: vaultId });
    await db.sync_state.put({ key: 'device_id', value: devA });
    await db.sync_state.put({ key: 'last_server_seq', value: '5' });

    const keyring = buildKeyringFromVsk(vaultId, devA, vsk);
    const { kOp } = deriveEpochKeys(keyring.epochRootKeys.get(1)!, vaultId, 1);

    const spliced = await encryptOperationEnvelope(
      kOp,
      {
        vault_id: generateUuidV4(),
        device_id: devB,
        operation_id: generateUuidV4(),
        device_seq: '1',
        key_epoch: 1,
      },
      {
        payload_version: 1,
        operation_type: 'entity.upsert',
        entity_type: 'note',
        entity_id: generateUuidV4(),
        revision_id: generateUuidV4(),
        parent_revision_ids: [],
        created_at: '2026-11-20T01:00:00.000Z',
        data: {},
      },
    );

    const client1 = new SyncHttpClient({ baseUrl: 'http://127.0.0.1', accessToken: 'tok' });
    client1.listEpochKeys = async () => ({ current_key_epoch: 1, epochs: [] });
    client1.pullOperations = async () => ({
      operations: [{ ...spliced, server_seq: '6', received_at: '2026-11-20T01:00:00.000Z' }],
      next_since_seq: '6',
      has_more: false,
    });
    const engine1 = new PwaSyncEngine(client1, keyring);
    await expect(engine1.syncNow()).rejects.toThrow(/vault_id mismatch/);

    const replayEnv = await encryptOperationEnvelope(
      kOp,
      {
        vault_id: vaultId,
        device_id: devB,
        operation_id: generateUuidV4(),
        device_seq: '1',
        key_epoch: 1,
      },
      {
        payload_version: 1,
        operation_type: 'entity.upsert',
        entity_type: 'note',
        entity_id: generateUuidV4(),
        revision_id: generateUuidV4(),
        parent_revision_ids: [],
        created_at: '2026-11-20T01:00:00.000Z',
        data: {},
      },
    );
    client1.pullOperations = async () => ({
      operations: [{ ...replayEnv, server_seq: '5', received_at: '2026-11-20T01:00:00.000Z' }],
      next_since_seq: '5',
      has_more: false,
    });
    await expect(engine1.syncNow()).rejects.toThrow(/Non-monotonic server_seq/);
  });
});
