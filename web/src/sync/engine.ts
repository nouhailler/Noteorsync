import { db, now } from '../db';
import type {
  Note,
  Category,
  Folder,
  Tag,
  Attachment,
  SyncOperationType,
  SyncPendingOpRecord,
} from '../types';
import {
  buildOperationAad,
  decryptMediaStream,
  decryptOperationEnvelope,
  deriveEpochKeys,
  deriveInitialEpoch1RootKey,
  deriveMediaObjectKey,
  encryptMediaStream,
  encryptOperationEnvelope,
  fromBase64Url,
  generateUuidV4,
  sealEpochKeyForDevice,
  sha256Hex,
  toBase64Url,
  unsealEpochKeyForDevice,
  type DecryptedOperationPayload,
  type OperationEnvelope,
} from './crypto';
import { normalizeTagName, normalizeUtcTimestamp } from './ids';

export interface SyncVaultKeyring {
  vaultId: string;
  deviceId: string;
  currentKeyEpoch: number;
  devicePublicKey?: Uint8Array;
  devicePrivateKey?: Uint8Array;
  epochRootKeys: Map<number, Uint8Array>;
}

export interface PullOperationsResponse {
  operations: Array<OperationEnvelope & { server_seq: string; received_at?: string }>;
  next_since_seq: string;
  has_more: boolean;
}

export interface BatchPushResponse {
  accepted: Array<{ operation_id: string; server_seq: string; duplicate?: boolean }>;
  next_expected_device_seq: string;
}

export interface ServerEpochKeyRecord {
  key_epoch: number;
  created_at: string;
  created_by_device_id: string;
  reason: string;
  wrapped_epoch_key: string | null;
}

export interface ServerDeviceInfo {
  device_id: string;
  device_name: string;
  device_type: 'desktop' | 'pwa' | 'mobile';
  public_key_x25519: string;
  status: 'active' | 'revoked';
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
}

export interface SyncTransport {
  baseUrl: string;
  accessToken: string;
  fetchImpl?: typeof fetch;
}

export interface SyncProgress {
  phase: 'idle' | 'pushing_media' | 'pushing_ops' | 'pulling_ops' | 'downloading_media';
  pushedCount: number;
  pulledCount: number;
  conflictsCreated: number;
  mediaUploaded: number;
  mediaDownloaded: number;
}

export class SyncHttpError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(`${code}: ${message}`);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export class SyncHttpClient {
  private baseUrl: string;
  private accessToken: string;
  private fetchFn: typeof fetch;

  constructor(transport: SyncTransport) {
    this.baseUrl = transport.baseUrl.replace(/\/+$/, '');
    this.accessToken = transport.accessToken;
    this.fetchFn = transport.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  private async requestJson<T>(
    method: string,
    path: string,
    body?: unknown,
    requireAuth = true,
  ): Promise<T> {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (requireAuth && this.accessToken) headers['Authorization'] = `Bearer ${this.accessToken}`;
    const res = await this.fetchFn(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let parsed: Record<string, any> = {};
    if (text) {
      try {
        parsed = JSON.parse(text) as Record<string, any>;
      } catch {
        throw new SyncHttpError(
          res.status,
          `HTTP_${res.status}`,
          text.slice(0, 300),
        );
      }
    }
    if (!res.ok) {
      const errObj = parsed?.error ?? parsed;
      throw new SyncHttpError(
        res.status,
        errObj?.code ?? `HTTP_${res.status}`,
        errObj?.message ?? res.statusText,
        errObj?.details ?? {},
      );
    }
    return parsed as T;
  }

  async bootstrapVault(payload: {
    vault_id: string;
    device_id: string;
    device_name: string;
    device_type: 'desktop' | 'pwa' | 'mobile';
    public_key_x25519: string;
    vsk_recovery_envelope?: unknown;
  }): Promise<{ vault_id: string; device_id: string; device_access_token: string; current_key_epoch: number }> {
    return this.requestJson('POST', '/api/v1/vaults', payload, false);
  }

  async getVaultInfo(
    vaultId: string,
  ): Promise<{ vault_id: string; protocol_version: number; current_key_epoch: number; status: string }> {
    return this.requestJson('GET', `/api/v1/vaults/${vaultId}`);
  }

  async pushOperations(vaultId: string, operations: OperationEnvelope[]): Promise<BatchPushResponse> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/operations:batch`, { operations });
  }

  async pullOperations(vaultId: string, sinceSeq: string, limit = 200): Promise<PullOperationsResponse> {
    return this.requestJson(
      'GET',
      `/api/v1/vaults/${vaultId}/operations?since_seq=${encodeURIComponent(sinceSeq)}&limit=${limit}`,
    );
  }

  async listEpochKeys(vaultId: string): Promise<{ current_key_epoch: number; epochs: ServerEpochKeyRecord[] }> {
    return this.requestJson('GET', `/api/v1/vaults/${vaultId}/keys/epochs`);
  }

  async rotateEpoch(
    vaultId: string,
    payload: {
      new_key_epoch: number;
      reason: 'device_revoked' | 'manual';
      wrapped_keys: Record<string, string>;
    },
  ): Promise<{ current_key_epoch: number }> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/keys/epochs`, payload);
  }

  async listDevices(vaultId: string): Promise<{ devices: ServerDeviceInfo[] }> {
    return this.requestJson('GET', `/api/v1/vaults/${vaultId}/devices`);
  }

