import { db, enqueueExistingVaultSnapshot } from '../db';
import {
  ARGON2ID_ITERATIONS,
  ARGON2ID_MEM_KIB,
  ARGON2ID_PARALLELISM,
  aeadDecrypt,
  aeadEncrypt,
  canonicalJsonBytes,
  computePairingConfirmHmac,
  computeSecretProofHmac,
  decryptPairingBundle,
  decryptVskEnvelope,
  deriveArgon2idKek,
  deriveInitialEpoch1RootKey,
  derivePairingKey,
  encryptPairingBundle,
  encryptVskEnvelope,
  ensureCryptoReady,
  fromBase64Url,
  generateDeviceKeypair,
  generateUuidV4,
  hkdfSha256,
  randomBytes,
  toBase64Url,
  utf8ToBytes,
  uuidToBytes,
  type PairingBundlePayload,
  type VskRecoveryEnvelope,
} from './crypto';
import {
  PwaSyncEngine,
  SyncHttpClient,
  buildKeyringFromVsk,
  type SyncVaultKeyring,
} from './engine';

export interface UnlockedVaultSession {
  vaultId: string;
  deviceId: string;
  serverUrl: string;
  accessToken: string;
  vsk: Uint8Array;
  devicePublicKey: Uint8Array;
  devicePrivateKey: Uint8Array;
  keyring: SyncVaultKeyring;
  autoLockMinutes: number;
  unlockedAt: number;
}

export interface QrPairingPayload {
  v: 1;
  server_url: string;
  vault_id: string;
  pairing_id: string;
  pairing_secret: string;
  expires_at: string;
}

interface WrappedDeviceKeys {
  public_key_b64u: string;
  nonce_b64u: string;
  ciphertext_b64u: string;
}

let activeSession: UnlockedVaultSession | null = null;
let inactivityTimer: ReturnType<typeof setTimeout> | null = null;
const sessionListeners = new Set<() => void>();

function notifyListeners(): void {
  for (const fn of sessionListeners) fn();
}

export function subscribeVaultSession(fn: () => void): () => void {
  sessionListeners.add(fn);
  return () => sessionListeners.delete(fn);
}

export function getActiveVaultSession(): UnlockedVaultSession | null {
  return activeSession;
}

export function touchVaultActivity(): void {
  if (!activeSession) return;
  if (inactivityTimer) clearTimeout(inactivityTimer);
  const mins = activeSession.autoLockMinutes;
  if (mins > 0 && typeof setTimeout === 'function') {
    inactivityTimer = setTimeout(() => {
      lockVaultNow();
    }, mins * 60 * 1000);
  }
}

export function lockVaultNow(): void {
  if (inactivityTimer) {
    clearTimeout(inactivityTimer);
    inactivityTimer = null;
  }
  if (activeSession) {
    activeSession.vsk.fill(0);
    activeSession.devicePrivateKey.fill(0);
    for (const k of activeSession.keyring.epochRootKeys.values()) {
      k.fill(0);
    }
    activeSession = null;
  }
  notifyListeners();
}

/**
 * Derive Argon2id KEK inside a Web Worker when supported, falling back to inline WASM cleanly.
 */
export async function deriveArgon2idInWorker(
  passwordUtf8: string,
  salt16: Uint8Array,
  memoryKib = ARGON2ID_MEM_KIB,
  iterations = ARGON2ID_ITERATIONS,
  parallelism = ARGON2ID_PARALLELISM,
): Promise<Uint8Array> {
  if (typeof Worker !== 'undefined') {
    try {
      return await new Promise<Uint8Array>((resolve, reject) => {
        const worker = new Worker(new URL('./crypto.worker.ts', import.meta.url), { type: 'module' });
        const saltCopy = salt16.buffer.slice(salt16.byteOffset, salt16.byteOffset + salt16.byteLength) as ArrayBuffer;
        worker.onmessage = ev => {
          worker.terminate();
          if (ev.data.error) reject(new Error(ev.data.error));
          else resolve(new Uint8Array(ev.data.kek));
        };
        worker.onerror = err => {
          worker.terminate();
          reject(new Error(err.message || 'Worker execution failed'));
        };
        worker.postMessage(
          { id: 1, passwordUtf8, salt16: saltCopy, memoryKib, iterations, parallelism },
          [saltCopy],
        );
      });
    } catch {
      // Fallback inline if Web Worker blocked or running in test sandbox
    }
  }
  return deriveArgon2idKek(passwordUtf8, salt16, memoryKib, iterations, parallelism);
}

