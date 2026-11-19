import Dexie, { type Table } from 'dexie';
import type {
  Note,
  Category,
  Folder,
  Tag,
  NoteTag,
  Attachment,
  SyncOutboxOperation,
  SyncStateRecord,
  SyncRevisionRecord,
  SyncConflictRecord,
  SyncPendingOpRecord,
  SyncTombstoneRecord,
  SyncEntityType,
  SyncOperationType,
} from '../types';
import { generateUuidV4, normalizeTagName, normalizeUtcTimestamp } from '../sync/ids';

class NoteorDB extends Dexie {
  notes!: Table<Note, number>;
  categories!: Table<Category, number>;
  folders!: Table<Folder, number>;
  tags!: Table<Tag, number>;
  note_tags!: Table<NoteTag>;
  attachments!: Table<Attachment, number>;
  sync_operations!: Table<SyncOutboxOperation, number>;
  sync_state!: Table<SyncStateRecord, string>;
  sync_revisions!: Table<SyncRevisionRecord, number>;
  sync_conflicts!: Table<SyncConflictRecord, number>;
  sync_pending_ops!: Table<SyncPendingOpRecord, number>;
  sync_tombstones!: Table<SyncTombstoneRecord, string>;

  constructor() {
    super('noteor');
    this.version(1).stores({
      notes: '++id, title, category_id, folder_id, updated_at, is_deleted',
      categories: '++id, name, parent_id, sort_order',
      folders: '++id, name, parent_id',
      tags: '++id, &name',
      note_tags: '[note_id+tag_id], note_id, tag_id',
      attachments: '++id, note_id, type, created_at',
    });

    this.version(2)
      .stores({
        notes: '++id, &sync_id, title, category_id, folder_id, updated_at, is_deleted',
        categories: '++id, &sync_id, name, parent_id, sort_order',
        folders: '++id, &sync_id, name, parent_id',
        tags: '++id, &sync_id, &name',
        note_tags: '[note_id+tag_id], &sync_id, note_id, tag_id',
        attachments: '++id, &sync_id, media_id, note_id, type, created_at',
        sync_operations: '++id, &operation_id, device_seq, status, [status+device_seq], entity_type, entity_id',
        sync_state: '&key',
        sync_revisions:
          '++id, &[entity_type+entity_id+revision_id], [entity_type+entity_id], [entity_type+entity_id+is_head]',
        sync_conflicts: '++id, &conflict_id, status, [entity_type+entity_id], detected_at',
        sync_pending_ops: '++id, &operation_id, server_seq, entity_type, entity_id',
        sync_tombstones: '&entity_key, entity_type, entity_id',
      })
      .upgrade(async tx => {
        const tsNow = new Date().toISOString();
        const noteSyncById = new Map<number, string>();

        // 1. Categories
        const cats = await tx.table<Category>('categories').toArray();
        for (const c of cats) {
          const sync_id = c.sync_id || generateUuidV4();
          const revision_id = c.revision_id || generateUuidV4();
          const created_at = normalizeUtcTimestamp(c.created_at ?? tsNow);
          const updated_at = normalizeUtcTimestamp(c.updated_at ?? tsNow);
          await tx.table<Category>('categories').update(c.id!, {
            sync_id,
            revision_id,
            parent_revision_ids: c.parent_revision_ids ?? [],
            created_at,
            updated_at,
          });
          await tx.table<SyncRevisionRecord>('sync_revisions').put({
            entity_type: 'category',
            entity_id: sync_id,
            revision_id,
            parent_revision_ids: [],
            is_head: 1,
            operation_type: 'entity.upsert',
            created_at: updated_at,
            device_id: 'local',
          });
        }

        // 2. Folders
        const folders = await tx.table<Folder>('folders').toArray();
        for (const f of folders) {
          const sync_id = f.sync_id || generateUuidV4();
          const revision_id = f.revision_id || generateUuidV4();
          const created_at = normalizeUtcTimestamp(f.created_at);
          const updated_at = normalizeUtcTimestamp(f.updated_at ?? created_at);
          await tx.table<Folder>('folders').update(f.id!, {
            sync_id,
            revision_id,
            parent_revision_ids: f.parent_revision_ids ?? [],
            created_at,
            updated_at,
          });
          await tx.table<SyncRevisionRecord>('sync_revisions').put({
            entity_type: 'folder',
            entity_id: sync_id,
            revision_id,
            parent_revision_ids: [],
            is_head: 1,
            operation_type: 'entity.upsert',
            created_at: updated_at,
            device_id: 'local',
          });
        }

        // 3. Tags (normalize NFC + lowercase, deduplicate if needed)
        const tags = await tx.table<Tag>('tags').toArray();
        const canonicalTagByNorm = new Map<string, Tag>();
        const tagRemap = new Map<number, number>();
        for (const t of tags) {
          const norm = normalizeTagName(t.name);
          const existing = canonicalTagByNorm.get(norm);
          if (existing) {
            tagRemap.set(t.id!, existing.id!);
            await tx.table<Tag>('tags').delete(t.id!);
            continue;
          }
          const sync_id = t.sync_id || generateUuidV4();
          const revision_id = t.revision_id || generateUuidV4();
          const created_at = normalizeUtcTimestamp(t.created_at ?? tsNow);
          const updated_at = normalizeUtcTimestamp(t.updated_at ?? tsNow);
          const updatedTag: Tag = {
            ...t,
            name: norm,
            sync_id,
            revision_id,
            parent_revision_ids: t.parent_revision_ids ?? [],
            created_at,
            updated_at,
          };
          await tx.table<Tag>('tags').update(t.id!, updatedTag);
          canonicalTagByNorm.set(norm, updatedTag);
          await tx.table<SyncRevisionRecord>('sync_revisions').put({
            entity_type: 'tag',
            entity_id: sync_id,
            revision_id,
            parent_revision_ids: [],
            is_head: 1,
            operation_type: 'entity.upsert',
            created_at: updated_at,
            device_id: 'local',
          });
        }

        // 4. First pass on Notes: assign UUIDs & normalize timestamps
        const notes = await tx.table<Note>('notes').toArray();
        for (const n of notes) {
          const sync_id = n.sync_id || generateUuidV4();
          noteSyncById.set(n.id!, sync_id);
        }

        // Second pass on Notes: upgrade numeric wikilinks [[Title|123]] -> [[Title|@uuid]]
        for (const n of notes) {
          const sync_id = noteSyncById.get(n.id!)!;
          const revision_id = n.revision_id || generateUuidV4();
          const created_at = normalizeUtcTimestamp(n.created_at);
          const updated_at = normalizeUtcTimestamp(n.updated_at);
          const deleted_at = n.deleted_at ? normalizeUtcTimestamp(n.deleted_at) : undefined;
          const upgradedContent = (n.content ?? '').replace(
            /\[\[([^\]|[\n]+)\|(\d+)\]\]/g,
            (full, linkTitle, numStr) => {
              const targetUuid = noteSyncById.get(Number(numStr));
              return targetUuid ? `[[${linkTitle}|@${targetUuid}]]` : full;
            },
          );
          await tx.table<Note>('notes').update(n.id!, {
            sync_id,
            revision_id,
            parent_revision_ids: n.parent_revision_ids ?? [],
            content: upgradedContent,
            created_at,
            updated_at,
            deleted_at,
            metadata: n.metadata ?? {},
          });
          await tx.table<SyncRevisionRecord>('sync_revisions').put({
            entity_type: 'note',
            entity_id: sync_id,
            revision_id,
            parent_revision_ids: [],
            is_head: 1,
            operation_type: n.is_deleted ? 'entity.trash' : 'entity.upsert',
            created_at: updated_at,
            device_id: 'local',
          });
        }

        // 5. Note-Tags (remap deduplicated tags + assign sync_ids)
        const links = await tx.table<NoteTag>('note_tags').toArray();
        for (const l of links) {
          const remappedTagId = tagRemap.get(l.tag_id) ?? l.tag_id;
          const sync_id = l.sync_id || generateUuidV4();
          const revision_id = l.revision_id || generateUuidV4();
          const created_at = normalizeUtcTimestamp(l.created_at ?? tsNow);
          if (remappedTagId !== l.tag_id) {
            await tx.table<NoteTag>('note_tags').delete([l.note_id, l.tag_id]);
            const dupe = await tx.table<NoteTag>('note_tags').get([l.note_id, remappedTagId]);
            if (!dupe) {
              await tx.table<NoteTag>('note_tags').put({
                note_id: l.note_id,
                tag_id: remappedTagId,
                sync_id,
                revision_id,
                parent_revision_ids: [],
                created_at,
              });
            }
          } else {
            await tx.table<NoteTag>('note_tags').update([l.note_id, l.tag_id], {
              sync_id,
              revision_id,
              parent_revision_ids: l.parent_revision_ids ?? [],
              created_at,
            });
          }
        }

        // 6. Attachments
        const atts = await tx.table<Attachment>('attachments').toArray();
        for (const a of atts) {
          const sync_id = a.sync_id || generateUuidV4();
          const media_id = a.media_id || generateUuidV4();
          const revision_id = a.revision_id || generateUuidV4();
          const created_at = normalizeUtcTimestamp(a.created_at);
          await tx.table<Attachment>('attachments').update(a.id!, {
            sync_id,
            media_id,
            revision_id,
            parent_revision_ids: a.parent_revision_ids ?? [],
            created_at,
            mime_type: a.mime_type || a.data?.type || 'application/octet-stream',
            original_size: a.original_size ?? a.data?.size ?? 0,
            media_status: a.media_status ?? 'local_only',
          });
        }
      });
  }
}