  async revokeDevice(vaultId: string, targetDeviceId: string): Promise<{ status: string; revoked_at: string }> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/devices/${targetDeviceId}/revoke`, {});
  }

  async createPairingSession(
    vaultId: string,
    payload: {
      pairing_id: string;
      device_a_id: string;
      pairing_nonce: string;
      expires_at: string;
    },
  ): Promise<{ pairing_id: string; status: string; expires_at: string }> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/pairing`, payload);
  }

  async joinPairingSession(
    vaultId: string,
    pairingId: string,
    payload: {
      device_b_id: string;
      device_b_name: string;
      device_b_type: 'desktop' | 'pwa' | 'mobile';
      device_b_public_key_x25519: string;
      session_nonce_b: string;
      secret_proof_hmac: string;
    },
  ): Promise<{ status: string; device_a_id: string; pairing_nonce: string }> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/pairing/${pairingId}/join`, payload, false);
  }

  async getPairingStatus(vaultId: string, pairingId: string): Promise<Record<string, unknown>> {
    return this.requestJson('GET', `/api/v1/vaults/${vaultId}/pairing/${pairingId}`, undefined, false);
  }

  async approvePairingSession(
    vaultId: string,
    pairingId: string,
    payload: {
      bundle_nonce: string;
      encrypted_vault_bundle: string;
    },
  ): Promise<{ status: string; provisional_device_token?: string }> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/pairing/${pairingId}/approve`, payload);
  }

  async confirmPairingSession(
    vaultId: string,
    pairingId: string,
    payload: {
      device_b_id: string;
      confirm_hmac: string;
    },
  ): Promise<{ status: string; device_access_token: string }> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/pairing/${pairingId}/confirm`, payload, false);
  }

  async initMediaUpload(
    vaultId: string,
    payload: {
      media_id: string;
      encrypted_size: number;
      encrypted_sha256: string;
      key_epoch: number;
      chunk_size: number;
    },
  ): Promise<{ media_id: string; status: string; received_bytes: number }> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/media/uploads`, payload);
  }

  async uploadMediaChunk(
    vaultId: string,
    mediaId: string,
    chunk: Uint8Array,
    startOffset: number,
    totalSize: number,
  ): Promise<{ received_bytes: number }> {
    const endOffset = startOffset + chunk.length - 1;
    const bodySlice = chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength) as ArrayBuffer;
    const res = await this.fetchFn(`${this.baseUrl}/api/v1/vaults/${vaultId}/media/uploads/${mediaId}`, {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${this.accessToken}`,
        'Content-Type': 'application/octet-stream',
        'Content-Range': `bytes ${startOffset}-${endOffset}/${totalSize}`,
      },
      body: bodySlice,
    });
    const text = await res.text();
    const parsed = text ? JSON.parse(text) : {};
    if (!res.ok) {
      const errObj = parsed?.error ?? parsed;
      throw new SyncHttpError(res.status, errObj?.code ?? `HTTP_${res.status}`, errObj?.message ?? res.statusText);
    }
    return parsed as { received_bytes: number };
  }

  async completeMediaUpload(
    vaultId: string,
    mediaId: string,
  ): Promise<{ media_id: string; status: string; encrypted_sha256: string }> {
    return this.requestJson('POST', `/api/v1/vaults/${vaultId}/media/uploads/${mediaId}/complete`, {});
  }

  async downloadMediaBlob(vaultId: string, mediaId: string): Promise<Uint8Array> {
    const res = await this.fetchFn(`${this.baseUrl}/api/v1/vaults/${vaultId}/media/${mediaId}`, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${this.accessToken}`,
      },
    });
    if (!res.ok) {
      throw new SyncHttpError(res.status, `HTTP_${res.status}`, res.statusText);
    }
    const buf = await res.arrayBuffer();
    return new Uint8Array(buf);
  }
}

export function buildKeyringFromVsk(
  vaultId: string,
  deviceId: string,
  vsk: Uint8Array,
  currentKeyEpoch = 1,
  deviceKeypair?: { publicKey: Uint8Array; privateKey: Uint8Array },
  extraEpochs?: Record<number, Uint8Array>,
): SyncVaultKeyring {
  const epochRootKeys = new Map<number, Uint8Array>();
  epochRootKeys.set(1, deriveInitialEpoch1RootKey(vsk, vaultId));
  if (extraEpochs) {
    for (const [epStr, key] of Object.entries(extraEpochs)) {
      epochRootKeys.set(Number(epStr), key);
    }
  }
  return {
    vaultId,
    deviceId,
    currentKeyEpoch,
    devicePublicKey: deviceKeypair?.publicKey,
    devicePrivateKey: deviceKeypair?.privateKey,
    epochRootKeys,
  };
}

export class PwaSyncEngine {
  private client: SyncHttpClient;
  private keyring: SyncVaultKeyring;

  constructor(client: SyncHttpClient, keyring: SyncVaultKeyring) {
    this.client = client;
    this.keyring = keyring;
  }

  private async ensureEpochRootKey(epoch: number): Promise<Uint8Array> {
    const cached = this.keyring.epochRootKeys.get(epoch);
    if (cached) return cached;

    if (!this.keyring.devicePublicKey || !this.keyring.devicePrivateKey) {
      throw new Error(`Missing EpochRootKey for epoch ${epoch} and no device X25519 keypair to unseal`);
    }
    const res = await this.client.listEpochKeys(this.keyring.vaultId);
    this.keyring.currentKeyEpoch = res.current_key_epoch;
    for (const rec of res.epochs) {
      if (rec.wrapped_epoch_key && !this.keyring.epochRootKeys.has(rec.key_epoch)) {
        const unsealed = await unsealEpochKeyForDevice(
          this.keyring.devicePublicKey,
          this.keyring.devicePrivateKey,
          rec.wrapped_epoch_key,
          {
            vault_id: this.keyring.vaultId,
            key_epoch: rec.key_epoch,
            target_device_id: this.keyring.deviceId,
          },
        );
        this.keyring.epochRootKeys.set(rec.key_epoch, unsealed);
      }
    }
    const found = this.keyring.epochRootKeys.get(epoch);
    if (!found) {
      throw new Error(`Unable to obtain EpochRootKey for epoch ${epoch}`);
    }
    return found;
  }

  async syncNow(options: { downloadMedia?: boolean; onProgress?: (p: SyncProgress) => void } = {}): Promise<SyncProgress> {
    const progress: SyncProgress = {
      phase: 'idle',
      pushedCount: 0,
      pulledCount: 0,
      conflictsCreated: 0,
      mediaUploaded: 0,
      mediaDownloaded: 0,
    };
    const emit = () => options.onProgress?.({ ...progress });

    // Ensure keyring has latest epoch keys before pushing or pulling
    await this.refreshEpochKeysFromServer();

    // 1. Push pending media + operations
    progress.phase = 'pushing_media';
    emit();
    await this.pushPendingMedia(progress, emit);

    progress.phase = 'pushing_ops';
    emit();
    await this.pushPendingOperations(progress, emit);

    // 2. Pull remote operations and apply via DAG + dependency queue
    progress.phase = 'pulling_ops';
    emit();
    await this.pullAndApplyOperations(progress, emit);

    // 3. Optionally download pending media blobs
    if (options.downloadMedia !== false) {
      progress.phase = 'downloading_media';
      emit();
      await this.downloadPendingMedia(progress, emit);
    }

    progress.phase = 'idle';
    emit();
    return progress;
  }