function deriveDeviceKeyWrapKey(vsk: Uint8Array, vaultId: string): Uint8Array {
  return hkdfSha256(vsk, uuidToBytes(vaultId), utf8ToBytes('NOTEOR/SYNC/V1/LOCAL-DEVICE-KEYWRAP'), 32);
}

async function sealLocalDeviceKeypair(
  vsk: Uint8Array,
  vaultId: string,
  kp: { publicKey: Uint8Array; privateKey: Uint8Array },
): Promise<WrappedDeviceKeys> {
  const wrapKey = deriveDeviceKeyWrapKey(vsk, vaultId);
  try {
    const aad = canonicalJsonBytes({ purpose: 'local-device-x25519', vault_id: vaultId });
    const { nonce, ciphertext } = await aeadEncrypt(wrapKey, kp.privateKey, aad);
    return {
      public_key_b64u: toBase64Url(kp.publicKey),
      nonce_b64u: toBase64Url(nonce),
      ciphertext_b64u: toBase64Url(ciphertext),
    };
  } finally {
    wrapKey.fill(0);
  }
}

async function unsealLocalDeviceKeypair(
  vsk: Uint8Array,
  vaultId: string,
  wrapped: WrappedDeviceKeys,
): Promise<{ publicKey: Uint8Array; privateKey: Uint8Array }> {
  const wrapKey = deriveDeviceKeyWrapKey(vsk, vaultId);
  try {
    const aad = canonicalJsonBytes({ purpose: 'local-device-x25519', vault_id: vaultId });
    const priv = await aeadDecrypt(
      wrapKey,
      fromBase64Url(wrapped.nonce_b64u),
      fromBase64Url(wrapped.ciphertext_b64u),
      aad,
    );
    return {
      publicKey: fromBase64Url(wrapped.public_key_b64u),
      privateKey: priv,
    };
  } finally {
    wrapKey.fill(0);
  }
}

export async function getVaultConfigSummary(): Promise<{
  configured: boolean;
  vaultId?: string;
  deviceId?: string;
  serverUrl?: string;
  currentKeyEpoch?: number;
  autoLockMinutes: number;
  deviceName?: string;
}> {
  const rows = await db.sync_state.toArray();
  const map = new Map(rows.map(r => [r.key, r.value]));
  const vaultId = map.get('vault_id');
  return {
    configured: Boolean(vaultId && map.get('vault_vsk_envelope')),
    vaultId,
    deviceId: map.get('device_id'),
    serverUrl: map.get('server_url'),
    currentKeyEpoch: Number(map.get('current_key_epoch') ?? '1') || 1,
    autoLockMinutes: Number(map.get('auto_lock_minutes') ?? '15') || 15,
    deviceName: map.get('device_name'),
  };
}

export async function setAutoLockMinutes(minutes: number): Promise<void> {
  await db.sync_state.put({ key: 'auto_lock_minutes', value: String(minutes) });
  if (activeSession) {
    activeSession.autoLockMinutes = minutes;
    touchVaultActivity();
  }
}