export const db = new NoteorDB();

export function now(): string {
  return new Date().toISOString();
}

// ── Atomic Outbox & Revision Helpers ────────────────────────────────────────

function allSyncTables() {
  return [
    db.notes,
    db.categories,
    db.folders,
    db.tags,
    db.note_tags,
    db.attachments,
    db.sync_operations,
    db.sync_state,
    db.sync_revisions,
    db.sync_tombstones,
  ];
}

export async function getSyncStateValue(key: string): Promise<string | undefined> {
  const rec = await db.sync_state.get(key);
  return rec?.value;
}

export async function setSyncStateValue(key: string, value: string): Promise<void> {
  await db.sync_state.put({ key, value });
}

async function isSyncOutboxEnabledInTx(): Promise<{ enabled: boolean; deviceId: string }> {
  const vault = await db.sync_state.get('vault_id');
  if (!vault?.value) return { enabled: false, deviceId: 'local' };
  const dev = await db.sync_state.get('device_id');
  return { enabled: true, deviceId: dev?.value || 'local' };
}

async function nextDeviceSeqInTx(): Promise<string> {
  const rec = await db.sync_state.get('local_device_seq');
  const next = BigInt(rec?.value ?? '0') + 1n;
  const nextStr = next.toString();
  await db.sync_state.put({ key: 'local_device_seq', value: nextStr });
  return nextStr;
}

async function getHeadRevisionsInTx(entityType: SyncEntityType, entityId: string): Promise<string[]> {
  const heads = await db.sync_revisions
    .where('[entity_type+entity_id+is_head]')
    .equals([entityType, entityId, 1])
    .toArray();
  return heads.map(h => h.revision_id);
}

async function recordMutationInTx(params: {
  operation_type: SyncOperationType;
  entity_type: SyncEntityType;
  entity_id: string;
  payload_data: Record<string, unknown>;
  created_at: string;
  explicit_parents?: string[];
  media_local_attachment_id?: number;
  media_id?: string;
}): Promise<{ revision_id: string; parent_revision_ids: string[] }> {
  const { enabled, deviceId } = await isSyncOutboxEnabledInTx();
  const parent_revision_ids =
    params.explicit_parents ?? (await getHeadRevisionsInTx(params.entity_type, params.entity_id));
  const revision_id = generateUuidV4();

  // Retire previous heads
  const prevHeads = await db.sync_revisions
    .where('[entity_type+entity_id+is_head]')
    .equals([params.entity_type, params.entity_id, 1])
    .toArray();
  for (const h of prevHeads) {
    await db.sync_revisions.update(h.id!, { is_head: 0 });
  }

  await db.sync_revisions.put({
    entity_type: params.entity_type,
    entity_id: params.entity_id,
    revision_id,
    parent_revision_ids,
    is_head: 1,
    operation_type: params.operation_type,
    created_at: params.created_at,
    device_id: deviceId,
  });

  if (enabled) {
    const device_seq = await nextDeviceSeqInTx();
    const epochRec = await db.sync_state.get('current_key_epoch');
    const key_epoch = Number(epochRec?.value ?? '1') || 1;
    await db.sync_operations.add({
      operation_id: generateUuidV4(),
      device_seq,
      key_epoch,
      operation_type: params.operation_type,
      entity_type: params.entity_type,
      entity_id: params.entity_id,
      revision_id,
      parent_revision_ids,
      created_at: params.created_at,
      payload_data: params.payload_data,
      status: 'pending',
      media_local_attachment_id: params.media_local_attachment_id,
      media_id: params.media_id,
    });
  }

  return { revision_id, parent_revision_ids };
}