  async refreshEpochKeysFromServer(): Promise<void> {
    if (!this.keyring.devicePublicKey || !this.keyring.devicePrivateKey) return;
    const res = await this.client.listEpochKeys(this.keyring.vaultId);
    this.keyring.currentKeyEpoch = res.current_key_epoch;
    await db.sync_state.put({ key: 'current_key_epoch', value: String(res.current_key_epoch) });
    for (const rec of res.epochs) {
      if (rec.wrapped_epoch_key && !this.keyring.epochRootKeys.has(rec.key_epoch)) {
        const key = await unsealEpochKeyForDevice(
          this.keyring.devicePublicKey,
          this.keyring.devicePrivateKey,
          rec.wrapped_epoch_key,
          {
            vault_id: this.keyring.vaultId,
            key_epoch: rec.key_epoch,
            target_device_id: this.keyring.deviceId,
          },
        );
        this.keyring.epochRootKeys.set(rec.key_epoch, key);
      }
    }
  }

  private async pushPendingMedia(progress: SyncProgress, emit: () => void): Promise<void> {
    const pendingOps = (await db.sync_operations.where('status').equals('pending').toArray())
      .filter(op => op.operation_type === 'media.register')
      .sort((a, b) => Number(BigInt(a.device_seq) - BigInt(b.device_seq)));

    for (const op of pendingOps) {
      const att = op.media_local_attachment_id
        ? await db.attachments.get(op.media_local_attachment_id)
        : await db.attachments.where('sync_id').equals(op.entity_id).first();
      if (!att || !att.media_id || att.media_status === 'uploaded') continue;

      const epoch = this.keyring.currentKeyEpoch;
      const epochRoot = await this.ensureEpochRootKey(epoch);
      const { kMediaEpoch } = deriveEpochKeys(epochRoot, this.keyring.vaultId, epoch);
      const kMedia = deriveMediaObjectKey(kMediaEpoch, att.media_id);

      const plainBytes = new Uint8Array(await att.data.arrayBuffer());
      const encrypted = await encryptMediaStream(kMedia, att.media_id, epoch, plainBytes);
      const encryptedSha256 = sha256Hex(encrypted);
      const uploadChunkSize = 1024 * 1024; // 1 MiB HTTP transport chunks

      const initRes = await this.client.initMediaUpload(this.keyring.vaultId, {
        media_id: att.media_id,
        encrypted_size: encrypted.length,
        encrypted_sha256: encryptedSha256,
        key_epoch: epoch,
        chunk_size: uploadChunkSize,
      });

      if (initRes.status !== 'completed') {
        let offset = initRes.received_bytes || 0;
        while (offset < encrypted.length) {
          const end = Math.min(offset + uploadChunkSize, encrypted.length);
          await this.client.uploadMediaChunk(
            this.keyring.vaultId,
            att.media_id,
            encrypted.subarray(offset, end),
            offset,
            encrypted.length,
          );
          offset = end;
        }
        await this.client.completeMediaUpload(this.keyring.vaultId, att.media_id);
      }

      const updatedPayload = {
        ...op.payload_data,
        key_epoch: epoch,
        encrypted_size: encrypted.length,
        encrypted_sha256: encryptedSha256,
      };
      await db.attachments.update(att.id!, {
        key_epoch: epoch,
        encrypted_size: encrypted.length,
        encrypted_sha256: encryptedSha256,
        media_status: 'uploaded',
      });
      await db.sync_operations.update(op.id!, {
        key_epoch: epoch,
        payload_data: updatedPayload,
      });

      progress.mediaUploaded++;
      emit();
    }
  }

  private async pushPendingOperations(progress: SyncProgress, emit: () => void): Promise<void> {
    const pending = (await db.sync_operations.where('status').equals('pending').toArray()).sort((a, b) =>
      BigInt(a.device_seq) < BigInt(b.device_seq) ? -1 : BigInt(a.device_seq) > BigInt(b.device_seq) ? 1 : 0,
    );
    if (!pending.length) return;

    const epoch = this.keyring.currentKeyEpoch;
    const epochRoot = await this.ensureEpochRootKey(epoch);
    const { kOp } = deriveEpochKeys(epochRoot, this.keyring.vaultId, epoch);

    for (let i = 0; i < pending.length; i += 100) {
      const slice = pending.slice(i, i + 100);
      const envelopes: OperationEnvelope[] = [];
      for (const op of slice) {
        const payload: DecryptedOperationPayload = {
          payload_version: 1,
          operation_type: op.operation_type,
          entity_type: op.entity_type,
          entity_id: op.entity_id,
          revision_id: op.revision_id,
          parent_revision_ids: op.parent_revision_ids,
          created_at: op.created_at,
          data: op.payload_data,
        };
        const env = await encryptOperationEnvelope(kOp, {
          vault_id: this.keyring.vaultId,
          device_id: this.keyring.deviceId,
          operation_id: op.operation_id,
          device_seq: op.device_seq,
          key_epoch: epoch,
        }, payload);
        envelopes.push(env);
      }

      const res = await this.client.pushOperations(this.keyring.vaultId, envelopes);
      const ackMap = new Map(res.accepted.map(a => [a.operation_id, a.server_seq]));
      await db.transaction('rw', db.sync_operations, async () => {
        for (const op of slice) {
          const seq = ackMap.get(op.operation_id);
          if (seq !== undefined) {
            await db.sync_operations.update(op.id!, {
              status: 'acked',
              key_epoch: epoch,
              server_seq: seq,
            });
            progress.pushedCount++;
          }
        }
      });
      emit();
    }
  }

  private async pullAndApplyOperations(progress: SyncProgress, emit: () => void): Promise<void> {
    let cursor = (await db.sync_state.get('last_server_seq'))?.value ?? '0';
    let lastSeenSeq = BigInt(cursor);
    while (true) {
      const page = await this.client.pullOperations(this.keyring.vaultId, cursor, 200);
      if (page.operations.length > 0) {
        for (const env of page.operations) {
          if (env.vault_id !== this.keyring.vaultId) {
            throw new Error('Pulled operation vault_id mismatch');
          }
          if (!env.server_seq || !/^[1-9][0-9]*$/.test(env.server_seq)) {
            throw new Error(`Invalid server_seq in pulled operation: ${env.server_seq}`);
          }
          const srvSeq = BigInt(env.server_seq);
          if (srvSeq <= lastSeenSeq) {
            throw new Error(`Non-monotonic server_seq ${env.server_seq} after ${lastSeenSeq.toString()}`);
          }
          lastSeenSeq = srvSeq;
          if (env.device_id === this.keyring.deviceId) {
            progress.pulledCount++;
            continue;
          }
          const epochRoot = await this.ensureEpochRootKey(env.key_epoch);
          const { kOp } = deriveEpochKeys(epochRoot, this.keyring.vaultId, env.key_epoch);
          // Validate server_seq & envelope integrity via decryptOperationEnvelope
          const payload = await decryptOperationEnvelope(kOp, env);
          const appliedOrConflict = await this.applyDecryptedOperation(
            env.operation_id,
            env.server_seq,
            env.device_id,
            payload,
          );
          if (appliedOrConflict === 'conflict') {
            progress.conflictsCreated++;
          }
          progress.pulledCount++;
        }
        await this.drainPendingDeps(progress);
      }
      cursor = page.next_since_seq;
      await db.sync_state.put({ key: 'last_server_seq', value: cursor });
      emit();
      if (!page.has_more) break;
    }
  }