export async function initializeNewVault(params: {
  serverUrl: string;
  deviceName: string;
  masterPassword: string;
  fetchImpl?: typeof fetch;
}): Promise<UnlockedVaultSession> {
  await ensureCryptoReady();
  const vaultId = generateUuidV4();
  const deviceId = generateUuidV4();
  const vsk = randomBytes(32);
  const kp = await generateDeviceKeypair();
  const vskEnvelope = await encryptVskEnvelope(vsk, vaultId, params.masterPassword);
  const wrappedKp = await sealLocalDeviceKeypair(vsk, vaultId, kp);

  const client = new SyncHttpClient({
    baseUrl: params.serverUrl,
    accessToken: '',
    fetchImpl: params.fetchImpl,
  });
  const resp = await client.bootstrapVault({
    vault_id: vaultId,
    device_id: deviceId,
    device_name: params.deviceName,
    device_type: 'pwa',
    public_key_x25519: wrappedKp.public_key_b64u,
    vsk_recovery_envelope: vskEnvelope,
  });

  await db.sync_state.bulkPut([
    { key: 'vault_id', value: vaultId },
    { key: 'device_id', value: deviceId },
    { key: 'device_name', value: params.deviceName },
    { key: 'server_url', value: params.serverUrl.replace(/\/+$/, '') },
    { key: 'device_access_token', value: resp.device_access_token },
    { key: 'current_key_epoch', value: String(resp.current_key_epoch) },
    { key: 'vault_vsk_envelope', value: JSON.stringify(vskEnvelope) },
    { key: 'device_keypair_wrapped', value: JSON.stringify(wrappedKp) },
    { key: 'last_server_seq', value: '0' },
  ]);

  await enqueueExistingVaultSnapshot();

  const keyring = buildKeyringFromVsk(vaultId, deviceId, vsk, resp.current_key_epoch, kp);
  const autoLockMinutes = Number((await db.sync_state.get('auto_lock_minutes'))?.value ?? '15') || 15;
  activeSession = {
    vaultId,
    deviceId,
    serverUrl: params.serverUrl.replace(/\/+$/, ''),
    accessToken: resp.device_access_token,
    vsk,
    devicePublicKey: kp.publicKey,
    devicePrivateKey: kp.privateKey,
    keyring,
    autoLockMinutes,
    unlockedAt: Date.now(),
  };
  touchVaultActivity();
  notifyListeners();
  return activeSession;
}

export async function unlockVaultWithPassword(
  masterPassword: string,
): Promise<UnlockedVaultSession> {
  await ensureCryptoReady();
  const rows = await db.sync_state.toArray();
  const map = new Map(rows.map(r => [r.key, r.value]));
  const vaultId = map.get('vault_id');
  const deviceId = map.get('device_id');
  const serverUrl = map.get('server_url');
  const accessToken = map.get('device_access_token');
  const envJson = map.get('vault_vsk_envelope');
  const kpJson = map.get('device_keypair_wrapped');

  if (!vaultId || !deviceId || !serverUrl || !accessToken || !envJson || !kpJson) {
    throw new Error('Coffre de synchronisation non configuré sur cet appareil.');
  }

  const envelope = JSON.parse(envJson) as VskRecoveryEnvelope;
  // Derive KEK via Web Worker when available to keep UI responsive
  const salt = fromBase64Url(envelope.kdf.salt);
  const kek = await deriveArgon2idInWorker(
    masterPassword,
    salt,
    envelope.kdf.memory_kib,
    envelope.kdf.iterations,
    envelope.kdf.parallelism,
  );
  let vsk: Uint8Array;
  try {
    const aad = canonicalJsonBytes({
      envelope_version: 1,
      kdf: 'argon2id13',
      purpose: 'vault-recovery',
      vault_id: vaultId,
    });
    vsk = await aeadDecrypt(kek, fromBase64Url(envelope.aead.nonce), fromBase64Url(envelope.aead.ciphertext), aad);
  } finally {
    kek.fill(0);
  }

  const kp = await unsealLocalDeviceKeypair(vsk, vaultId, JSON.parse(kpJson) as WrappedDeviceKeys);
  const epoch = Number(map.get('current_key_epoch') ?? '1') || 1;
  const keyring = buildKeyringFromVsk(vaultId, deviceId, vsk, epoch, kp);
  const autoLockMinutes = Number(map.get('auto_lock_minutes') ?? '15') || 15;

  activeSession = {
    vaultId,
    deviceId,
    serverUrl,
    accessToken,
    vsk,
    devicePublicKey: kp.publicKey,
    devicePrivateKey: kp.privateKey,
    keyring,
    autoLockMinutes,
    unlockedAt: Date.now(),
  };
  touchVaultActivity();
  notifyListeners();
  return activeSession;
}

