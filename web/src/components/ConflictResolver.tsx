import { useState } from 'react';
import { useLiveQuery } from 'dexie-react-hooks';
import { AlertTriangle, Check, Copy, GitMerge, Trash2, X } from 'lucide-react';
import { db } from '../db';
import type { PwaSyncEngine } from '../sync/engine';
import type { SyncConflictRecord } from '../types';

interface Props {
  engine: PwaSyncEngine | null;
  onClose?: () => void;
}

export default function ConflictResolver({ engine, onClose }: Props) {
  const conflicts = useLiveQuery(
    () => db.sync_conflicts.where('status').equals('open').toArray(),
    [],
  ) ?? [];

  const [editingId, setEditingId] = useState<string | null>(null);
  const [mergeTitle, setMergeTitle] = useState('');
  const [mergeContent, setMergeContent] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState('');

  async function resolve(
    conflict: SyncConflictRecord,
    choice: 'keep_local' | 'keep_remote' | 'keep_both' | 'manual_merge',
  ) {
    if (!engine) {
      setError('Déverrouillez le coffre pour enregistrer la résolution dans le journal de synchronisation.');
      return;
    }
    setBusyId(conflict.conflict_id);
    setError('');
    try {
      await engine.resolveConflict(
        conflict.conflict_id,
        choice,
        choice === 'manual_merge' ? { title: mergeTitle, content: mergeContent } : undefined,
      );
      if (editingId === conflict.conflict_id) setEditingId(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erreur lors de la résolution du conflit.');
    } finally {
      setBusyId(null);
    }
  }

  function startManualMerge(c: SyncConflictRecord) {
    const loc = c.local_snapshot;
    const rem = c.remote_snapshot;
    setEditingId(c.conflict_id);
    setMergeTitle(String(loc.title ?? rem.title ?? ''));
    setMergeContent(
      `${String(loc.content ?? '')}\n\n---\n\n${String(rem.content ?? '')}`,
    );
  }

  if (conflicts.length === 0) {
    return (
      <div className="p-6 text-center bg-emerald-50 border border-emerald-100 rounded-xl">
        <Check size={24} className="mx-auto text-emerald-600 mb-2" />
        <p className="text-sm font-semibold text-emerald-900">Aucun conflit ouvert</p>
        <p className="text-xs text-emerald-700 mt-1">Toutes les branches de révisions sont convergées.</p>
        {onClose && (
          <button
            onClick={onClose}
            className="mt-4 px-4 min-h-[44px] rounded-xl bg-emerald-600 text-white text-sm font-medium"
          >
            Fermer
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="px-3 py-2.5 bg-red-50 border border-red-200 rounded-xl text-xs text-red-700">
          {error}
        </div>
      )}

      {conflicts.map(c => {
        const loc = c.local_snapshot;
        const rem = c.remote_snapshot;
        const isEditing = editingId === c.conflict_id;
        const isBusy = busyId === c.conflict_id;

        return (
          <div key={c.conflict_id} className="border border-amber-200 bg-amber-50/40 rounded-2xl p-4 space-y-3">
            <div className="flex items-start gap-2.5">
              <AlertTriangle size={18} className="text-amber-600 shrink-0 mt-0.5" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-gray-900">
                  Conflit sur {c.entity_type === 'note' ? 'la note' : c.entity_type} :{' '}
                  {String(loc.title ?? rem.title ?? c.entity_id.slice(0, 8))}
                </p>
                <p className="text-xs text-gray-500">
                  Détecté le {new Date(c.detected_at).toLocaleString('fr-FR')} · Appareil distant {c.remote_device_id.slice(0, 8)}
                </p>
              </div>
            </div>

            {isEditing ? (
              <div className="space-y-2.5 bg-white p-3 rounded-xl border border-gray-200">
                <label className="block text-xs font-medium text-gray-600">Titre fusionné</label>
                <input
                  type="text"
                  value={mergeTitle}
                  onChange={e => setMergeTitle(e.target.value)}
                  className="w-full px-3 py-2.5 text-sm border border-gray-200 rounded-lg outline-none focus:border-indigo-500"
                />
                <label className="block text-xs font-medium text-gray-600">Contenu fusionné</label>
                <textarea
                  rows={6}
                  value={mergeContent}
                  onChange={e => setMergeContent(e.target.value)}
                  className="w-full px-3 py-2 text-xs font-mono border border-gray-200 rounded-lg outline-none focus:border-indigo-500"
                />
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={() => resolve(c, 'manual_merge')}
                    disabled={isBusy}
                    className="flex-1 min-h-[44px] px-3 rounded-xl bg-indigo-600 text-white text-xs font-medium flex items-center justify-center gap-1.5"
                  >
                    <Check size={14} /> Enregistrer la fusion
                  </button>
                  <button
                    onClick={() => setEditingId(null)}
                    className="min-h-[44px] px-4 rounded-xl border border-gray-200 text-gray-600 text-xs font-medium flex items-center gap-1"
                  >
                    <X size={14} /> Annuler
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="bg-white border border-gray-200 rounded-xl p-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold uppercase tracking-wider text-indigo-600">
                        Version locale (cet appareil)
                      </span>
                      {Boolean(loc.deleted || loc.is_deleted) && (
                        <span className="text-[11px] font-medium text-red-600 flex items-center gap-1">
                          <Trash2 size={11} /> Supprimée
                        </span>
                      )}
                    </div>
                    {loc.title !== undefined && (
                      <p className="text-sm font-semibold text-gray-900 truncate">{String(loc.title || 'Sans titre')}</p>
                    )}
                    <p className="text-xs text-gray-600 whitespace-pre-wrap max-h-36 overflow-y-auto font-mono">
                      {loc.deleted ? '(Note supprimée définitivement localement)' : String(loc.content ?? '')}
                    </p>
                  </div>

                  <div className="bg-white border border-gray-200 rounded-xl p-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-700">
                        Version distante
                      </span>
                      {Boolean(rem.deleted || rem.is_deleted) && (
                        <span className="text-[11px] font-medium text-red-600 flex items-center gap-1">
                          <Trash2 size={11} /> Supprimée
                        </span>
                      )}
                    </div>
                    {rem.title !== undefined && (
                      <p className="text-sm font-semibold text-gray-900 truncate">{String(rem.title || 'Sans titre')}</p>
                    )}
                    <p className="text-xs text-gray-600 whitespace-pre-wrap max-h-36 overflow-y-auto font-mono">
                      {rem.deleted ? '(Note supprimée définitivement à distance)' : String(rem.content ?? '')}
                    </p>
                  </div>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
                  <button
                    onClick={() => resolve(c, 'keep_local')}
                    disabled={isBusy}
                    className="min-h-[44px] px-3 rounded-xl bg-indigo-600 text-white text-xs font-medium active:bg-indigo-700 disabled:opacity-50"
                  >
                    Garder locale
                  </button>
                  <button
                    onClick={() => resolve(c, 'keep_remote')}
                    disabled={isBusy}
                    className="min-h-[44px] px-3 rounded-xl bg-emerald-600 text-white text-xs font-medium active:bg-emerald-700 disabled:opacity-50"
                  >
                    Prendre distante
                  </button>
                  <button
                    onClick={() => resolve(c, 'keep_both')}
                    disabled={isBusy}
                    className="min-h-[44px] px-3 rounded-xl border border-gray-300 bg-white text-gray-800 text-xs font-medium flex items-center justify-center gap-1 active:bg-gray-50 disabled:opacity-50"
                  >
                    <Copy size={13} /> Garder les deux
                  </button>
                  <button
                    onClick={() => startManualMerge(c)}
                    disabled={isBusy}
                    className="min-h-[44px] px-3 rounded-xl border border-indigo-200 bg-indigo-50 text-indigo-700 text-xs font-medium flex items-center justify-center gap-1 active:bg-indigo-100 disabled:opacity-50"
                  >
                    <GitMerge size={13} /> Fusionner
                  </button>
                </div>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