  async applyDecryptedOperation(
    operationId: string,
    serverSeq: string,
    remoteDeviceId: string,
    payload: DecryptedOperationPayload,
  ): Promise<'applied' | 'skipped' | 'deferred' | 'conflict'> {
    return db.transaction(
      'rw',
      [
        db.notes,
        db.categories,
        db.folders,
        db.tags,
        db.note_tags,
        db.attachments,
        db.sync_revisions,
        db.sync_conflicts,
        db.sync_pending_ops,
        db.sync_tombstones,
        db.sync_state,
      ],
      async () => {
        // 1. Idempotency check on exact revision_id
        const alreadyRev = await db.sync_revisions
          .where('[entity_type+entity_id+revision_id]')
          .equals([payload.entity_type, payload.entity_id, payload.revision_id])
          .first();
        if (alreadyRev) {
          return 'skipped';
        }

        // 2. Check foreign dependencies
        const missingDeps = await this.findMissingDependenciesInTx(payload);
        if (missingDeps.length > 0) {
          const existsPending = await db.sync_pending_ops.where('operation_id').equals(operationId).first();
          if (!existsPending) {
            await db.sync_pending_ops.add({
              operation_id: operationId,
              server_seq: serverSeq,
              device_id: remoteDeviceId,
              entity_type: payload.entity_type,
              entity_id: payload.entity_id,
              missing_deps: missingDeps,
              decrypted_payload: payload as unknown as Record<string, unknown>,
              received_at: now(),
            });
          } else {
            await db.sync_pending_ops.update(existsPending.id!, { missing_deps: missingDeps });
          }
          return 'deferred';
        }

        // 3. Check DAG concurrency against local heads
        const localHeads = await db.sync_revisions
          .where('[entity_type+entity_id+is_head]')
          .equals([payload.entity_type, payload.entity_id, 1])
          .toArray();
        const localHeadIds = localHeads.map(h => h.revision_id);
        const parentSet = new Set(payload.parent_revision_ids);
        const isFastForward =
          localHeadIds.length === 0 || localHeadIds.every(h => parentSet.has(h));

        if (!isFastForward) {
          return this.handleConcurrentOperationInTx(remoteDeviceId, localHeadIds, payload);
        }

        await this.applyFastForwardInTx(remoteDeviceId, localHeads, payload);
        return 'applied';
      },
    );
  }

  private async findMissingDependenciesInTx(payload: DecryptedOperationPayload): Promise<string[]> {
    const missing: string[] = [];
    const data = payload.data as Record<string, unknown>;
    if (payload.operation_type === 'entity.delete' || payload.operation_type === 'media.delete') {
      return missing;
    }
    if (payload.entity_type === 'note') {
      if (typeof data.category_id === 'string') {
        const c = await db.categories.where('sync_id').equals(data.category_id).first();
        if (!c) missing.push(`category:${data.category_id}`);
      }
      if (typeof data.folder_id === 'string') {
        const f = await db.folders.where('sync_id').equals(data.folder_id).first();
        if (!f) missing.push(`folder:${data.folder_id}`);
      }
    } else if (payload.entity_type === 'category') {
      if (typeof data.parent_id === 'string') {
        const p = await db.categories.where('sync_id').equals(data.parent_id).first();
        if (!p) missing.push(`category:${data.parent_id}`);
      }
    } else if (payload.entity_type === 'folder') {
      if (typeof data.parent_id === 'string') {
        const p = await db.folders.where('sync_id').equals(data.parent_id).first();
        if (!p) missing.push(`folder:${data.parent_id}`);
      }
    } else if (payload.entity_type === 'relation') {
      const nId = data.note_id as string | undefined;
      const tId = data.tag_id as string | undefined;
      if (nId && !(await db.notes.where('sync_id').equals(nId).first())) {
        missing.push(`note:${nId}`);
      }
      if (tId && !(await db.tags.where('sync_id').equals(tId).first())) {
        missing.push(`tag:${tId}`);
      }
    } else if (payload.entity_type === 'attachment') {
      const nId = data.note_id as string | undefined;
      if (nId && !(await db.notes.where('sync_id').equals(nId).first())) {
        missing.push(`note:${nId}`);
      }
    }
    return missing;
  }