export async function recoverVaultWithSecretKey(
  recoveryVskB64u: string,
  newMasterPassword: string,
): Promise<UnlockedVaultSession> {
  await ensureCryptoReady();
  const vsk = fromBase64Url(recoveryVskB64u.trim());
  if (vsk.length !== 32) throw new Error('Clé de secours invalide (32 octets attendus).');
  const rows = await db.sync_state.toArray();
  const map = new Map(rows.map(r => [r.key, r.value]));
  const vaultId = map.get('vault_id');
  const deviceId = map.get('device_id');
  const serverUrl = map.get('server_url');
  const accessToken = map.get('device_access_token');
  const kpJson = map.get('device_keypair_wrapped');
  if (!vaultId || !deviceId || !serverUrl || !accessToken || !kpJson) {
    throw new Error('Coffre local introuvable.');
  }

  const kp = await unsealLocalDeviceKeypair(vsk, vaultId, JSON.parse(kpJson) as WrappedDeviceKeys);
  const newEnv = await encryptVskEnvelope(vsk, vaultId, newMasterPassword);
  await db.sync_state.put({ key: 'vault_vsk_envelope', value: JSON.stringify(newEnv) });

  const epoch = Number(map.get('current_key_epoch') ?? '1') || 1;
  const keyring = buildKeyringFromVsk(vaultId, deviceId, vsk, epoch, kp);
  const autoLockMinutes = Number(map.get('auto_lock_minutes') ?? '15') || 15;
  activeSession = {
    vaultId,
    deviceId,
    serverUrl,
    accessToken,
    vsk,
    devicePublicKey: kp.publicKey,
    devicePrivateKey: kp.privateKey,
    keyring,
    autoLockMinutes,
    unlockedAt: Date.now(),
  };
  touchVaultActivity();
  notifyListeners();
  return activeSession;
}

export function exportRecoveryKeyString(session: UnlockedVaultSession): string {
  return toBase64Url(session.vsk);
}

// ── QR Pairing Initiator (Device A) & Joiner (Device B) ───────────────────────

export async function startPairingInitiator(
  session: UnlockedVaultSession,
  fetchImpl?: typeof fetch,
): Promise<{
  pairingId: string;
  pairingSecret: Uint8Array;
  pairingNonce: Uint8Array;
  qrPayload: QrPairingPayload;
  qrString: string;
}> {
  await ensureCryptoReady();
  const pairingId = generateUuidV4();
  const pairingSecret = randomBytes(32);
  const pairingNonce = randomBytes(32);
  const expiresAt = new Date(Date.now() + 10 * 60 * 1000).toISOString();

  const client = new SyncHttpClient({
    baseUrl: session.serverUrl,
    accessToken: session.accessToken,
    fetchImpl,
  });
  await client.createPairingSession(session.vaultId, {
    pairing_id: pairingId,
    device_a_id: session.deviceId,
    pairing_nonce: toBase64Url(pairingNonce),
    expires_at: expiresAt,
  });

  const qrPayload: QrPairingPayload = {
    v: 1,
    server_url: session.serverUrl,
    vault_id: session.vaultId,
    pairing_id: pairingId,
    pairing_secret: toBase64Url(pairingSecret),
    expires_at: expiresAt,
  };
  return {
    pairingId,
    pairingSecret,
    pairingNonce,
    qrPayload,
    qrString: JSON.stringify(qrPayload),
  };
}

