import { useState, useEffect } from 'react';
import { useLiveQuery } from 'dexie-react-hooks';
import QRCode from 'qrcode';
import {
  Shield,
  Lock,
  Unlock,
  RefreshCw,
  QrCode,
  Laptop,
  AlertTriangle,
  KeyRound,
  Copy,
  Check,
  X,
  Trash2,
  UserPlus,
} from 'lucide-react';
import { db } from '../db';
import ConflictResolver from './ConflictResolver';
import type { ServerDeviceInfo, SyncProgress } from '../sync/engine';
import {
  approveJoinedPairingDevice,
  createEngineForSession,
  exportRecoveryKeyString,
  getActiveVaultSession,
  getVaultConfigSummary,
  initializeNewVault,
  joinVaultWithPairingCode,
  lockVaultNow,
  recoverVaultWithSecretKey,
  resetLocalSyncConfig,
  setAutoLockMinutes,
  startPairingInitiator,
  subscribeVaultSession,
  touchVaultActivity,
  unlockVaultWithPassword,
  type UnlockedVaultSession,
} from '../sync/vault';

interface Props {
  onClose?: () => void;
  embedded?: boolean;
}

type SyncTab = 'status' | 'pairing' | 'devices' | 'conflicts' | 'security';

export default function SyncModal({ onClose, embedded = false }: Props) {
  const [session, setSession] = useState<UnlockedVaultSession | null>(() => getActiveVaultSession());
  const [config, setConfig] = useState<{
    configured: boolean;
    vaultId?: string;
    deviceId?: string;
    serverUrl?: string;
    currentKeyEpoch?: number;
    autoLockMinutes: number;
    deviceName?: string;
  }>({ configured: false, autoLockMinutes: 15 });

  const [tab, setTab] = useState<SyncTab>('status');
  const [setupMode, setSetupMode] = useState<'create' | 'join'>('create');
  const [serverUrl, setServerUrl] = useState('http://localhost:8787');
  const [deviceName, setDeviceName] = useState('Noteor PWA');
  const [password, setPassword] = useState('');
  const [qrInput, setQrInput] = useState('');
  const [recoveryInput, setRecoveryInput] = useState('');
  const [showRecoverForm, setShowRecoverForm] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [infoMsg, setInfoMsg] = useState('');
  const [progress, setProgress] = useState<SyncProgress | null>(null);

  // Pairing initiator state
  const [pairingData, setPairingData] = useState<{
    pairingId: string;
    pairingSecret: Uint8Array;
    pairingNonce: Uint8Array;
    qrString: string;
    qrImageUrl: string;
  } | null>(null);
  const [devices, setDevices] = useState<ServerDeviceInfo[]>([]);
  const [copiedRecovery, setCopiedRecovery] = useState(false);
  const [showRecoverySecret, setShowRecoverySecret] = useState(false);

  const pendingOutboxCount = useLiveQuery(
    () => db.sync_operations.where('status').equals('pending').count(),
    [],
  ) ?? 0;
  const openConflictCount = useLiveQuery(
    () => db.sync_conflicts.where('status').equals('open').count(),
    [],
  ) ?? 0;

  async function refreshConfig() {
    const c = await getVaultConfigSummary();
    setConfig(c);
    if (c.serverUrl) setServerUrl(c.serverUrl);
  }

  useEffect(() => {
    refreshConfig();
    const unsub = subscribeVaultSession(() => {
      setSession(getActiveVaultSession());
      refreshConfig();
    });
    return unsub;
  }, []);

  const currentEngine = session ? createEngineForSession(session).engine : null;

  async function handleCreateVault(e: React.FormEvent) {
    e.preventDefault();
    if (password.length < 8) {
      setError('Utilisez un mot de passe d\'au moins 8 caractères.');
      return;
    }
    setBusy(true);
    setError('');
    setInfoMsg('');
    try {
      await initializeNewVault({ serverUrl, deviceName, masterPassword: password });
      setPassword('');
      setInfoMsg('Coffre créé et chiffré. Enregistrez votre clé de secours dans l\'onglet Sécurité.');
      await refreshConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Échec création du coffre.');
    } finally {
      setBusy(false);
    }
  }

  async function handleJoinVault(e: React.FormEvent) {
    e.preventDefault();
    if (password.length < 8) {
      setError('Choisissez un mot de passe local d\'au moins 8 caractères.');
      return;
    }
    setBusy(true);
    setError('');
    setInfoMsg('En attente de l\'approbation sur l\'appareil principal…');
    try {
      await joinVaultWithPairingCode({
        qrJson: qrInput,
        deviceName,
        masterPassword: password,
      });
      setPassword('');
      setQrInput('');
      setInfoMsg('Appareil jumelé avec succès !');
      await refreshConfig();
    } catch (err) {
      setInfoMsg('');
      setError(err instanceof Error ? err.message : 'Échec du jumelage.');
    } finally {
      setBusy(false);
    }
  }

  async function handleUnlock(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await unlockVaultWithPassword(password);
      setPassword('');
    } catch {
      setError('Mot de passe maître incorrect.');
    } finally {
      setBusy(false);
    }
  }

  async function handleRecoverWithKey(e: React.FormEvent) {
    e.preventDefault();
    if (password.length < 8) {
      setError('Nouveau mot de passe : 8 caractères minimum.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await recoverVaultWithSecretKey(recoveryInput, password);
      setRecoveryInput('');
      setPassword('');
      setShowRecoverForm(false);
      setInfoMsg('Coffre restauré et re-chiffré avec le nouveau mot de passe.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Clé de secours invalide.');
    } finally {
      setBusy(false);
    }
  }

  async function handleSyncNow() {
    if (!session) return;
    touchVaultActivity();
    setBusy(true);
    setError('');
    setInfoMsg('');
    try {
      const { engine } = createEngineForSession(session);
      const res = await engine.syncNow({ onProgress: setProgress });
      setInfoMsg(
        `Synchronisation terminée : ${res.pushedCount} envoyée(s), ${res.pulledCount} reçue(s)` +
          (res.conflictsCreated > 0 ? ` · ${res.conflictsCreated} conflit(s) détecté(s)` : ''),
      );
      if (res.conflictsCreated > 0) setTab('conflicts');
      await refreshConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur réseau durant la synchronisation.');
    } finally {
      setBusy(false);
    }
  }

  async function handleStartPairing() {
    if (!session) return;
    touchVaultActivity();
    setBusy(true);
    setError('');
    try {
      const started = await startPairingInitiator(session);
      const qrImageUrl = await QRCode.toDataURL(started.qrString, { width: 220, margin: 2 });
      setPairingData({ ...started, qrImageUrl });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Impossible de créer la session de jumelage.');
    } finally {
      setBusy(false);
    }
  }

  async function handleApprovePairing() {
    if (!session || !pairingData) return;
    setBusy(true);
    setError('');
    try {
      const res = await approveJoinedPairingDevice(
        session,
        pairingData.pairingId,
        pairingData.pairingSecret,
        pairingData.pairingNonce,
      );
      setPairingData(null);
      setInfoMsg(`Appareil « ${res.deviceBName} » approuvé et chiffré avec succès.`);
      await loadDevices();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Approbation impossible (vérifiez que l\'autre appareil a scanné le code).');
    } finally {
      setBusy(false);
    }
  }

  async function loadDevices() {
    if (!session) return;
    setBusy(true);
    setError('');
    try {
      const { client } = createEngineForSession(session);
      const res = await client.listDevices(session.vaultId);
      setDevices(res.devices);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Impossible de charger les appareils.');
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(deviceId: string) {
    if (!session) return;
    if (!confirm('Révoquer cet appareil et effectuer une rotation des clés de chiffrement ?')) return;
    setBusy(true);
    setError('');
    try {
      const { engine } = createEngineForSession(session);
      const newEpoch = await engine.revokeDeviceAndRotateEpoch(deviceId);
      setInfoMsg(`Appareil révoqué. Nouvelle époque cryptographique : #${newEpoch}.`);
      await loadDevices();
      await refreshConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Échec de la révocation.');
    } finally {
      setBusy(false);
    }
  }

  const contentBody = (
    <div className="space-y-5">
      {/* Alerts */}
      {error && (
        <div className="px-3.5 py-2.5 bg-red-50 border border-red-200 rounded-xl text-xs text-red-700">
          {error}
        </div>
      )}
      {infoMsg && (
        <div className="px-3.5 py-2.5 bg-emerald-50 border border-emerald-200 rounded-xl text-xs text-emerald-800">
          {infoMsg}
        </div>
      )}

      {/* Not configured: Create Vault or Join via Pairing */}
      {!config.configured && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 p-1 bg-gray-100 rounded-xl">
            <button
              type="button"
              onClick={() => setSetupMode('create')}
              className={`min-h-[44px] rounded-lg text-xs font-semibold transition-colors ${
                setupMode === 'create' ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-600'
              }`}
            >
              Créer un coffre
            </button>
            <button
              type="button"
              onClick={() => setSetupMode('join')}
              className={`min-h-[44px] rounded-lg text-xs font-semibold transition-colors ${
                setupMode === 'join' ? 'bg-white text-indigo-700 shadow-sm' : 'text-gray-600'
              }`}
            >
              Rejoindre via jumelage
            </button>
          </div>

          {setupMode === 'create' ? (
            <form onSubmit={handleCreateVault} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">URL du serveur Noteor Sync</label>
                <input
                  type="url"
                  required
                  value={serverUrl}
                  onChange={e => setServerUrl(e.target.value)}
                  placeholder="https://sync.mon-serveur.fr"
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-xl font-mono outline-none focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Nom de cet appareil</label>
                <input
                  type="text"
                  required
                  value={deviceName}
                  onChange={e => setDeviceName(e.target.value)}
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-xl outline-none focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Mot de passe maître E2EE (Argon2id — jamais envoyé au serveur)
                </label>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="Minimum 8 caractères"
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-xl outline-none focus:border-indigo-500"
                />
              </div>
              <button
                type="submit"
                disabled={busy}
                className="w-full min-h-[44px] rounded-xl bg-indigo-600 text-white text-sm font-semibold active:bg-indigo-700 disabled:opacity-50"
              >
                {busy ? 'Chiffrement Argon2id…' : 'Initialiser le coffre chiffré'}
              </button>
            </form>
          ) : (
            <form onSubmit={handleJoinVault} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Payload JSON / Code de jumelage affiché sur l&apos;appareil principal
                </label>
                <textarea
                  rows={4}
                  required
                  value={qrInput}
                  onChange={e => setQrInput(e.target.value)}
                  placeholder='{"v":1,"server_url":"...","vault_id":"...","pairing_id":"...","pairing_secret":"..."}'
                  className="w-full px-3 py-2 text-xs font-mono border border-gray-200 rounded-xl outline-none focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Nom de cet appareil</label>
                <input
                  type="text"
                  required
                  value={deviceName}
                  onChange={e => setDeviceName(e.target.value)}
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-xl outline-none focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Mot de passe maître local</label>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-xl outline-none focus:border-indigo-500"
                />
              </div>
              <button
                type="submit"
                disabled={busy}
                className="w-full min-h-[44px] rounded-xl bg-indigo-600 text-white text-sm font-semibold active:bg-indigo-700 disabled:opacity-50 flex items-center justify-center gap-2"
              >
                <UserPlus size={16} />
                {busy ? 'Attente d\'approbation…' : 'Rejoindre le coffre'}
              </button>
            </form>
          )}
        </div>
      )}

      {/* Configured but LOCKED */}
      {config.configured && !session && (
        <div className="bg-gray-50 border border-gray-200 rounded-2xl p-4 space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-100 text-indigo-700 flex items-center justify-center shrink-0">
              <Lock size={20} />
            </div>
            <div>
              <p className="text-sm font-semibold text-gray-900">Coffre verrouillé</p>
              <p className="text-xs text-gray-500">
                {pendingOutboxCount} modification(s) locale(s) en attente · Conflits : {openConflictCount}
              </p>
            </div>
          </div>

          {!showRecoverForm ? (
            <form onSubmit={handleUnlock} className="space-y-3">
              <input
                type="password"
                required
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Mot de passe maître"
                className="w-full px-3 py-2.5 text-sm bg-white border border-gray-200 rounded-xl outline-none focus:border-indigo-500"
              />
              <button
                type="submit"
                disabled={busy}
                className="w-full min-h-[44px] rounded-xl bg-indigo-600 text-white text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-50"
              >
                <Unlock size={16} />
                {busy ? 'Dérivation Argon2id…' : 'Déverrouiller la synchronisation'}
              </button>
              <button
                type="button"
                onClick={() => setShowRecoverForm(true)}
                className="w-full min-h-[44px] text-xs text-indigo-600 font-medium"
              >
                Mot de passe oublié ? Utiliser la clé de secours 256 bits
              </button>
            </form>
          ) : (
            <form onSubmit={handleRecoverWithKey} className="space-y-3">
              <input
                type="text"
                required
                value={recoveryInput}
                onChange={e => setRecoveryInput(e.target.value)}
                placeholder="Clé de secours (Base64URL)"
                className="w-full px-3 py-2.5 text-xs font-mono bg-white border border-gray-200 rounded-xl outline-none"
              />
              <input
                type="password"
                required
                minLength={8}
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Nouveau mot de passe maître"
                className="w-full px-3 py-2.5 text-sm bg-white border border-gray-200 rounded-xl outline-none"
              />
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={busy}
                  className="flex-1 min-h-[44px] rounded-xl bg-indigo-600 text-white text-xs font-semibold"
                >
                  Restaurer l&apos;accès
                </button>
                <button
                  type="button"
                  onClick={() => setShowRecoverForm(false)}
                  className="min-h-[44px] px-4 rounded-xl border border-gray-300 text-xs text-gray-600"
                >
                  Annuler
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* Configured AND UNLOCKED */}
      {config.configured && session && (
        <div className="space-y-4">
          {/* Tab Strip */}
          <div className="flex gap-1 overflow-x-auto pb-1 border-b border-gray-100">
            {(
              [
                ['status', 'État', RefreshCw],
                ['pairing', 'Jumelage QR', QrCode],
                ['devices', 'Appareils', Laptop],
                ['conflicts', `Conflits (${openConflictCount})`, AlertTriangle],
                ['security', 'Sécurité', KeyRound],
              ] as const
            ).map(([key, label, Icon]) => (
              <button
                key={key}
                onClick={() => {
                  setTab(key);
                  if (key === 'devices') loadDevices();
                }}
                className={`min-h-[44px] px-3 rounded-xl text-xs font-semibold flex items-center gap-1.5 shrink-0 transition-colors ${
                  tab === key
                    ? 'bg-indigo-600 text-white'
                    : key === 'conflicts' && openConflictCount > 0
                      ? 'bg-amber-50 text-amber-800 border border-amber-200'
                      : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                <Icon size={14} />
                {label}
              </button>
            ))}
          </div>

          {tab === 'status' && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2.5">
                <div className="p-3 bg-gray-50 rounded-xl border border-gray-100">
                  <p className="text-[11px] uppercase tracking-wider text-gray-400 font-semibold">Opérations en attente</p>
                  <p className="text-lg font-bold text-gray-900 tabular-nums mt-0.5">{pendingOutboxCount}</p>
                </div>
                <div className="p-3 bg-gray-50 rounded-xl border border-gray-100">
                  <p className="text-[11px] uppercase tracking-wider text-gray-400 font-semibold">Époque cryptographique</p>
                  <p className="text-lg font-bold text-indigo-700 tabular-nums mt-0.5">#{session.keyring.currentKeyEpoch}</p>
                </div>
              </div>

              <div className="text-xs text-gray-500 font-mono bg-gray-50 px-3 py-2 rounded-xl border border-gray-100 truncate">
                Serveur : {session.serverUrl} · Coffre {session.vaultId.slice(0, 8)}
              </div>

              {progress && progress.phase !== 'idle' && (
                <div className="text-xs text-indigo-700 bg-indigo-50 px-3 py-2 rounded-xl border border-indigo-100">
                  Phase : {progress.phase} ({progress.pushedCount} envoyées, {progress.pulledCount} reçues)
                </div>
              )}

              <div className="flex gap-2">
                <button
                  onClick={handleSyncNow}
                  disabled={busy}
                  className="flex-1 min-h-[44px] px-4 rounded-xl bg-indigo-600 text-white text-sm font-semibold flex items-center justify-center gap-2 active:bg-indigo-700 disabled:opacity-50"
                >
                  <RefreshCw size={16} className={busy ? 'animate-spin' : ''} />
                  {busy ? 'Synchronisation…' : 'Synchroniser maintenant'}
                </button>
                <button
                  onClick={lockVaultNow}
                  className="min-h-[44px] px-4 rounded-xl border border-gray-200 text-gray-700 text-xs font-semibold flex items-center gap-1.5"
                >
                  <Lock size={14} /> Verrouiller
                </button>
              </div>
            </div>
          )}

          {tab === 'pairing' && (
            <div className="space-y-4">
              {!pairingData ? (
                <div className="space-y-3">
                  <p className="text-xs text-gray-600 leading-relaxed">
                    Générez un QR code temporaire (valable 10 minutes) contenant le secret de session
                    permettant à Noteor Desktop ou à un second navigateur de recevoir vos clés de chiffrement sans jamais exposer vos secrets au serveur.
                  </p>
                  <button
                    onClick={handleStartPairing}
                    disabled={busy}
                    className="w-full min-h-[44px] rounded-xl bg-indigo-600 text-white text-sm font-semibold flex items-center justify-center gap-2"
                  >
                    <QrCode size={16} /> Générer un QR code de jumelage
                  </button>
                </div>
              ) : (
                <div className="space-y-3 text-center">
                  <img
                    src={pairingData.qrImageUrl}
                    alt="QR Code de jumelage Noteor"
                    className="mx-auto rounded-xl border border-gray-200"
                  />
                  <div className="text-left">
                    <label className="block text-[11px] font-semibold text-gray-500 mb-1">
                      Ou copier le code texte sur le second appareil :
                    </label>
                    <textarea
                      readOnly
                      rows={3}
                      value={pairingData.qrString}
                      onClick={e => (e.target as HTMLTextAreaElement).select()}
                      className="w-full px-3 py-2 text-[11px] font-mono bg-gray-50 border border-gray-200 rounded-xl"
                    />
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={handleApprovePairing}
                      disabled={busy}
                      className="flex-1 min-h-[44px] rounded-xl bg-emerald-600 text-white text-xs font-semibold"
                    >
                      Approuver et envoyer les clés
                    </button>
                    <button
                      onClick={() => setPairingData(null)}
                      className="min-h-[44px] px-4 rounded-xl border border-gray-200 text-xs text-gray-600"
                    >
                      Annuler
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {tab === 'devices' && (
            <div className="space-y-2.5">
              {devices.length === 0 ? (
                <p className="text-xs text-gray-500 py-4 text-center">Chargement des appareils…</p>
              ) : (
                devices.map(d => {
                  const isSelf = d.device_id === session.deviceId;
                  return (
                    <div
                      key={d.device_id}
                      className="flex items-center gap-3 p-3 rounded-xl border border-gray-200 bg-white"
                    >
                      <Laptop size={18} className={d.status === 'active' ? 'text-indigo-600' : 'text-gray-300'} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-gray-900 truncate">
                          {d.device_name} {isSelf ? '(cet appareil)' : ''}
                        </p>
                        <p className="text-xs text-gray-500">
                          {d.device_type} · {d.status === 'active' ? 'Actif' : 'Révoqué'} · {d.device_id.slice(0, 8)}
                        </p>
                      </div>
                      {!isSelf && d.status === 'active' && (
                        <button
                          onClick={() => handleRevoke(d.device_id)}
                          disabled={busy}
                          className="min-h-[44px] px-3 rounded-xl border border-red-200 text-red-600 text-xs font-semibold hover:bg-red-50 flex items-center gap-1"
                        >
                          <Trash2 size={13} /> Révoquer
                        </button>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          )}

          {tab === 'conflicts' && <ConflictResolver engine={currentEngine} />}

          {tab === 'security' && (
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1.5">
                  Verrouillage automatique en RAM après inactivité
                </label>
                <div className="grid grid-cols-3 gap-2">
                  {[5, 15, 60].map(mins => (
                    <button
                      key={mins}
                      onClick={() => setAutoLockMinutes(mins)}
                      className={`min-h-[44px] rounded-xl text-xs font-semibold border ${
                        config.autoLockMinutes === mins
                          ? 'border-indigo-600 bg-indigo-50 text-indigo-700'
                          : 'border-gray-200 text-gray-600'
                      }`}
                    >
                      {mins} min
                    </button>
                  ))}
                </div>
              </div>

              <div className="p-3.5 bg-amber-50 border border-amber-200 rounded-xl space-y-2.5">
                <p className="text-xs font-semibold text-amber-900">Clé maître de secours (256 bits)</p>
                <p className="text-xs text-amber-800 leading-relaxed">
                  Conservez cette clé hors ligne (coffre gestionnaire ou papier). Elle permet de réinitialiser le mot de passe maître de cet appareil.
                </p>
                {showRecoverySecret ? (
                  <div className="space-y-2">
                    <code className="block p-2.5 bg-white border border-amber-200 rounded-lg text-xs font-mono break-all select-all">
                      {exportRecoveryKeyString(session)}
                    </code>
                    <div className="flex gap-2">
                      <button
                        onClick={() => {
                          navigator.clipboard?.writeText(exportRecoveryKeyString(session));
                          setCopiedRecovery(true);
                          setTimeout(() => setCopiedRecovery(false), 2000);
                        }}
                        className="flex-1 min-h-[44px] rounded-xl bg-amber-700 text-white text-xs font-semibold flex items-center justify-center gap-1.5"
                      >
                        {copiedRecovery ? <Check size={14} /> : <Copy size={14} />}
                        {copiedRecovery ? 'Copiée' : 'Copier'}
                      </button>
                      <button
                        onClick={() => setShowRecoverySecret(false)}
                        className="min-h-[44px] px-3 rounded-xl border border-amber-300 text-xs text-amber-900"
                      >
                        Masquer
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => setShowRecoverySecret(true)}
                    className="w-full min-h-[44px] rounded-xl bg-white border border-amber-300 text-amber-900 text-xs font-semibold"
                  >
                    Afficher temporairement la clé de secours
                  </button>
                )}
              </div>

              <button
                onClick={async () => {
                  if (confirm('Dissocier cet appareil du serveur de synchronisation ? Vos notes locales seront conservées.')) {
                    await resetLocalSyncConfig();
                  }
                }}
                className="w-full min-h-[44px] rounded-xl border border-red-200 text-red-600 text-xs font-semibold hover:bg-red-50"
              >
                Dissocier cet appareil de la synchronisation
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );

  if (embedded) {
    return (
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-indigo-100 flex items-center justify-center">
            <Shield size={16} className="text-indigo-600" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-gray-800">Synchronisation E2EE</h2>
            <p className="text-xs text-gray-500">Chiffrement de bout en bout (XChaCha20-Poly1305)</p>
          </div>
        </div>
        {contentBody}
      </section>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end md:items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-full md:max-w-lg max-h-[90vh] overflow-y-auto bg-white rounded-t-2xl md:rounded-2xl shadow-2xl p-5 safe-bottom">
        <div className="flex items-center gap-2 pb-3 mb-4 border-b border-gray-100">
          <Shield size={18} className="text-indigo-600" />
          <h2 className="text-base font-semibold text-gray-900 flex-1">Synchronisation Noteor</h2>
          {onClose && (
            <button onClick={onClose} className="p-2 text-gray-400 active:text-gray-700" aria-label="Fermer">
              <X size={20} />
            </button>
          )}
        </div>
        {contentBody}
      </div>
    </div>
  );
}