  private async handleConcurrentOperationInTx(
    remoteDeviceId: string,
    localHeadIds: string[],
    payload: DecryptedOperationPayload,
  ): Promise<'applied' | 'conflict'> {
    // Tags with same normalized name auto-merge by smallest UUID rule
    if (payload.entity_type === 'relation') {
      const heads = await db.sync_revisions
        .where('[entity_type+entity_id+is_head]')
        .equals(['relation', payload.entity_id, 1])
        .toArray();
      await this.applyFastForwardInTx(remoteDeviceId, heads, payload);
      return 'applied';
    }

    if (payload.entity_type === 'note') {
      const localNote = await db.notes.where('sync_id').equals(payload.entity_id).first();
      const tomb = await db.sync_tombstones.get(`note:${payload.entity_id}`);
      const localCat = localNote?.category_id ? (await db.categories.get(localNote.category_id))?.sync_id ?? null : null;
      const localFld = localNote?.folder_id ? (await db.folders.get(localNote.folder_id))?.sync_id ?? null : null;
      const localSnap: Record<string, unknown> = localNote
        ? {
            title: localNote.title,
            content: localNote.content,
            category_id: localCat,
            folder_id: localFld,
            is_deleted: localNote.is_deleted,
            deleted_at: localNote.deleted_at ?? null,
            updated_at: localNote.updated_at,
          }
        : {
            deleted: true,
            deleted_at: tomb?.deleted_at ?? null,
          };

      const d = payload.data as Record<string, unknown>;
      const remoteSnap: Record<string, unknown> =
        payload.operation_type === 'entity.delete'
          ? { deleted: true, deleted_at: d.deleted_at ?? payload.created_at }
          : {
              title: String(d.title ?? ''),
              content: String(d.content ?? ''),
              category_id: (d.category_id as string | null) ?? null,
              folder_id: (d.folder_id as string | null) ?? null,
              is_deleted: payload.operation_type === 'entity.trash' ? true : Boolean(d.is_deleted),
              deleted_at: (d.deleted_at as string | null) ?? null,
              updated_at: normalizeUtcTimestamp((d.updated_at as string | undefined) ?? payload.created_at),
            };

      // Check semantic equality for silent convergence
      if (
        localNote &&
        payload.operation_type !== 'entity.delete' &&
        localSnap.title === remoteSnap.title &&
        localSnap.content === remoteSnap.content &&
        localSnap.category_id === remoteSnap.category_id &&
        localSnap.folder_id === remoteSnap.folder_id &&
        localSnap.is_deleted === remoteSnap.is_deleted
      ) {
        await db.sync_revisions.put({
          entity_type: 'note',
          entity_id: payload.entity_id,
          revision_id: payload.revision_id,
          parent_revision_ids: payload.parent_revision_ids,
          is_head: 1,
          operation_type: payload.operation_type,
          created_at: payload.created_at,
          device_id: remoteDeviceId,
        });
        return 'applied';
      }

      // Record explicit conflict (do NOT overwrite local note silently!)
      await db.sync_revisions.put({
        entity_type: 'note',
        entity_id: payload.entity_id,
        revision_id: payload.revision_id,
        parent_revision_ids: payload.parent_revision_ids,
        is_head: 1,
        operation_type: payload.operation_type,
        created_at: payload.created_at,
        device_id: remoteDeviceId,
      });

      await db.sync_conflicts.add({
        conflict_id: generateUuidV4(),
        entity_type: 'note',
        entity_id: payload.entity_id,
        local_revision_id: localHeadIds[0] ?? '',
        remote_revision_id: payload.revision_id,
        local_snapshot: localSnap,
        remote_snapshot: remoteSnap,
        remote_device_id: remoteDeviceId,
        detected_at: now(),
        status: 'open',
      });
      return 'conflict';
    }

    // Category / Folder conflict
    await db.sync_revisions.put({
      entity_type: payload.entity_type,
      entity_id: payload.entity_id,
      revision_id: payload.revision_id,
      parent_revision_ids: payload.parent_revision_ids,
      is_head: 1,
      operation_type: payload.operation_type,
      created_at: payload.created_at,
      device_id: remoteDeviceId,
    });
    await db.sync_conflicts.add({
      conflict_id: generateUuidV4(),
      entity_type: payload.entity_type,
      entity_id: payload.entity_id,
      local_revision_id: localHeadIds[0] ?? '',
      remote_revision_id: payload.revision_id,
      local_snapshot: { note: 'Concurrent metadata update' },
      remote_snapshot: payload.data,
      remote_device_id: remoteDeviceId,
      detected_at: now(),
      status: 'open',
    });
    return 'conflict';
  }