export async function approveJoinedPairingDevice(
  session: UnlockedVaultSession,
  pairingId: string,
  pairingSecret: Uint8Array,
  pairingNonce: Uint8Array,
  fetchImpl?: typeof fetch,
): Promise<{ deviceBName: string; deviceBId: string }> {
  const client = new SyncHttpClient({
    baseUrl: session.serverUrl,
    accessToken: session.accessToken,
    fetchImpl,
  });
  const st = await client.getPairingStatus(session.vaultId, pairingId);
  if (st.status !== 'joined') {
    throw new Error(`Session pas encore prête (statut : ${String(st.status)})`);
  }
  const deviceBId = String(st.device_b_id);
  const deviceBName = String(st.device_b_name || 'Nouvel appareil');
  const sessionNonceB = String(st.session_nonce_b);
  const gotProof = String(st.secret_proof_hmac);
  const expectedProof = computeSecretProofHmac(
    pairingSecret,
    session.vaultId,
    pairingId,
    deviceBId,
    sessionNonceB,
  );
  if (gotProof !== expectedProof) {
    throw new Error('Preuve cryptographique de jumelage invalide !');
  }

  const ctx = {
    vault_id: session.vaultId,
    pairing_id: pairingId,
    device_a_id: session.deviceId,
    device_b_id: deviceBId,
    session_nonce_b: sessionNonceB,
  };
  const kPair = derivePairingKey(pairingSecret, pairingNonce, ctx);

  // Include all cached epoch root keys + ensure epoch 1
  if (!session.keyring.epochRootKeys.has(1)) {
    session.keyring.epochRootKeys.set(1, deriveInitialEpoch1RootKey(session.vsk, session.vaultId));
  }
  const epochKeysMap: Record<string, string> = {};
  for (const [ep, key] of session.keyring.epochRootKeys.entries()) {
    epochKeysMap[String(ep)] = toBase64Url(key);
  }
  const bundle: PairingBundlePayload = {
    vault_id: session.vaultId,
    vsk_b64u: toBase64Url(session.vsk),
    current_key_epoch: session.keyring.currentKeyEpoch,
    epoch_keys: epochKeysMap,
  };
  const encrypted = await encryptPairingBundle(kPair, ctx, bundle);
  await client.approvePairingSession(session.vaultId, pairingId, {
    bundle_nonce: encrypted.nonce,
    encrypted_vault_bundle: encrypted.ciphertext,
  });
  return { deviceBId, deviceBName };
}