async function resolveCategorySyncId(localId?: number): Promise<string | null> {
  if (!localId) return null;
  const cat = await db.categories.get(localId);
  return cat?.sync_id ?? null;
}

async function resolveFolderSyncId(localId?: number): Promise<string | null> {
  if (!localId) return null;
  const folder = await db.folders.get(localId);
  return folder?.sync_id ?? null;
}

async function noteToSyncData(note: Note): Promise<Record<string, unknown>> {
  return {
    title: note.title,
    content: note.content,
    category_id: await resolveCategorySyncId(note.category_id),
    folder_id: await resolveFolderSyncId(note.folder_id),
    is_deleted: Boolean(note.is_deleted),
    deleted_at: note.deleted_at ?? null,
    created_at: note.created_at,
    updated_at: note.updated_at,
    metadata: note.metadata ?? {},
  };
}

/**
 * When a device joins or initializes a vault while already holding local offline data,
 * enqueue initial operations in dependency order for all entities not yet queued.
 */
export async function enqueueExistingVaultSnapshot(): Promise<number> {
  return db.transaction('rw', allSyncTables(), async () => {
    const { enabled } = await isSyncOutboxEnabledInTx();
    if (!enabled) return 0;

    const existingEntityKeys = new Set<string>();
    for (const op of await db.sync_operations.toArray()) {
      existingEntityKeys.add(`${op.entity_type}:${op.entity_id}`);
    }
    for (const rev of await db.sync_revisions.toArray()) {
      if (rev.device_id !== 'local') {
        existingEntityKeys.add(`${rev.entity_type}:${rev.entity_id}`);
      }
    }
    let queued = 0;

    // 1. Categories (parents before children if ordered, else two passes cleanly)
    const cats = await db.categories.toArray();
    for (const c of cats) {
      const syncId = c.sync_id || generateUuidV4();
      if (!c.sync_id) await db.categories.update(c.id!, { sync_id: syncId });
      if (existingEntityKeys.has(`category:${syncId}`)) continue;
      const ts = c.updated_at || now();
      await recordMutationInTx({
        operation_type: 'entity.upsert',
        entity_type: 'category',
        entity_id: syncId,
        created_at: ts,
        payload_data: {
          name: c.name,
          parent_id: await resolveCategorySyncId(c.parent_id),
          color: c.color,
          sort_order: c.sort_order,
          created_at: c.created_at || ts,
          updated_at: ts,
        },
      });
      queued++;
    }

    // 2. Folders
    const folders = await db.folders.toArray();
    for (const f of folders) {
      const syncId = f.sync_id || generateUuidV4();
      if (!f.sync_id) await db.folders.update(f.id!, { sync_id: syncId });
      if (existingEntityKeys.has(`folder:${syncId}`)) continue;
      const ts = f.updated_at || f.created_at || now();
      await recordMutationInTx({
        operation_type: 'entity.upsert',
        entity_type: 'folder',
        entity_id: syncId,
        created_at: ts,
        payload_data: {
          name: f.name,
          parent_id: await resolveFolderSyncId(f.parent_id),
          created_at: f.created_at || ts,
          updated_at: ts,
        },
      });
      queued++;
    }

    // 3. Tags
    const tags = await db.tags.toArray();
    for (const t of tags) {
      const syncId = t.sync_id || generateUuidV4();
      if (!t.sync_id) await db.tags.update(t.id!, { sync_id: syncId });
      if (existingEntityKeys.has(`tag:${syncId}`)) continue;
      const ts = t.updated_at || now();
      await recordMutationInTx({
        operation_type: 'entity.upsert',
        entity_type: 'tag',
        entity_id: syncId,
        created_at: ts,
        payload_data: {
          name: normalizeTagName(t.name),
          color: t.color,
          created_at: t.created_at || ts,
          updated_at: ts,
        },
      });
      queued++;
    }

    // 4. Notes
    const notes = await db.notes.toArray();
    for (const n of notes) {
      const syncId = n.sync_id || generateUuidV4();
      if (!n.sync_id) await db.notes.update(n.id!, { sync_id: syncId });
      if (existingEntityKeys.has(`note:${syncId}`)) continue;
      await recordMutationInTx({
        operation_type: n.is_deleted ? 'entity.trash' : 'entity.upsert',
        entity_type: 'note',
        entity_id: syncId,
        created_at: n.updated_at || now(),
        payload_data: await noteToSyncData(n),
      });
      queued++;
    }

    // 5. Note-Tag Relations
    const noteTags = await db.note_tags.toArray();
    for (const nt of noteTags) {
      const note = await db.notes.get(nt.note_id);
      const tag = await db.tags.get(nt.tag_id);
      if (!note?.sync_id || !tag?.sync_id) continue;
      const syncId = nt.sync_id || generateUuidV4();
      if (!nt.sync_id) await db.note_tags.update([nt.note_id, nt.tag_id], { sync_id: syncId });
      if (existingEntityKeys.has(`relation:${syncId}`)) continue;
      const ts = nt.created_at || now();
      await recordMutationInTx({
        operation_type: 'entity.upsert',
        entity_type: 'relation',
        entity_id: syncId,
        created_at: ts,
        payload_data: {
          relation_type: 'note_tag',
          note_id: note.sync_id,
          tag_id: tag.sync_id,
          created_at: ts,
        },
      });
      queued++;
    }

    // 6. Attachments
    const atts = await db.attachments.toArray();
    for (const a of atts) {
      const note = await db.notes.get(a.note_id);
      if (!note?.sync_id) continue;
      const syncId = a.sync_id || generateUuidV4();
      const mediaId = a.media_id || generateUuidV4();
      if (!a.sync_id || !a.media_id) {
        await db.attachments.update(a.id!, {
          sync_id: syncId,
          media_id: mediaId,
          media_status: 'pending_upload',
        });
      }
      if (existingEntityKeys.has(`attachment:${syncId}`)) continue;
      await recordMutationInTx({
        operation_type: 'media.register',
        entity_type: 'attachment',
        entity_id: syncId,
        created_at: a.created_at || now(),
        media_local_attachment_id: a.id!,
        media_id: mediaId,
        payload_data: {
          note_id: note.sync_id,
          media_id: mediaId,
          media_type: a.type,
          media_crypto_version: 1,
          key_epoch: a.key_epoch ?? 1,
          encrypted_size: a.encrypted_size ?? 0,
          encrypted_sha256: a.encrypted_sha256 ?? '',
          original_size: a.original_size ?? a.data.size,
          mime_type: a.mime_type || a.data.type || 'application/octet-stream',
          file_name: a.filename,
          duration: a.duration ?? null,
          thumbnail_data_url: a.thumbnail ?? null,
          created_at: a.created_at,
        },
      });
      queued++;
    }

    return queued;
  });
}

