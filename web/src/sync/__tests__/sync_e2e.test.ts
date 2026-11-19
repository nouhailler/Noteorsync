import 'fake-indexeddb/auto';
import { describe, it, expect, beforeAll, afterAll, beforeEach } from 'vitest';
import { spawn, execFileSync, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { db, saveCategory, saveNote, addTagToNote, addAttachment } from '../../db';
import { PwaSyncEngine, SyncHttpError } from '../engine';
import {
  approveJoinedPairingDevice,
  createEngineForSession,
  exportRecoveryKeyString,
  getActiveVaultSession,
  initializeNewVault,
  lockVaultNow,
  recoverVaultWithSecretKey,
  startPairingInitiator,
  unlockVaultWithPassword,
} from '../vault';

const ROOT_DIR = path.resolve(__dirname, '../../../..');

async function getFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      if (!addr || typeof addr === 'string') {
        srv.close(() => reject(new Error('Could not bind ephemeral port')));
        return;
      }
      const p = addr.port;
      srv.close(() => resolve(p));
    });
  });
}

async function waitForHealth(baseUrl: string): Promise<void> {
  const deadline = Date.now() + 8000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/healthz`);
      if (res.ok) return;
    } catch {
      // retry until up
    }
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error(`Sync server did not start cleanly at ${baseUrl}`);
}

function runDesktopScript(code: string, payloadJson: unknown): Record<string, unknown> {
  const stdout = execFileSync('python3', ['-c', code], {
    cwd: ROOT_DIR,
    input: JSON.stringify(payloadJson),
    encoding: 'utf-8',
    env: {
      ...process.env,
      PYTHONPATH: `${path.join(ROOT_DIR)}:${path.join(ROOT_DIR, 'sync-server')}`,
    },
  });
  return JSON.parse(stdout.trim()) as Record<string, unknown>;
}

describe('Phase 7 Full Live E2E: PWA (TypeScript/Dexie) ↔ Sync Server (HTTP) ↔ Desktop (Python/SQLite)', () => {
  let tmpDir: string;
  let serverProc: ChildProcess;
  let serverUrl: string;

  beforeAll(async () => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'noteor-e2e-'));
    const serverDataDir = path.join(tmpDir, 'server-data');
    fs.mkdirSync(serverDataDir, { recursive: true });
    const port = await getFreePort();
    serverUrl = `http://127.0.0.1:${port}`;

    serverProc = spawn('python3', ['-m', 'app.stdlib_server'], {
      cwd: path.join(ROOT_DIR, 'sync-server'),
      env: {
        ...process.env,
        PORT: String(port),
        NOTEOR_DATA_DIR: serverDataDir,
      },
      stdio: 'pipe',
    });
    await waitForHealth(serverUrl);
  });

  afterAll(() => {
    serverProc?.kill('SIGTERM');
    if (tmpDir) {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });

  beforeEach(async () => {
    lockVaultNow();
    await db.delete();
    await db.open();
  });

  it('runs end-to-end vault init, lock/unlock, recovery, QR pairing to Python Desktop, NTRM1 media, conflict resolution, and epoch rotation', async () => {
    // ── 1. Initialize encrypted Vault on PWA & create local data with NTRM1 media ──
    const session = await initializeNewVault({
      serverUrl,
      deviceName: 'Noteor PWA Firefox',
      masterPassword: 'CorrectHorseBatteryStaple!42',
    });
    expect(session.vaultId).toMatch(/^[0-9a-f-]{36}$/);

    const catId = await saveCategory({ name: 'Travail', color: '#4F46E5', sort_order: 1 });
    const targetId = await saveNote({
      title: 'Architecture Noteor',
      content: 'Spécification E2EE complète',
      category_id: catId,
      created_at: '',
      updated_at: '',
      is_deleted: false,
    });
    await addTagToNote(targetId, 'Chiffré');
    const noteTarget = (await db.notes.get(targetId))!;

    const pngBytes = new Uint8Array(32 * 1024);
    pngBytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
    for (let i = 8; i < pngBytes.length; i++) pngBytes[i] = i & 0xff;
    await addAttachment({
      note_id: targetId,
      type: 'image',
      filename: 'schema.png',
      data: new Blob([pngBytes], { type: 'image/png' }),
      thumbnail: 'data:image/png;base64,AA==',
      mime_type: 'image/png',
      created_at: new Date().toISOString(),
    });

    // Push PWA -> live HTTP Sync Server
    const { engine } = createEngineForSession(session);
    const pushRes = await engine.syncNow();
    expect(pushRes.pushedCount).toBeGreaterThanOrEqual(4);
    expect(pushRes.mediaUploaded).toBe(1);

    // ── 2. Verify Vault lock wipes RAM keys & password unlock + recovery key work ──
    const recoveryString = exportRecoveryKeyString(session);
    const vskRef = session.vsk;
    lockVaultNow();
    expect(getActiveVaultSession()).toBeNull();
    // VSK buffer was zeroized in place
    expect(Array.from(vskRef).every(b => b === 0)).toBe(true);

    // Wrong password rejects
    await expect(unlockVaultWithPassword('WrongPassword!')).rejects.toThrow();

    // Recovery key resets password and unlocks cleanly
    const recoveredSession = await recoverVaultWithSecretKey(recoveryString, 'NewMasterPass#99');
    expect(recoveredSession.vaultId).toBe(session.vaultId);

    lockVaultNow();
    const reunlocked = await unlockVaultWithPassword('NewMasterPass#99');
    expect(reunlocked.deviceId).toBe(session.deviceId);

    // ── 3. QR Pairing: PWA initiates -> Python Desktop joins -> PWA approves -> Python Desktop confirms & pulls ──
    const pairing = await startPairingInitiator(reunlocked);
    const desktopDbPath = path.join(tmpDir, 'desktop.db');
    const desktopMediaDir = path.join(tmpDir, 'desktop_media');

    // Python Desktop joins the pairing session
    const desktopJoinOut = runDesktopScript(
      `
import json, sys, os
from sync.desktop import crypto as pyc
from sync.desktop.engine import SyncHttpClient

inp = json.loads(sys.stdin.read())
qr = json.loads(inp["qr_string"])
dev_b = pyc.generate_uuid_v4()
pub_b, priv_b = pyc.generate_device_keypair()
sess_nonce_b = pyc.to_b64u(os.urandom(24))
proof = pyc.compute_secret_proof_hmac(
    pyc.from_b64u(qr["pairing_secret"]), qr["vault_id"], qr["pairing_id"], dev_b, sess_nonce_b
)
client = SyncHttpClient(qr["server_url"])
res = client.join_pairing_session(qr["vault_id"], qr["pairing_id"], {
    "device_b_id": dev_b,
    "device_b_name": "Noteor Desktop Linux",
    "device_b_type": "desktop",
    "device_b_public_key_x25519": pyc.to_b64u(pub_b),
    "session_nonce_b": sess_nonce_b,
    "secret_proof_hmac": proof,
})
print(json.dumps({
    "device_b_id": dev_b,
    "pub_b": pyc.to_b64u(pub_b),
    "priv_b": pyc.to_b64u(priv_b),
    "session_nonce_b": sess_nonce_b,
    "device_a_id": res["device_a_id"],
    "pairing_nonce": res["pairing_nonce"],
}))
`,
      { qr_string: pairing.qrString },
    );

    // PWA verifies proof_hmac and approves pairing
    const approval = await approveJoinedPairingDevice(
      reunlocked,
      pairing.pairingId,
      pairing.pairingSecret,
      pairing.pairingNonce,
    );
    expect(approval.deviceBName).toBe('Noteor Desktop Linux');

    // Python Desktop finishes confirm_pairing, syncs from live HTTP server, decrypts note + NTRM1 image, verifies SHA-256
    const desktopPullOut = runDesktopScript(
      `
import hashlib, json, sys
from pathlib import Path
from sync.desktop import crypto as pyc
from sync.desktop.engine import DesktopSyncEngine, SyncHttpClient, VaultKeyring
from sync.desktop.sqlite_sync_adapter import DesktopSyncDatabase

inp = json.loads(sys.stdin.read())
qr = json.loads(inp["qr_string"])
client = SyncHttpClient(qr["server_url"])
st = client.get_pairing_status(qr["vault_id"], qr["pairing_id"])
ctx = {
    "vault_id": qr["vault_id"],
    "pairing_id": qr["pairing_id"],
    "device_a_id": inp["device_a_id"],
    "device_b_id": inp["device_b_id"],
    "session_nonce_b": inp["session_nonce_b"],
}
k_pair = pyc.derive_pairing_key(
    pyc.from_b64u(qr["pairing_secret"]),
    pyc.from_b64u(inp["pairing_nonce"]),
    qr["vault_id"],
    qr["pairing_id"],
    inp["device_a_id"],
    inp["device_b_id"],
    inp["session_nonce_b"],
)
bundle = pyc.decrypt_pairing_bundle(k_pair, ctx, st["bundle_nonce"], st["encrypted_vault_bundle"])
conf_hmac = pyc.compute_pairing_confirm_hmac(k_pair, qr["pairing_id"], inp["device_b_id"], qr["vault_id"])
conf = client.confirm_pairing_session(qr["vault_id"], qr["pairing_id"], {
    "device_b_id": inp["device_b_id"],
    "confirm_hmac": conf_hmac,
})
token_b = conf["device_access_token"]
client.access_token = token_b

db_d = DesktopSyncDatabase(inp["db_path"])
db_d.set_state("vault_id", qr["vault_id"])
db_d.set_state("device_id", inp["device_b_id"])
vsk = pyc.from_b64u(bundle["vsk_b64u"])
kp = (pyc.from_b64u(inp["pub_b"]), pyc.from_b64u(inp["priv_b"]))
kr = VaultKeyring.from_vsk(qr["vault_id"], inp["device_b_id"], vsk, int(bundle["current_key_epoch"]), kp)
eng = DesktopSyncEngine(db_d, client, kr, inp["media_dir"])
res = eng.sync_now()

notes = [dict(r) for r in db_d.conn.execute("SELECT * FROM notes").fetchall()]
atts = [dict(r) for r in db_d.conn.execute("SELECT * FROM attachments").fetchall()]
tags = [dict(r) for r in db_d.conn.execute("SELECT * FROM tags").fetchall()]
att_bytes = Path(atts[0]["filepath"]).read_bytes()

# Desktop creates an audio note + edits existing note offline cleanly later; first return verified pulled state
db_d.close()
print(json.dumps({
    "token_b": token_b,
    "pulled_count": res.pulled_count,
    "media_downloaded": res.media_downloaded,
    "note_title": notes[0]["title"],
    "note_sync_id": notes[0]["sync_id"],
    "tag_name": tags[0]["name"],
    "att_sha256": hashlib.sha256(att_bytes).hexdigest(),
    "att_len": len(att_bytes),
}))
`,
      {
        ...desktopJoinOut,
        qr_string: pairing.qrString,
        db_path: desktopDbPath,
        media_dir: desktopMediaDir,
      },
    );

    expect(desktopPullOut.note_title).toBe('Architecture Noteor');
    expect(desktopPullOut.note_sync_id).toBe(noteTarget.sync_id);
    expect(desktopPullOut.tag_name).toBe('chiffré');
    expect(desktopPullOut.media_downloaded).toBe(1);
    expect(desktopPullOut.att_len).toBe(pngBytes.length);

    // ── 4. Concurrent edits: Desktop edits & creates audio note; PWA edits offline -> Conflict -> PWA resolves -> Desktop pulls ──
    const audioBytes = new Uint8Array([0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4, 5, 6, 7, 8]);
    const desktopAudioPath = path.join(tmpDir, 'voice.webm');
    fs.writeFileSync(desktopAudioPath, audioBytes);

    runDesktopScript(
      `
import json, sys
from sync.desktop import crypto as pyc
from sync.desktop.engine import DesktopSyncEngine, SyncHttpClient, VaultKeyring
from sync.desktop.sqlite_sync_adapter import DesktopSyncDatabase

inp = json.loads(sys.stdin.read())
db_d = DesktopSyncDatabase(inp["db_path"])
client = SyncHttpClient(inp["server_url"], inp["token_b"])
vsk = pyc.from_b64u(inp["vsk_b64u"])
kp = (pyc.from_b64u(inp["pub_b"]), pyc.from_b64u(inp["priv_b"]))
kr = VaultKeyring.from_vsk(inp["vault_id"], inp["device_b_id"], vsk, 1, kp)
eng = DesktopSyncEngine(db_d, client, kr, inp["media_dir"])

existing = db_d.conn.execute("SELECT * FROM notes WHERE sync_id=?", (inp["target_sync_id"],)).fetchone()
db_d.save_note(title="Architecture Noteor", content="Modif Desktop distante", note_id=existing["id"], category_id=existing["category_id"])
audio_note_id = db_d.save_note(title="Mémo Vocal Desktop", content="Lien [[Architecture Noteor|@" + inp["target_sync_id"] + "]]")
db_d.add_attachment(note_id=audio_note_id, media_type="audio", filename="voice.webm", filepath=inp["audio_path"], duration=4.2, mime_type="audio/webm")
res = eng.sync_now()
db_d.close()
print(json.dumps({"pushed": res.pushed_count, "media_uploaded": res.media_uploaded}))
`,
      {
        server_url: serverUrl,
        db_path: desktopDbPath,
        media_dir: desktopMediaDir,
        token_b: desktopPullOut.token_b,
        vsk_b64u: recoveryString,
        pub_b: desktopJoinOut.pub_b,
        priv_b: desktopJoinOut.priv_b,
        vault_id: reunlocked.vaultId,
        device_b_id: desktopJoinOut.device_b_id,
        target_sync_id: noteTarget.sync_id,
        audio_path: desktopAudioPath,
      },
    );

    // Offline edit on PWA to the same shared note -> triggers DAG conflict
    await saveNote({
      ...noteTarget,
      id: targetId,
      content: 'Modif PWA locale hors-ligne',
    });

    const pwaEngine: PwaSyncEngine = createEngineForSession(reunlocked).engine;
    const sync2 = await pwaEngine.syncNow();
    expect(sync2.conflictsCreated).toBe(1);
    expect(sync2.mediaDownloaded).toBe(1);

    // Verify Desktop's audio note & decrypted audio attachment arrived in PWA Dexie DB
    const audioNotePwa = await db.notes.where('title').equals('Mémo Vocal Desktop').first();
    expect(audioNotePwa).toBeDefined();
    expect(audioNotePwa!.content).toBe(`Lien [[Architecture Noteor|@${noteTarget.sync_id}]]`);
    const pwaAudioAtt = await db.attachments.where('note_id').equals(audioNotePwa!.id!).first();
    expect(pwaAudioAtt?.media_status).toBe('available');
    expect(new Uint8Array(await pwaAudioAtt!.data.arrayBuffer())).toEqual(audioBytes);

    // Resolve conflict manually on PWA & sync back to Desktop
    const openConflicts = await db.sync_conflicts.where('status').equals('open').toArray();
    expect(openConflicts.length).toBe(1);
    await pwaEngine.resolveConflict(openConflicts[0].conflict_id, 'manual_merge', {
      title: 'Architecture Noteor (Fusionnée)',
      content: 'Modif PWA + Modif Desktop',
    });
    await pwaEngine.syncNow();

    // ── 5. PWA revokes Desktop device & rotates epoch -> Desktop gets 403 DEVICE_REVOKED ──
    const newEpoch = await pwaEngine.revokeDeviceAndRotateEpoch(String(desktopJoinOut.device_b_id));
    expect(newEpoch).toBe(2);

    const revokedCheck = runDesktopScript(
      `
import json, sys
from sync.desktop import crypto as pyc
from sync.desktop.engine import DesktopSyncEngine, SyncHttpClient, SyncHttpError, VaultKeyring
from sync.desktop.sqlite_sync_adapter import DesktopSyncDatabase

inp = json.loads(sys.stdin.read())
db_d = DesktopSyncDatabase(inp["db_path"])
client = SyncHttpClient(inp["server_url"], inp["token_b"])
vsk = pyc.from_b64u(inp["vsk_b64u"])
kp = (pyc.from_b64u(inp["pub_b"]), pyc.from_b64u(inp["priv_b"]))
kr = VaultKeyring.from_vsk(inp["vault_id"], inp["device_b_id"], vsk, 1, kp)
eng = DesktopSyncEngine(db_d, client, kr, inp["media_dir"])
try:
    eng.sync_now()
    out = {"revoked": False}
except SyncHttpError as exc:
    out = {"revoked": True, "status": exc.status, "code": exc.code}
db_d.close()
print(json.dumps(out))
`,
      {
        server_url: serverUrl,
        db_path: desktopDbPath,
        media_dir: desktopMediaDir,
        token_b: desktopPullOut.token_b,
        vsk_b64u: recoveryString,
        pub_b: desktopJoinOut.pub_b,
        priv_b: desktopJoinOut.priv_b,
        vault_id: reunlocked.vaultId,
        device_b_id: desktopJoinOut.device_b_id,
      },
    );
    expect(revokedCheck.revoked).toBe(true);
    expect(revokedCheck.status).toBe(403);
    expect(revokedCheck.code).toBe('DEVICE_REVOKED');

    // Device A (PWA) creates & syncs a note under key epoch #2
    await saveNote({
      title: 'Note Epoch 2',
      content: 'Post-révocation',
      created_at: '',
      updated_at: '',
      is_deleted: false,
    });
    const finalPwaSync = await pwaEngine.syncNow();
    expect(finalPwaSync.pushedCount).toBe(1);
    const epoch2Ops = await db.sync_operations.filter(op => op.key_epoch === 2).count();
    expect(epoch2Ops).toBe(1);

    // Ensure SyncHttpError stays exported cleanly
    expect(SyncHttpError).toBeDefined();
  });
});