export async function joinVaultWithPairingCode(params: {
  qrJson: string;
  deviceName: string;
  masterPassword: string;
  fetchImpl?: typeof fetch;
  pollIntervalMs?: number;
  timeoutMs?: number;
}): Promise<UnlockedVaultSession> {
  await ensureCryptoReady();
  const qr = JSON.parse(params.qrJson.trim()) as QrPairingPayload;
  if (qr.v !== 1 || !qr.vault_id || !qr.pairing_id || !qr.pairing_secret || !qr.server_url) {
    throw new Error('Code de jumelage Noteor invalide.');
  }
  const pairingSecret = fromBase64Url(qr.pairing_secret);
  const deviceBId = generateUuidV4();
  const kpB = await generateDeviceKeypair();
  const sessionNonceBBytes = randomBytes(24);
  const sessionNonceB = toBase64Url(sessionNonceBBytes);
  const secretProof = computeSecretProofHmac(
    pairingSecret,
    qr.vault_id,
    qr.pairing_id,
    deviceBId,
    sessionNonceB,
  );

  const client = new SyncHttpClient({
    baseUrl: qr.server_url,
    accessToken: '',
    fetchImpl: params.fetchImpl,
  });

  const joinRes = await client.joinPairingSession(qr.vault_id, qr.pairing_id, {
    device_b_id: deviceBId,
    device_b_name: params.deviceName,
    device_b_type: 'pwa',
    device_b_public_key_x25519: toBase64Url(kpB.publicKey),
    session_nonce_b: sessionNonceB,
    secret_proof_hmac: secretProof,
  });

  const deadline = Date.now() + (params.timeoutMs ?? 60_000);
  let statusObj: Record<string, unknown> = {};
  while (Date.now() < deadline) {
    statusObj = await client.getPairingStatus(qr.vault_id, qr.pairing_id);
    if (statusObj.status === 'approved') break;
    await new Promise(r => setTimeout(r, params.pollIntervalMs ?? 1200));
  }
  if (statusObj.status !== 'approved') {
    throw new Error('Temps dépassé : l\'appareil principal n\'a pas approuvé le jumelage.');
  }

  const ctx = {
    vault_id: qr.vault_id,
    pairing_id: qr.pairing_id,
    device_a_id: joinRes.device_a_id,
    device_b_id: deviceBId,
    session_nonce_b: sessionNonceB,
  };
  const kPair = derivePairingKey(pairingSecret, fromBase64Url(joinRes.pairing_nonce), ctx);
  const bundle = await decryptPairingBundle(
    kPair,
    ctx,
    String(statusObj.bundle_nonce),
    String(statusObj.encrypted_vault_bundle),
  );

  const vsk = fromBase64Url(bundle.vsk_b64u);
  const confirmHmac = computePairingConfirmHmac(kPair, qr.pairing_id, deviceBId, qr.vault_id);
  const confirmRes = await client.confirmPairingSession(qr.vault_id, qr.pairing_id, {
    device_b_id: deviceBId,
    confirm_hmac: confirmHmac,
  });

  const vskEnvelope = await encryptVskEnvelope(vsk, qr.vault_id, params.masterPassword);
  const wrappedKp = await sealLocalDeviceKeypair(vsk, qr.vault_id, kpB);

  await db.sync_state.bulkPut([
    { key: 'vault_id', value: qr.vault_id },
    { key: 'device_id', value: deviceBId },
    { key: 'device_name', value: params.deviceName },
    { key: 'server_url', value: qr.server_url.replace(/\/+$/, '') },
    { key: 'device_access_token', value: confirmRes.device_access_token },
    { key: 'current_key_epoch', value: String(bundle.current_key_epoch) },
    { key: 'vault_vsk_envelope', value: JSON.stringify(vskEnvelope) },
    { key: 'device_keypair_wrapped', value: JSON.stringify(wrappedKp) },
    { key: 'last_server_seq', value: '0' },
  ]);

  await enqueueExistingVaultSnapshot();

  const extraEpochs: Record<number, Uint8Array> = {};
  for (const [epStr, b64] of Object.entries(bundle.epoch_keys)) {
    extraEpochs[Number(epStr)] = fromBase64Url(b64);
  }
  const keyring = buildKeyringFromVsk(
    qr.vault_id,
    deviceBId,
    vsk,
    bundle.current_key_epoch,
    kpB,
    extraEpochs,
  );
  const autoLockMinutes = Number((await db.sync_state.get('auto_lock_minutes'))?.value ?? '15') || 15;
  activeSession = {
    vaultId: qr.vault_id,
    deviceId: deviceBId,
    serverUrl: qr.server_url.replace(/\/+$/, ''),
    accessToken: confirmRes.device_access_token,
    vsk,
    devicePublicKey: kpB.publicKey,
    devicePrivateKey: kpB.privateKey,
    keyring,
    autoLockMinutes,
    unlockedAt: Date.now(),
  };
  touchVaultActivity();
  notifyListeners();
  return activeSession;
}

export async function resetLocalSyncConfig(): Promise<void> {
  lockVaultNow();
  await db.sync_state.clear();
  await db.sync_operations.clear();
  await db.sync_pending_ops.clear();
  await db.sync_conflicts.clear();
}

export function createEngineForSession(
  session: UnlockedVaultSession,
  fetchImpl?: typeof fetch,
): { client: SyncHttpClient; engine: PwaSyncEngine } {
  const client = new SyncHttpClient({
    baseUrl: session.serverUrl,
    accessToken: session.accessToken,
    fetchImpl,
  });
  const engine = new PwaSyncEngine(client, session.keyring);
  return { client, engine };
}

// Re-export helper for recovery tests
export { decryptVskEnvelope };