// ── Notes ───────────────────────────────────────────────────────────────────

export async function getNotes(opts: {
  search?: string;
  categoryId?: number;
  folderId?: number;
  tagId?: number;
  typeFilter?: string;
  dateFilter?: string;
  deleted?: boolean;
}): Promise<Note[]> {
  const { search, categoryId, folderId, tagId, typeFilter, dateFilter, deleted = false } = opts;

  let notes = await db.notes.filter(n => n.is_deleted === deleted).toArray();

  if (search) {
    const q = search.toLowerCase();
    notes = notes.filter(n =>
      n.title.toLowerCase().includes(q) || n.content.toLowerCase().includes(q)
    );
  }
  if (categoryId !== undefined) notes = notes.filter(n => n.category_id === categoryId);
  if (folderId !== undefined) notes = notes.filter(n => n.folder_id === folderId);

  if (tagId !== undefined) {
    const taggedIds = (await db.note_tags.where('tag_id').equals(tagId).toArray()).map(r => r.note_id);
    notes = notes.filter(n => n.id !== undefined && taggedIds.includes(n.id));
  }

  if (typeFilter && typeFilter !== 'all') {
    const attachsByNote = await db.attachments.where('type').equals(typeFilter).toArray();
    const noteIds = new Set(attachsByNote.map(a => a.note_id));
    notes = notes.filter(n => n.id !== undefined && noteIds.has(n.id));
  }

  if (dateFilter && dateFilter !== 'all') {
    const cur = new Date();
    let cutoff: Date;
    if (dateFilter === 'today') {
      cutoff = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate());
    } else if (dateFilter === '7d') {
      cutoff = new Date(cur.getTime() - 7 * 86400000);
    } else if (dateFilter === '30d') {
      cutoff = new Date(cur.getTime() - 30 * 86400000);
    } else {
      cutoff = new Date(cur.getFullYear(), 0, 1);
    }
    const cutoffStr = cutoff.toISOString();
    notes = notes.filter(n => normalizeUtcTimestamp(n.updated_at) >= cutoffStr);
  }

  return notes.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

export async function saveNote(note: Note): Promise<number> {
  return db.transaction('rw', allSyncTables(), async () => {
    const ts = now();
    if (note.id) {
      const existing = await db.notes.get(note.id);
      const sync_id = existing?.sync_id || note.sync_id || generateUuidV4();
      const merged: Note = {
        ...(existing ?? {}),
        ...note,
        sync_id,
        created_at: normalizeUtcTimestamp(existing?.created_at ?? note.created_at ?? ts),
        updated_at: ts,
        metadata: note.metadata ?? existing?.metadata ?? {},
      };
      const { revision_id, parent_revision_ids } = await recordMutationInTx({
        operation_type: merged.is_deleted ? 'entity.trash' : 'entity.upsert',
        entity_type: 'note',
        entity_id: sync_id,
        created_at: ts,
        payload_data: await noteToSyncData(merged),
      });
      await db.notes.update(note.id, {
        ...merged,
        revision_id,
        parent_revision_ids,
      });
      return note.id;
    }

    const sync_id = note.sync_id || generateUuidV4();
    const created_at = normalizeUtcTimestamp(note.created_at || ts);
    const updated_at = ts;
    const toInsert: Note = {
      ...note,
      sync_id,
      created_at,
      updated_at,
      is_deleted: Boolean(note.is_deleted),
      metadata: note.metadata ?? {},
    };
    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: toInsert.is_deleted ? 'entity.trash' : 'entity.upsert',
      entity_type: 'note',
      entity_id: sync_id,
      created_at: updated_at,
      payload_data: await noteToSyncData(toInsert),
    });
    toInsert.revision_id = revision_id;
    toInsert.parent_revision_ids = parent_revision_ids;
    return db.notes.add(toInsert);
  });
}

export async function deleteNote(id: number): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const existing = await db.notes.get(id);
    if (!existing) return;
    const ts = now();
    const sync_id = existing.sync_id || generateUuidV4();
    const updated: Note = {
      ...existing,
      sync_id,
      is_deleted: true,
      deleted_at: ts,
      updated_at: ts,
    };
    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: 'entity.trash',
      entity_type: 'note',
      entity_id: sync_id,
      created_at: ts,
      payload_data: await noteToSyncData(updated),
    });
    await db.notes.update(id, {
      sync_id,
      is_deleted: true,
      deleted_at: ts,
      updated_at: ts,
      revision_id,
      parent_revision_ids,
    });
  });
}

export async function restoreNote(id: number): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const existing = await db.notes.get(id);
    if (!existing) return;
    const ts = now();
    const sync_id = existing.sync_id || generateUuidV4();
    const updated: Note = {
      ...existing,
      sync_id,
      is_deleted: false,
      deleted_at: undefined,
      updated_at: ts,
    };
    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: 'entity.restore',
      entity_type: 'note',
      entity_id: sync_id,
      created_at: ts,
      payload_data: await noteToSyncData(updated),
    });
    await db.notes.update(id, {
      sync_id,
      is_deleted: false,
      deleted_at: undefined,
      updated_at: ts,
      revision_id,
      parent_revision_ids,
    });
  });
}