  private async applyFastForwardInTx(
    remoteDeviceId: string,
    prevHeads: Array<{ id?: number }>,
    payload: DecryptedOperationPayload,
  ): Promise<void> {
    for (const h of prevHeads) {
      if (h.id) await db.sync_revisions.update(h.id, { is_head: 0 });
    }
    await db.sync_revisions.put({
      entity_type: payload.entity_type,
      entity_id: payload.entity_id,
      revision_id: payload.revision_id,
      parent_revision_ids: payload.parent_revision_ids,
      is_head: 1,
      operation_type: payload.operation_type,
      created_at: payload.created_at,
      device_id: remoteDeviceId,
    });

    const d = payload.data as Record<string, unknown>;

    if (payload.entity_type === 'category') {
      const existing = await db.categories.where('sync_id').equals(payload.entity_id).first();
      if (payload.operation_type === 'entity.delete') {
        if (existing?.id) {
          await db.notes.where('category_id').equals(existing.id).modify({ category_id: undefined });
          await db.categories.delete(existing.id);
        }
        await db.sync_tombstones.put({
          entity_key: `category:${payload.entity_id}`,
          entity_type: 'category',
          entity_id: payload.entity_id,
          revision_id: payload.revision_id,
          deleted_at: (d.deleted_at as string) || payload.created_at,
        });
        return;
      }
      const parentLocal =
        typeof d.parent_id === 'string'
          ? (await db.categories.where('sync_id').equals(d.parent_id).first())?.id
          : undefined;
      const obj: Category = {
        sync_id: payload.entity_id,
        revision_id: payload.revision_id,
        parent_revision_ids: payload.parent_revision_ids,
        name: String(d.name ?? ''),
        parent_id: parentLocal,
        color: String(d.color ?? '#6366F1'),
        sort_order: Number(d.sort_order ?? 0),
        created_at: normalizeUtcTimestamp((d.created_at as string) ?? payload.created_at),
        updated_at: normalizeUtcTimestamp((d.updated_at as string) ?? payload.created_at),
      };
      if (existing?.id) await db.categories.update(existing.id, obj);
      else await db.categories.add(obj);
      return;
    }

    if (payload.entity_type === 'folder') {
      const existing = await db.folders.where('sync_id').equals(payload.entity_id).first();
      if (payload.operation_type === 'entity.delete') {
        if (existing?.id) {
          await db.notes.where('folder_id').equals(existing.id).modify({ folder_id: undefined });
          await db.folders.delete(existing.id);
        }
        await db.sync_tombstones.put({
          entity_key: `folder:${payload.entity_id}`,
          entity_type: 'folder',
          entity_id: payload.entity_id,
          revision_id: payload.revision_id,
          deleted_at: (d.deleted_at as string) || payload.created_at,
        });
        return;
      }
      const parentLocal =
        typeof d.parent_id === 'string'
          ? (await db.folders.where('sync_id').equals(d.parent_id).first())?.id
          : undefined;
      const obj: Folder = {
        sync_id: payload.entity_id,
        revision_id: payload.revision_id,
        parent_revision_ids: payload.parent_revision_ids,
        name: String(d.name ?? ''),
        parent_id: parentLocal,
        created_at: normalizeUtcTimestamp((d.created_at as string) ?? payload.created_at),
        updated_at: normalizeUtcTimestamp((d.updated_at as string) ?? payload.created_at),
      };
      if (existing?.id) await db.folders.update(existing.id, obj);
      else await db.folders.add(obj);
      return;
    }

    if (payload.entity_type === 'tag') {
      const existing = await db.tags.where('sync_id').equals(payload.entity_id).first();
      if (payload.operation_type === 'entity.delete') {
        if (existing?.id) {
          await db.note_tags.where('tag_id').equals(existing.id).delete();
          await db.tags.delete(existing.id);
        }
        return;
      }
      const normName = normalizeTagName(String(d.name ?? ''));
      const byName = await db.tags.where('name').equalsIgnoreCase(normName).first();
      if (byName && byName.sync_id && byName.sync_id !== payload.entity_id) {
        // Deterministic UUID min tie-break merge
        const keepRemote = payload.entity_id < byName.sync_id;
        if (keepRemote) {
          await db.tags.update(byName.id!, {
            sync_id: payload.entity_id,
            revision_id: payload.revision_id,
            name: normName,
            color: String(d.color ?? byName.color),
          });
        }
        return;
      }
      const obj: Tag = {
        sync_id: payload.entity_id,
        revision_id: payload.revision_id,
        parent_revision_ids: payload.parent_revision_ids,
        name: normName,
        color: String(d.color ?? '#6366F1'),
        created_at: normalizeUtcTimestamp((d.created_at as string) ?? payload.created_at),
        updated_at: normalizeUtcTimestamp((d.updated_at as string) ?? payload.created_at),
      };
      if (existing?.id) await db.tags.update(existing.id, obj);
      else await db.tags.add(obj);
      return;
    }

    if (payload.entity_type === 'note') {
      const existing = await db.notes.where('sync_id').equals(payload.entity_id).first();
      if (payload.operation_type === 'entity.delete') {
        if (existing?.id) {
          await db.attachments.where('note_id').equals(existing.id).delete();
          await db.note_tags.where('note_id').equals(existing.id).delete();
          await db.notes.delete(existing.id);
        }
        await db.sync_tombstones.put({
          entity_key: `note:${payload.entity_id}`,
          entity_type: 'note',
          entity_id: payload.entity_id,
          revision_id: payload.revision_id,
          deleted_at: (d.deleted_at as string) || payload.created_at,
        });
        return;
      }

      const catLocal =
        typeof d.category_id === 'string'
          ? (await db.categories.where('sync_id').equals(d.category_id).first())?.id
          : undefined;
      const fldLocal =
        typeof d.folder_id === 'string'
          ? (await db.folders.where('sync_id').equals(d.folder_id).first())?.id
          : undefined;
      const isDeleted =
        payload.operation_type === 'entity.trash'
          ? true
          : payload.operation_type === 'entity.restore'
            ? false
            : Boolean(d.is_deleted);
      const deletedAt = isDeleted
        ? normalizeUtcTimestamp((d.deleted_at as string | undefined) ?? payload.created_at)
        : undefined;

      const noteObj: Note = {
        sync_id: payload.entity_id,
        revision_id: payload.revision_id,
        parent_revision_ids: payload.parent_revision_ids,
        title: String(d.title ?? ''),
        content: String(d.content ?? ''),
        category_id: catLocal,
        folder_id: fldLocal,
        created_at: normalizeUtcTimestamp((d.created_at as string) ?? existing?.created_at ?? payload.created_at),
        updated_at: normalizeUtcTimestamp((d.updated_at as string) ?? payload.created_at),
        is_deleted: isDeleted,
        deleted_at: deletedAt,
        metadata: (d.metadata as Record<string, unknown>) ?? {},
      };
      if (existing?.id) await db.notes.update(existing.id, noteObj);
      else await db.notes.add(noteObj);
      return;
    }

    if (payload.entity_type === 'relation') {
      const existing = await db.note_tags.where('sync_id').equals(payload.entity_id).first();
      if (payload.operation_type === 'entity.delete') {
        if (existing) await db.note_tags.delete([existing.note_id, existing.tag_id]);
        await db.sync_tombstones.put({
          entity_key: `relation:${payload.entity_id}`,
          entity_type: 'relation',
          entity_id: payload.entity_id,
          revision_id: payload.revision_id,
          deleted_at: (d.deleted_at as string) || payload.created_at,
        });
        return;
      }
      const note = await db.notes.where('sync_id').equals(String(d.note_id)).first();
      const tag = await db.tags.where('sync_id').equals(String(d.tag_id)).first();
      if (!note?.id || !tag?.id) return;
      const dupe = await db.note_tags.get([note.id, tag.id]);
      if (!dupe) {
        await db.note_tags.put({
          note_id: note.id,
          tag_id: tag.id,
          sync_id: payload.entity_id,
          revision_id: payload.revision_id,
          parent_revision_ids: payload.parent_revision_ids,
          created_at: normalizeUtcTimestamp((d.created_at as string) ?? payload.created_at),
        });
      }
      return;
    }

    if (payload.entity_type === 'attachment') {
      const existing = await db.attachments.where('sync_id').equals(payload.entity_id).first();
      if (payload.operation_type === 'media.delete' || payload.operation_type === 'entity.delete') {
        if (existing?.id) await db.attachments.delete(existing.id);
        await db.sync_tombstones.put({
          entity_key: `attachment:${payload.entity_id}`,
          entity_type: 'attachment',
          entity_id: payload.entity_id,
          revision_id: payload.revision_id,
          deleted_at: (d.deleted_at as string) || payload.created_at,
        });
        return;
      }
      const note = await db.notes.where('sync_id').equals(String(d.note_id)).first();
      if (!note?.id) return;
      const mediaType = (d.media_type as 'image' | 'audio' | 'video') ?? 'image';
      const attObj: Attachment = {
        sync_id: payload.entity_id,
        media_id: String(d.media_id),
        revision_id: payload.revision_id,
        parent_revision_ids: payload.parent_revision_ids,
        note_id: note.id,
        type: mediaType,
        filename: String(d.file_name ?? 'attachment'),
        data: existing?.data ?? new Blob([], { type: String(d.mime_type ?? 'application/octet-stream') }),
        thumbnail: (d.thumbnail_data_url as string | undefined) ?? undefined,
        duration: (d.duration as number | undefined) ?? undefined,
        mime_type: String(d.mime_type ?? 'application/octet-stream'),
        original_size: Number(d.original_size ?? 0),
        encrypted_size: Number(d.encrypted_size ?? 0),
        encrypted_sha256: String(d.encrypted_sha256 ?? ''),
        key_epoch: Number(d.key_epoch ?? 1),
        media_status: existing?.media_status === 'available' ? 'available' : 'pending_download',
        created_at: normalizeUtcTimestamp((d.created_at as string) ?? payload.created_at),
      };
      if (existing?.id) await db.attachments.update(existing.id, attObj);
      else await db.attachments.add(attObj);
    }
  }