export async function permanentlyDeleteNote(id: number): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const note = await db.notes.get(id);
    const ts = now();

    const atts = await db.attachments.where('note_id').equals(id).toArray();
    for (const att of atts) {
      if (att.sync_id) {
        const rev = await recordMutationInTx({
          operation_type: 'media.delete',
          entity_type: 'attachment',
          entity_id: att.sync_id,
          created_at: ts,
          media_id: att.media_id,
          payload_data: {
            media_id: att.media_id,
            deleted_at: ts,
          },
        });
        await db.sync_tombstones.put({
          entity_key: `attachment:${att.sync_id}`,
          entity_type: 'attachment',
          entity_id: att.sync_id,
          revision_id: rev.revision_id,
          deleted_at: ts,
        });
      }
      await db.attachments.delete(att.id!);
    }

    const links = await db.note_tags.where('note_id').equals(id).toArray();
    for (const l of links) {
      if (l.sync_id) {
        const rev = await recordMutationInTx({
          operation_type: 'entity.delete',
          entity_type: 'relation',
          entity_id: l.sync_id,
          created_at: ts,
          payload_data: { deleted_at: ts },
        });
        await db.sync_tombstones.put({
          entity_key: `relation:${l.sync_id}`,
          entity_type: 'relation',
          entity_id: l.sync_id,
          revision_id: rev.revision_id,
          deleted_at: ts,
        });
      }
      await db.note_tags.delete([l.note_id, l.tag_id]);
    }

    if (note?.sync_id) {
      const rev = await recordMutationInTx({
        operation_type: 'entity.delete',
        entity_type: 'note',
        entity_id: note.sync_id,
        created_at: ts,
        payload_data: { deleted_at: ts },
      });
      await db.sync_tombstones.put({
        entity_key: `note:${note.sync_id}`,
        entity_type: 'note',
        entity_id: note.sync_id,
        revision_id: rev.revision_id,
        deleted_at: ts,
      });
    }
    await db.notes.delete(id);
  });
}

export async function redateNote(id: number, newDateYmd: string): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const existing = await db.notes.get(id);
    if (!existing) return;
    const sync_id = existing.sync_id || generateUuidV4();
    const existingIso = normalizeUtcTimestamp(existing.updated_at);
    const timePart = existingIso.slice(11) || '12:00:00.000Z';
    const newUpdatedAt = normalizeUtcTimestamp(`${newDateYmd}T${timePart}`);
    const updated: Note = {
      ...existing,
      sync_id,
      updated_at: newUpdatedAt,
    };
    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: existing.is_deleted ? 'entity.trash' : 'entity.upsert',
      entity_type: 'note',
      entity_id: sync_id,
      created_at: now(),
      payload_data: await noteToSyncData(updated),
    });
    await db.notes.update(id, {
      sync_id,
      updated_at: newUpdatedAt,
      revision_id,
      parent_revision_ids,
    });
  });
}

// ── Tags ────────────────────────────────────────────────────────────────────

export async function getNoteTags(noteId: number): Promise<Tag[]> {
  const links = await db.note_tags.where('note_id').equals(noteId).toArray();
  if (!links.length) return [];
  return db.tags.where('id').anyOf(links.map(l => l.tag_id)).toArray();
}

async function addTagToNoteInTx(noteId: number, tagName: string): Promise<void> {
  const name = normalizeTagName(tagName);
  if (!name) return;
  const note = await db.notes.get(noteId);
  if (!note) return;
  const noteSyncId = note.sync_id || generateUuidV4();
  if (!note.sync_id) await db.notes.update(noteId, { sync_id: noteSyncId });

  const ts = now();
  let tag = await db.tags.where('name').equalsIgnoreCase(name).first();
  if (!tag) {
    const colors = ['#6366F1', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6'];
    const color = colors[Math.floor(Math.random() * colors.length)];
    const tagSyncId = generateUuidV4();
    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: 'entity.upsert',
      entity_type: 'tag',
      entity_id: tagSyncId,
      created_at: ts,
      payload_data: {
        name,
        color,
        created_at: ts,
        updated_at: ts,
      },
    });
    const id = await db.tags.add({
      sync_id: tagSyncId,
      revision_id,
      parent_revision_ids,
      name,
      color,
      created_at: ts,
      updated_at: ts,
    });
    tag = { id, sync_id: tagSyncId, revision_id, parent_revision_ids, name, color, created_at: ts, updated_at: ts };
  } else if (!tag.sync_id) {
    const tagSyncId = generateUuidV4();
    await db.tags.update(tag.id!, { sync_id: tagSyncId });
    tag = { ...tag, sync_id: tagSyncId };
  }

  const exists = await db.note_tags.get([noteId, tag.id!]);
  if (!exists) {
    const relSyncId = generateUuidV4();
    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: 'entity.upsert',
      entity_type: 'relation',
      entity_id: relSyncId,
      created_at: ts,
      payload_data: {
        relation_type: 'note_tag',
        note_id: noteSyncId,
        tag_id: tag.sync_id!,
        created_at: ts,
      },
    });
    await db.note_tags.add({
      note_id: noteId,
      tag_id: tag.id!,
      sync_id: relSyncId,
      revision_id,
      parent_revision_ids,
      created_at: ts,
    });
  }
}

export async function addTagToNote(noteId: number, tagName: string): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    await addTagToNoteInTx(noteId, tagName);
  });
}

export async function removeTagFromNote(noteId: number, tagId: number): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const link = await db.note_tags.get([noteId, tagId]);
    if (!link) return;
    const ts = now();
    if (link.sync_id) {
      const rev = await recordMutationInTx({
        operation_type: 'entity.delete',
        entity_type: 'relation',
        entity_id: link.sync_id,
        created_at: ts,
        payload_data: { deleted_at: ts },
      });
      await db.sync_tombstones.put({
        entity_key: `relation:${link.sync_id}`,
        entity_type: 'relation',
        entity_id: link.sync_id,
        revision_id: rev.revision_id,
        deleted_at: ts,
      });
    }
    await db.note_tags.delete([noteId, tagId]);
  });
}

// ── Attachments ─────────────────────────────────────────────────────────────

export async function getNoteAttachments(noteId: number): Promise<Attachment[]> {
  return db.attachments.where('note_id').equals(noteId).sortBy('created_at');
}

export async function addAttachment(attachment: Omit<Attachment, 'id'>): Promise<number> {
  return db.transaction('rw', allSyncTables(), async () => {
    const note = await db.notes.get(attachment.note_id);
    if (!note) throw new Error(`Note ${attachment.note_id} not found for attachment`);
    const noteSyncId = note.sync_id || generateUuidV4();
    if (!note.sync_id) await db.notes.update(attachment.note_id, { sync_id: noteSyncId });

    const sync_id = attachment.sync_id || generateUuidV4();
    const media_id = attachment.media_id || generateUuidV4();
    const created_at = normalizeUtcTimestamp(attachment.created_at);
    const mime_type = attachment.mime_type || attachment.data.type || 'application/octet-stream';
    const original_size = attachment.original_size ?? attachment.data.size;
    const epochRec = await db.sync_state.get('current_key_epoch');
    const key_epoch = attachment.key_epoch ?? (Number(epochRec?.value ?? '1') || 1);

    const newAtt: Attachment = {
      ...attachment,
      sync_id,
      media_id,
      created_at,
      mime_type,
      original_size,
      key_epoch,
      media_status: attachment.media_status ?? 'pending_upload',
    };
    const newId = await db.attachments.add(newAtt);

    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: 'media.register',
      entity_type: 'attachment',
      entity_id: sync_id,
      created_at,
      media_local_attachment_id: newId,
      media_id,
      payload_data: {
        note_id: noteSyncId,
        media_id,
        media_type: newAtt.type,
        media_crypto_version: 1,
        key_epoch,
        encrypted_size: newAtt.encrypted_size ?? 0,
        encrypted_sha256: newAtt.encrypted_sha256 ?? '',
        original_size,
        mime_type,
        file_name: newAtt.filename,
        duration: newAtt.duration ?? null,
        thumbnail_data_url: newAtt.thumbnail ?? null,
        created_at,
      },
    });
    await db.attachments.update(newId, { revision_id, parent_revision_ids });
    return newId;
  });
}

export async function deleteAttachment(id: number): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const att = await db.attachments.get(id);
    if (!att) return;
    const ts = now();
    if (att.sync_id) {
      const rev = await recordMutationInTx({
        operation_type: 'media.delete',
        entity_type: 'attachment',
        entity_id: att.sync_id,
        created_at: ts,
        media_id: att.media_id,
        payload_data: {
          media_id: att.media_id,
          deleted_at: ts,
        },
      });
      await db.sync_tombstones.put({
        entity_key: `attachment:${att.sync_id}`,
        entity_type: 'attachment',
        entity_id: att.sync_id,
        revision_id: rev.revision_id,
        deleted_at: ts,
      });
    }
    await db.attachments.delete(id);
  });
}

// ── Categories ──────────────────────────────────────────────────────────────

export async function getCategories(): Promise<Category[]> {
  return db.categories.orderBy('sort_order').toArray();
}

export async function saveCategory(cat: Category): Promise<number> {
  return db.transaction('rw', allSyncTables(), async () => {
    const ts = now();
    if (cat.id) {
      const existing = await db.categories.get(cat.id);
      const sync_id = existing?.sync_id || cat.sync_id || generateUuidV4();
      const created_at = normalizeUtcTimestamp(existing?.created_at ?? cat.created_at ?? ts);
      const merged: Category = { ...(existing ?? {}), ...cat, sync_id, created_at, updated_at: ts };
      const { revision_id, parent_revision_ids } = await recordMutationInTx({
        operation_type: 'entity.upsert',
        entity_type: 'category',
        entity_id: sync_id,
        created_at: ts,
        payload_data: {
          name: merged.name,
          parent_id: await resolveCategorySyncId(merged.parent_id),
          color: merged.color,
          sort_order: merged.sort_order,
          created_at,
          updated_at: ts,
        },
      });
      await db.categories.update(cat.id, { ...merged, revision_id, parent_revision_ids });
      return cat.id;
    }

    const sync_id = cat.sync_id || generateUuidV4();
    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: 'entity.upsert',
      entity_type: 'category',
      entity_id: sync_id,
      created_at: ts,
      payload_data: {
        name: cat.name,
        parent_id: await resolveCategorySyncId(cat.parent_id),
        color: cat.color,
        sort_order: cat.sort_order,
        created_at: ts,
        updated_at: ts,
      },
    });
    return db.categories.add({
      ...cat,
      sync_id,
      revision_id,
      parent_revision_ids,
      created_at: ts,
      updated_at: ts,
    });
  });
}

export async function deleteCategory(id: number): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const cat = await db.categories.get(id);
    if (!cat) return;
    const ts = now();

    const affectedNotes = await db.notes.where('category_id').equals(id).toArray();
    for (const n of affectedNotes) {
      const updatedNote: Note = { ...n, category_id: undefined, updated_at: ts };
      const noteSyncId = n.sync_id || generateUuidV4();
      const rev = await recordMutationInTx({
        operation_type: n.is_deleted ? 'entity.trash' : 'entity.upsert',
        entity_type: 'note',
        entity_id: noteSyncId,
        created_at: ts,
        payload_data: await noteToSyncData(updatedNote),
      });
      await db.notes.update(n.id!, {
        sync_id: noteSyncId,
        category_id: undefined,
        updated_at: ts,
        revision_id: rev.revision_id,
        parent_revision_ids: rev.parent_revision_ids,
      });
    }

    const childCats = await db.categories.where('parent_id').equals(id).toArray();
    for (const c of childCats) {
      const cSyncId = c.sync_id || generateUuidV4();
      const rev = await recordMutationInTx({
        operation_type: 'entity.upsert',
        entity_type: 'category',
        entity_id: cSyncId,
        created_at: ts,
        payload_data: {
          name: c.name,
          parent_id: null,
          color: c.color,
          sort_order: c.sort_order,
          created_at: c.created_at || ts,
          updated_at: ts,
        },
      });
      await db.categories.update(c.id!, {
        sync_id: cSyncId,
        parent_id: undefined,
        updated_at: ts,
        revision_id: rev.revision_id,
        parent_revision_ids: rev.parent_revision_ids,
      });
    }

    if (cat.sync_id) {
      const rev = await recordMutationInTx({
        operation_type: 'entity.delete',
        entity_type: 'category',
        entity_id: cat.sync_id,
        created_at: ts,
        payload_data: { deleted_at: ts },
      });
      await db.sync_tombstones.put({
        entity_key: `category:${cat.sync_id}`,
        entity_type: 'category',
        entity_id: cat.sync_id,
        revision_id: rev.revision_id,
        deleted_at: ts,
      });
    }
    await db.categories.delete(id);
  });
}

// ── Folders ─────────────────────────────────────────────────────────────────

export async function getFolders(): Promise<Folder[]> {
  return db.folders.toArray();
}