  private async drainPendingDeps(progress: SyncProgress): Promise<void> {
    let madeProgress = true;
    while (madeProgress) {
      madeProgress = false;
      const pending: SyncPendingOpRecord[] = await db.sync_pending_ops.orderBy('id').toArray();
      for (const item of pending) {
        const res = await this.applyDecryptedOperation(
          item.operation_id,
          item.server_seq,
          item.device_id,
          item.decrypted_payload as unknown as DecryptedOperationPayload,
        );
        if (res !== 'deferred') {
          await db.sync_pending_ops.delete(item.id!);
          if (res === 'conflict') progress.conflictsCreated++;
          madeProgress = true;
        }
      }
    }
  }

  async downloadPendingMedia(progress: SyncProgress, emit: () => void): Promise<void> {
    const pendingAtts = await db.attachments.filter(a => a.media_status === 'pending_download').toArray();
    for (const att of pendingAtts) {
      if (!att.media_id) continue;
      const epoch = att.key_epoch ?? 1;
      const epochRoot = await this.ensureEpochRootKey(epoch);
      const { kMediaEpoch } = deriveEpochKeys(epochRoot, this.keyring.vaultId, epoch);
      const kMedia = deriveMediaObjectKey(kMediaEpoch, att.media_id);

      const encryptedBlob = await this.client.downloadMediaBlob(this.keyring.vaultId, att.media_id);
      if (att.encrypted_sha256) {
        const gotHash = sha256Hex(encryptedBlob);
        if (gotHash !== att.encrypted_sha256) {
          throw new Error(`Downloaded media SHA-256 mismatch for ${att.media_id}`);
        }
      }
      const plainBytes = await decryptMediaStream(kMedia, att.media_id, epoch, encryptedBlob);
      const ab = plainBytes.buffer.slice(plainBytes.byteOffset, plainBytes.byteOffset + plainBytes.byteLength) as ArrayBuffer;
      const blob = new Blob([ab], { type: att.mime_type || 'application/octet-stream' });
      await db.attachments.update(att.id!, {
        data: blob,
        original_size: plainBytes.length,
        media_status: 'available',
      });
      progress.mediaDownloaded++;
      emit();
    }
  }