export async function saveFolder(folder: Folder): Promise<number> {
  return db.transaction('rw', allSyncTables(), async () => {
    const ts = now();
    if (folder.id) {
      const existing = await db.folders.get(folder.id);
      const sync_id = existing?.sync_id || folder.sync_id || generateUuidV4();
      const created_at = normalizeUtcTimestamp(existing?.created_at ?? folder.created_at ?? ts);
      const merged: Folder = { ...(existing ?? {}), ...folder, sync_id, created_at, updated_at: ts };
      const { revision_id, parent_revision_ids } = await recordMutationInTx({
        operation_type: 'entity.upsert',
        entity_type: 'folder',
        entity_id: sync_id,
        created_at: ts,
        payload_data: {
          name: merged.name,
          parent_id: await resolveFolderSyncId(merged.parent_id),
          created_at,
          updated_at: ts,
        },
      });
      await db.folders.update(folder.id, { ...merged, revision_id, parent_revision_ids });
      return folder.id;
    }

    const sync_id = folder.sync_id || generateUuidV4();
    const { revision_id, parent_revision_ids } = await recordMutationInTx({
      operation_type: 'entity.upsert',
      entity_type: 'folder',
      entity_id: sync_id,
      created_at: ts,
      payload_data: {
        name: folder.name,
        parent_id: await resolveFolderSyncId(folder.parent_id),
        created_at: ts,
        updated_at: ts,
      },
    });
    return db.folders.add({
      ...folder,
      sync_id,
      revision_id,
      parent_revision_ids,
      created_at: ts,
      updated_at: ts,
    });
  });
}

export async function deleteFolder(id: number): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const folder = await db.folders.get(id);
    if (!folder) return;
    const ts = now();

    const affectedNotes = await db.notes.where('folder_id').equals(id).toArray();
    for (const n of affectedNotes) {
      const updatedNote: Note = { ...n, folder_id: undefined, updated_at: ts };
      const noteSyncId = n.sync_id || generateUuidV4();
      const rev = await recordMutationInTx({
        operation_type: n.is_deleted ? 'entity.trash' : 'entity.upsert',
        entity_type: 'note',
        entity_id: noteSyncId,
        created_at: ts,
        payload_data: await noteToSyncData(updatedNote),
      });
      await db.notes.update(n.id!, {
        sync_id: noteSyncId,
        folder_id: undefined,
        updated_at: ts,
        revision_id: rev.revision_id,
        parent_revision_ids: rev.parent_revision_ids,
      });
    }

    const childFolders = await db.folders.where('parent_id').equals(id).toArray();
    for (const f of childFolders) {
      const fSyncId = f.sync_id || generateUuidV4();
      const rev = await recordMutationInTx({
        operation_type: 'entity.upsert',
        entity_type: 'folder',
        entity_id: fSyncId,
        created_at: ts,
        payload_data: {
          name: f.name,
          parent_id: null,
          created_at: f.created_at || ts,
          updated_at: ts,
        },
      });
      await db.folders.update(f.id!, {
        sync_id: fSyncId,
        parent_id: undefined,
        updated_at: ts,
        revision_id: rev.revision_id,
        parent_revision_ids: rev.parent_revision_ids,
      });
    }

    if (folder.sync_id) {
      const rev = await recordMutationInTx({
        operation_type: 'entity.delete',
        entity_type: 'folder',
        entity_id: folder.sync_id,
        created_at: ts,
        payload_data: { deleted_at: ts },
      });
      await db.sync_tombstones.put({
        entity_key: `folder:${folder.sync_id}`,
        entity_type: 'folder',
        entity_id: folder.sync_id,
        revision_id: rev.revision_id,
        deleted_at: ts,
      });
    }
    await db.folders.delete(id);
  });
}

// ── All Tags & Wikilinks ────────────────────────────────────────────────────

export async function getAllTags(): Promise<Tag[]> {
  return db.tags.toArray();
}

export async function findNoteByTitle(title: string): Promise<Note | undefined> {
  return db.notes.filter(n => !n.is_deleted && n.title.toLowerCase() === title.toLowerCase()).first();
}

export async function findNoteBySyncId(syncId: string): Promise<Note | undefined> {
  return db.notes.where('sync_id').equals(syncId).filter(n => !n.is_deleted).first();
}

// Returns all non-deleted notes (excluding self) containing a wikilink to noteId / noteSyncId / noteTitle.
// Supports [[Title|@uuid]] (canonical V1), [[Title|id]] (legacy numeric), and [[Title]] (title-based).
export async function getBacklinks(
  noteId: number,
  noteTitle: string,
  noteSyncId?: string,
): Promise<Note[]> {
  const numericFragment = `|${noteId}]]`;
  const uuidFragment = noteSyncId ? `|@${noteSyncId}]]` : null;
  const titleLower = noteTitle.toLowerCase();
  const notes = await db.notes.filter(n => !n.is_deleted && n.id !== noteId).toArray();
  return notes.filter(n => {
    if (!n.content.includes('[[')) return false;
    if (uuidFragment && n.content.includes(uuidFragment)) return true;
    if (n.content.includes(numericFragment)) return true;
    return Boolean(titleLower) && n.content.toLowerCase().includes(`[[${titleLower}]]`);
  });
}

// Update wikilinks across all notes when a note is renamed.
// Upgrades [[OldTitle|id]] and [[OldTitle]] -> [[NewTitle|@sync_id]] (or [[NewTitle|id]] if no sync_id).
export async function updateWikilinksOnRename(
  oldTitle: string,
  newTitle: string,
  noteId: number,
): Promise<number> {
  if (!oldTitle || oldTitle === newTitle) return 0;
  return db.transaction('rw', allSyncTables(), async () => {
    const targetNote = await db.notes.get(noteId);
    const targetSyncId = targetNote?.sync_id;
    const escaped = oldTitle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const withUuid = targetSyncId
      ? new RegExp(`\\[\\[${escaped}\\|@${targetSyncId}\\]\\]`, 'gi')
      : null;
    const withId = new RegExp(`\\[\\[${escaped}\\|${noteId}\\]\\]`, 'gi');
    const legacy = new RegExp(`\\[\\[${escaped}\\]\\]`, 'gi');
    const canonicalTarget = targetSyncId ? `[[${newTitle}|@${targetSyncId}]]` : `[[${newTitle}|${noteId}]]`;

    const notes = await db.notes.filter(n => !n.is_deleted).toArray();
    let count = 0;
    const ts = now();
    for (const note of notes) {
      if (!note.content.includes('[[')) continue;
      let updated = note.content;
      if (withUuid) updated = updated.replace(withUuid, canonicalTarget);
      updated = updated.replace(withId, canonicalTarget).replace(legacy, canonicalTarget);
      if (updated !== note.content) {
        const noteSyncId = note.sync_id || generateUuidV4();
        const merged: Note = { ...note, sync_id: noteSyncId, content: updated, updated_at: ts };
        const rev = await recordMutationInTx({
          operation_type: 'entity.upsert',
          entity_type: 'note',
          entity_id: noteSyncId,
          created_at: ts,
          payload_data: await noteToSyncData(merged),
        });
        await db.notes.update(note.id!, {
          sync_id: noteSyncId,
          content: updated,
          updated_at: ts,
          revision_id: rev.revision_id,
          parent_revision_ids: rev.parent_revision_ids,
        });
        count++;
      }
    }
    return count;
  });
}