  async resolveConflict(
    conflictId: string,
    choice: 'keep_local' | 'keep_remote' | 'keep_both' | 'manual_merge',
    manualData?: { title: string; content: string },
  ): Promise<void> {
    await db.transaction(
      'rw',
      [db.notes, db.categories, db.folders, db.sync_conflicts, db.sync_revisions, db.sync_operations, db.sync_state, db.sync_tombstones],
      async () => {
        const conflict = await db.sync_conflicts.where('conflict_id').equals(conflictId).first();
        if (!conflict || conflict.status !== 'open') return;
        const ts = now();
        const mergeParents = [conflict.local_revision_id, conflict.remote_revision_id].filter(Boolean);
        const epochRec = await db.sync_state.get('current_key_epoch');
        const keyEpoch = Number(epochRec?.value ?? '1') || 1;
        const seqRec = await db.sync_state.get('local_device_seq');
        const nextSeq = (BigInt(seqRec?.value ?? '0') + 1n).toString();
        await db.sync_state.put({ key: 'local_device_seq', value: nextSeq });

        if (conflict.entity_type === 'note') {
          const localNote = await db.notes.where('sync_id').equals(conflict.entity_id).first();
          const newRev = generateUuidV4();
          const prevHeads = await db.sync_revisions
            .where('[entity_type+entity_id+is_head]')
            .equals(['note', conflict.entity_id, 1])
            .toArray();
          for (const h of prevHeads) await db.sync_revisions.update(h.id!, { is_head: 0 });

          if (choice === 'keep_both') {
            // Create a duplicate note with remote content, keep local note as merged revision
            const rem = conflict.remote_snapshot;
            const dupSyncId = generateUuidV4();
            const dupRev = generateUuidV4();
            const dupTitle = `${String(rem.title ?? 'Note')} (conflit)`;
            const dupContent = String(rem.content ?? '');
            const dupCatLocal =
              typeof rem.category_id === 'string'
                ? (await db.categories.where('sync_id').equals(rem.category_id).first())?.id
                : undefined;
            const dupFldLocal =
              typeof rem.folder_id === 'string'
                ? (await db.folders.where('sync_id').equals(rem.folder_id).first())?.id
                : undefined;
            await db.notes.add({
              sync_id: dupSyncId,
              revision_id: dupRev,
              parent_revision_ids: [],
              title: dupTitle,
              content: dupContent,
              category_id: dupCatLocal,
              folder_id: dupFldLocal,
              created_at: ts,
              updated_at: ts,
              is_deleted: false,
              metadata: {},
            });
            await db.sync_revisions.put({
              entity_type: 'note',
              entity_id: dupSyncId,
              revision_id: dupRev,
              parent_revision_ids: [],
              is_head: 1,
              operation_type: 'entity.upsert',
              created_at: ts,
              device_id: this.keyring.deviceId,
            });
            await db.sync_operations.add({
              operation_id: generateUuidV4(),
              device_seq: nextSeq,
              key_epoch: keyEpoch,
              operation_type: 'entity.upsert',
              entity_type: 'note',
              entity_id: dupSyncId,
              revision_id: dupRev,
              parent_revision_ids: [],
              created_at: ts,
              payload_data: {
                title: dupTitle,
                content: dupContent,
                category_id: dupCatLocal ? (rem.category_id as string) : null,
                folder_id: dupFldLocal ? (rem.folder_id as string) : null,
                is_deleted: false,
                deleted_at: null,
                created_at: ts,
                updated_at: ts,
                metadata: {},
              },
              status: 'pending',
            });

            // Also record merge revision for original entity keeping local
            const seq2 = (BigInt(nextSeq) + 1n).toString();
            await db.sync_state.put({ key: 'local_device_seq', value: seq2 });
            if (localNote) {
              const locCatSync = localNote.category_id
                ? (await db.categories.get(localNote.category_id))?.sync_id ?? null
                : null;
              const locFldSync = localNote.folder_id
                ? (await db.folders.get(localNote.folder_id))?.sync_id ?? null
                : null;
              await db.notes.update(localNote.id!, { revision_id: newRev, parent_revision_ids: mergeParents, updated_at: ts });
              await db.sync_revisions.put({
                entity_type: 'note',
                entity_id: conflict.entity_id,
                revision_id: newRev,
                parent_revision_ids: mergeParents,
                is_head: 1,
                operation_type: 'entity.upsert',
                created_at: ts,
                device_id: this.keyring.deviceId,
              });
              await db.sync_operations.add({
                operation_id: generateUuidV4(),
                device_seq: seq2,
                key_epoch: keyEpoch,
                operation_type: 'entity.upsert',
                entity_type: 'note',
                entity_id: conflict.entity_id,
                revision_id: newRev,
                parent_revision_ids: mergeParents,
                created_at: ts,
                payload_data: {
                  title: localNote.title,
                  content: localNote.content,
                  category_id: locCatSync,
                  folder_id: locFldSync,
                  is_deleted: localNote.is_deleted,
                  deleted_at: localNote.deleted_at ?? null,
                  created_at: localNote.created_at,
                  updated_at: ts,
                  metadata: localNote.metadata ?? {},
                },
                status: 'pending',
              });
            }
          } else {
            const snap =
              choice === 'keep_local'
                ? conflict.local_snapshot
                : choice === 'keep_remote'
                  ? conflict.remote_snapshot
                  : { ...conflict.local_snapshot, ...manualData, is_deleted: false, deleted_at: null };

            const opType: SyncOperationType = snap.deleted
              ? 'entity.delete'
              : snap.is_deleted
                ? 'entity.trash'
                : 'entity.upsert';

            if (snap.deleted && localNote?.id) {
              await db.notes.delete(localNote.id);
              await db.sync_tombstones.put({
                entity_key: `note:${conflict.entity_id}`,
                entity_type: 'note',
                entity_id: conflict.entity_id,
                revision_id: newRev,
                deleted_at: ts,
              });
            } else {
              const title = String(snap.title ?? '');
              const content = String(snap.content ?? '');
              const isDeleted = Boolean(snap.is_deleted);
              const catLocal =
                typeof snap.category_id === 'string'
                  ? (await db.categories.where('sync_id').equals(snap.category_id).first())?.id
                  : undefined;
              const fldLocal =
                typeof snap.folder_id === 'string'
                  ? (await db.folders.where('sync_id').equals(snap.folder_id).first())?.id
                  : undefined;
              if (localNote?.id) {
                await db.notes.update(localNote.id, {
                  title,
                  content,
                  category_id: catLocal,
                  folder_id: fldLocal,
                  is_deleted: isDeleted,
                  deleted_at: isDeleted ? ts : undefined,
                  updated_at: ts,
                  revision_id: newRev,
                  parent_revision_ids: mergeParents,
                });
              } else {
                await db.notes.add({
                  sync_id: conflict.entity_id,
                  revision_id: newRev,
                  parent_revision_ids: mergeParents,
                  title,
                  content,
                  category_id: catLocal,
                  folder_id: fldLocal,
                  is_deleted: isDeleted,
                  deleted_at: isDeleted ? ts : undefined,
                  created_at: ts,
                  updated_at: ts,
                  metadata: {},
                });
              }
            }

            await db.sync_revisions.put({
              entity_type: 'note',
              entity_id: conflict.entity_id,
              revision_id: newRev,
              parent_revision_ids: mergeParents,
              is_head: 1,
              operation_type: opType,
              created_at: ts,
              device_id: this.keyring.deviceId,
            });

            await db.sync_operations.add({
              operation_id: generateUuidV4(),
              device_seq: nextSeq,
              key_epoch: keyEpoch,
              operation_type: opType,
              entity_type: 'note',
              entity_id: conflict.entity_id,
              revision_id: newRev,
              parent_revision_ids: mergeParents,
              created_at: ts,
              payload_data:
                opType === 'entity.delete'
                  ? { deleted_at: ts }
                  : {
                      title: String(snap.title ?? ''),
                      content: String(snap.content ?? ''),
                      category_id: (snap.category_id as string | null) ?? null,
                      folder_id: (snap.folder_id as string | null) ?? null,
                      is_deleted: Boolean(snap.is_deleted),
                      deleted_at: snap.is_deleted ? ts : null,
                      created_at: localNote?.created_at ?? ts,
                      updated_at: ts,
                      metadata: localNote?.metadata ?? {},
                    },
              status: 'pending',
            });
          }
        }

        await db.sync_conflicts.update(conflict.id!, {
          status: 'resolved',
          resolution: choice,
          resolved_at: ts,
        });
      },
    );
  }

  /**
   * Revoke another active device and rotate to key_epoch + 1 by wrapping the fresh
   * EpochRootKey for every remaining active device using X25519 crypto_box_seal.
   */
  async revokeDeviceAndRotateEpoch(targetDeviceId: string): Promise<number> {
    await this.client.revokeDevice(this.keyring.vaultId, targetDeviceId);
    const { devices } = await this.client.listDevices(this.keyring.vaultId);
    const activeDevices = devices.filter(d => d.status === 'active' && d.device_id !== targetDeviceId);
    const newEpoch = this.keyring.currentKeyEpoch + 1;
    const newEpochRootKey = crypto.getRandomValues(new Uint8Array(32));

    const wrappedKeys: Record<string, string> = {};
    for (const dev of activeDevices) {
      const pub = fromBase64Url(dev.public_key_x25519);
      wrappedKeys[dev.device_id] = await sealEpochKeyForDevice(pub, {
        vault_id: this.keyring.vaultId,
        key_epoch: newEpoch,
        target_device_id: dev.device_id,
        epoch_root_key: newEpochRootKey,
      });
    }

    const res = await this.client.rotateEpoch(this.keyring.vaultId, {
      new_key_epoch: newEpoch,
      reason: 'device_revoked',
      wrapped_keys: wrappedKeys,
    });
    this.keyring.epochRootKeys.set(res.current_key_epoch, newEpochRootKey);
    this.keyring.currentKeyEpoch = res.current_key_epoch;
    await db.sync_state.put({ key: 'current_key_epoch', value: String(res.current_key_epoch) });
    return res.current_key_epoch;
  }
}

// Re-export helpers convenient for worker/pairing flows
export {
  buildOperationAad,
  deriveInitialEpoch1RootKey,
  fromBase64Url,
  toBase64Url,
};
export type { DecryptedOperationPayload };