// ── Bulk Operations (transactional + sync outbox) ───────────────────────────

export async function moveNotesBulk(
  ids: number[],
  opts: { folderId?: number | null; categoryId?: number | null },
): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const ts = now();
    for (const id of ids) {
      const existing = await db.notes.get(id);
      if (!existing) continue;
      const folder_id = 'folderId' in opts ? (opts.folderId ?? undefined) : existing.folder_id;
      const category_id = 'categoryId' in opts ? (opts.categoryId ?? undefined) : existing.category_id;
      const sync_id = existing.sync_id || generateUuidV4();
      const updated: Note = {
        ...existing,
        sync_id,
        folder_id,
        category_id,
        updated_at: ts,
      };
      const rev = await recordMutationInTx({
        operation_type: existing.is_deleted ? 'entity.trash' : 'entity.upsert',
        entity_type: 'note',
        entity_id: sync_id,
        created_at: ts,
        payload_data: await noteToSyncData(updated),
      });
      await db.notes.update(id, {
        sync_id,
        folder_id,
        category_id,
        updated_at: ts,
        revision_id: rev.revision_id,
        parent_revision_ids: rev.parent_revision_ids,
      });
    }
  });
}

export async function tagNotesBulk(ids: number[], tagName: string): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    for (const id of ids) {
      await addTagToNoteInTx(id, tagName);
    }
  });
}

export async function deleteNotesBulk(ids: number[]): Promise<void> {
  await db.transaction('rw', allSyncTables(), async () => {
    const ts = now();
    for (const id of ids) {
      const existing = await db.notes.get(id);
      if (!existing) continue;
      const sync_id = existing.sync_id || generateUuidV4();
      const updated: Note = { ...existing, sync_id, is_deleted: true, deleted_at: ts, updated_at: ts };
      const rev = await recordMutationInTx({
        operation_type: 'entity.trash',
        entity_type: 'note',
        entity_id: sync_id,
        created_at: ts,
        payload_data: await noteToSyncData(updated),
      });
      await db.notes.update(id, {
        sync_id,
        is_deleted: true,
        deleted_at: ts,
        updated_at: ts,
        revision_id: rev.revision_id,
        parent_revision_ids: rev.parent_revision_ids,
      });
    }
  });
}

export async function exportSelectedNotes(ids: number[]): Promise<ExportData> {
  const notes = await db.notes.where('id').anyOf(ids).toArray();
  const exportedNotes: ExportNote[] = [];
  for (const note of notes) {
    if (!note.id) continue;
    const tags = await getNoteTags(note.id);
    const attachments = await db.attachments.where('note_id').equals(note.id).toArray();
    exportedNotes.push({
      id: note.id,
      title: note.title,
      content: note.content,
      created_at: note.created_at,
      updated_at: note.updated_at,
      is_deleted: note.is_deleted,
      deleted_at: note.deleted_at,
      tags: tags.map(t => ({ name: t.name, color: t.color })),
      attachments: attachments.map(a => ({
        type: a.type,
        filename: a.filename,
        filepath: a.filename,
        duration: a.duration,
      })),
    });
  }
  return { version: '1.0', app: 'Noteor', exported_at: new Date().toISOString(), notes: exportedNotes };
}

// ── Export / Import ─────────────────────────────────────────────────────────

export interface ExportAttachment {
  type: 'image' | 'audio' | 'video';
  filename: string;
  filepath: string;
  duration?: number;
}

export interface ExportNote {
  id: number;
  title: string;
  content: string;
  created_at: string;
  updated_at: string;
  is_deleted: boolean;
  deleted_at?: string;
  tags: { name: string; color: string }[];
  attachments: ExportAttachment[];
}

export interface ExportData {
  version: '1.0';
  app: 'Noteor';
  exported_at: string;
  notes: ExportNote[];
}

export async function exportAllNotes(): Promise<ExportData> {
  const notes = await db.notes.toArray();
  const exportedNotes: ExportNote[] = [];

  for (const note of notes) {
    if (!note.id) continue;
    const tags = await getNoteTags(note.id);
    const attachments = await db.attachments.where('note_id').equals(note.id).toArray();

    exportedNotes.push({
      id: note.id,
      title: note.title,
      content: note.content,
      created_at: note.created_at,
      updated_at: note.updated_at,
      is_deleted: note.is_deleted,
      deleted_at: note.deleted_at,
      tags: tags.map(t => ({ name: t.name, color: t.color })),
      attachments: attachments.map(a => ({
        type: a.type,
        filename: a.filename,
        filepath: a.filename,
        duration: a.duration,
      })),
    });
  }

  return {
    version: '1.0',
    app: 'Noteor',
    exported_at: new Date().toISOString(),
    notes: exportedNotes,
  };
}

export async function importNotes(
  data: ExportData,
): Promise<{ imported: number; skipped: number; errors: number }> {
  return db.transaction('rw', allSyncTables(), async () => {
    let imported = 0;
    let skipped = 0;
    let errors = 0;

    const existingIds = new Set((await db.notes.toArray()).map(n => n.id!));

    for (const noteData of data.notes) {
      try {
        if (existingIds.has(noteData.id)) {
          skipped++;
          continue;
        }
        const sync_id = generateUuidV4();
        const created_at = normalizeUtcTimestamp(noteData.created_at);
        const updated_at = normalizeUtcTimestamp(noteData.updated_at);
        const deleted_at = noteData.deleted_at ? normalizeUtcTimestamp(noteData.deleted_at) : undefined;
        const noteObj: Note = {
          sync_id,
          title: noteData.title,
          content: noteData.content,
          created_at,
          updated_at,
          is_deleted: noteData.is_deleted ?? false,
          deleted_at,
          metadata: {},
        };
        const rev = await recordMutationInTx({
          operation_type: noteObj.is_deleted ? 'entity.trash' : 'entity.upsert',
          entity_type: 'note',
          entity_id: sync_id,
          created_at: updated_at,
          payload_data: await noteToSyncData(noteObj),
        });
        noteObj.revision_id = rev.revision_id;
        noteObj.parent_revision_ids = rev.parent_revision_ids;
        const newId = await db.notes.add(noteObj);

        for (const tagData of noteData.tags ?? []) {
          await addTagToNoteInTx(newId, tagData.name);
        }

        imported++;
      } catch {
        errors++;
      }
    }

    return { imported, skipped, errors };
  });
}
