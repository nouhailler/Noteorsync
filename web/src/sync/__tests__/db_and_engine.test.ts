import 'fake-indexeddb/auto';
import { describe, it, expect, beforeEach } from 'vitest';
import Dexie from 'dexie';
import {
  db,
  saveNote,
  deleteNote,
  restoreNote,
  permanentlyDeleteNote,
  addTagToNote,
  removeTagFromNote,
  saveCategory,
  deleteCategory,
  getBacklinks,
  updateWikilinksOnRename,
  enqueueExistingVaultSnapshot,
} from '../../db';
import { PwaSyncEngine, SyncHttpClient, buildKeyringFromVsk } from '../engine';
import { generateUuidV4 } from '../crypto';

describe('Phase 4 & 5: Dexie v2 Migration, Atomic Outbox, DAG Conflicts & Dependencies', () => {
  beforeEach(async () => {
    await db.delete();
    await db.open();
  });

  it('upgrades a legacy v1 Dexie database (UUID sync_ids, UTC dates, @uuid wikilinks, tag dedup)', async () => {
    await db.close();
    const dbName = db.name;
    // Delete and seed pure v1 schema via raw Dexie instance
    await Dexie.delete(dbName);
    const v1 = new Dexie(dbName);
    v1.version(1).stores({
      notes: '++id, title, category_id, folder_id, updated_at, is_deleted',
      categories: '++id, name, parent_id, sort_order',
      folders: '++id, name, parent_id',
      tags: '++id, &name',
      note_tags: '[note_id+tag_id], note_id, tag_id',
      attachments: '++id, note_id, type, created_at',
    });
    await v1.open();
    const targetId = await v1.table('notes').add({
      title: 'Cible',
      content: 'Note cible',
      created_at: '2026-11-18 10:00:00',
      updated_at: '2026-11-18 10:05:00',
      is_deleted: false,
    });
    await v1.table('notes').add({
      title: 'Source',
      content: `Voir [[Cible|${targetId}]]`,
      created_at: '2026-11-18 10:10:00',
      updated_at: '2026-11-18 10:15:00',
      is_deleted: false,
    });
    const t1 = await v1.table('tags').add({ name: 'Urgent', color: '#EF4444' });
    const t2 = await v1.table('tags').add({ name: 'urgent ', color: '#10B981' });
    await v1.table('note_tags').add({ note_id: targetId, tag_id: t1 });
    await v1.table('note_tags').add({ note_id: targetId, tag_id: t2 });
    v1.close();

    // Re-open NoteorDB (triggers v1 -> v2 upgrade)
    await db.open();
    const allNotes = await db.notes.toArray();
    const cible = allNotes.find(n => n.title === 'Cible')!;
    const source = allNotes.find(n => n.title === 'Source')!;
    expect(cible.sync_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(cible.created_at).toBe('2026-11-18T10:00:00.000Z');
    expect(source.content).toBe(`Voir [[Cible|@${cible.sync_id}]]`);

    // Tags deduplicated case-insensitively and note_tags remapped
    const allTags = await db.tags.toArray();
    expect(allTags.length).toBe(1);
    expect(allTags[0].name).toBe('urgent');
    const links = await db.note_tags.where('note_id').equals(cible.id!).toArray();
    expect(links.length).toBe(1);
    expect(links[0].sync_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('atomically queues outbox operations with monotonic device_seq and handles cascading deletes', async () => {
    const vaultId = generateUuidV4();
    const deviceId = generateUuidV4();
    await db.sync_state.put({ key: 'vault_id', value: vaultId });
    await db.sync_state.put({ key: 'device_id', value: deviceId });

    const catId = await saveCategory({ name: 'Work', color: '#4F46E5', sort_order: 0 });
    const note1 = await saveNote({
      title: 'Note A',
      content: 'Bonjour',
      category_id: catId,
      created_at: '',
      updated_at: '',
      is_deleted: false,
    });
    const note2 = await saveNote({
      title: 'Note B',
      content: 'Lien [[Note A]]',
      created_at: '',
      updated_at: '',
      is_deleted: false,
    });

    const renamed = await updateWikilinksOnRename('Note A', 'Note A2', note1);
    expect(renamed).toBe(1);
    const updatedB = await db.notes.get(note2);
    const noteAObj = await db.notes.get(note1);
    expect(updatedB!.content).toBe(`Lien [[Note A2|@${noteAObj!.sync_id}]]`);

    const backlinks = await getBacklinks(note1, 'Note A2', noteAObj!.sync_id);
    expect(backlinks.map(b => b.id)).toEqual([note2]);

    await deleteNote(note1);
    expect((await db.notes.get(note1))!.is_deleted).toBe(true);
    await restoreNote(note1);
    expect((await db.notes.get(note1))!.is_deleted).toBe(false);

    await addTagToNote(note1, '  Projet  ');
    const tags = await db.tags.toArray();
    expect(tags.length).toBe(1);
    await removeTagFromNote(note1, tags[0].id!);
    await deleteCategory(catId);
    expect((await db.notes.get(note1))!.category_id).toBeUndefined();

    await permanentlyDeleteNote(note2);
    const ops = await db.sync_operations.orderBy('id').toArray();
    expect(ops.length).toBeGreaterThanOrEqual(8);
    expect(ops.map(o => o.device_seq)).toEqual(
      ops.map((_, idx) => String(idx + 1)),
    );
    expect(ops.some(o => o.operation_type === 'entity.delete' && o.entity_type === 'category')).toBe(true);
    expect(ops.some(o => o.operation_type === 'entity.delete' && o.entity_type === 'note')).toBe(true);
  });

  it('defers out-of-order dependent operations and creates explicit conflicts on concurrent DAG edits', async () => {
    const vaultId = generateUuidV4();
    const deviceA = generateUuidV4();
    const deviceB = generateUuidV4();
    await db.sync_state.put({ key: 'vault_id', value: vaultId });
    await db.sync_state.put({ key: 'device_id', value: deviceA });

    const keyring = buildKeyringFromVsk(vaultId, deviceA, new Uint8Array(32).fill(1));
    const client = new SyncHttpClient({ baseUrl: 'http://localhost', accessToken: 'x' });
    const engine = new PwaSyncEngine(client, keyring);

    // Receive relation BEFORE note or tag arrive locally
    const remoteNoteUuid = generateUuidV4();
    const remoteNoteRev1 = generateUuidV4();
    const remoteTagUuid = generateUuidV4();
    const remoteTagRev1 = generateUuidV4();
    const relUuid = generateUuidV4();

    const relRes = await engine.applyDecryptedOperation(generateUuidV4(), '1', deviceB, {
      payload_version: 1,
      operation_type: 'entity.upsert',
      entity_type: 'relation',
      entity_id: relUuid,
      revision_id: generateUuidV4(),
      parent_revision_ids: [],
      created_at: '2026-11-19T10:00:00.000Z',
      data: { relation_type: 'note_tag', note_id: remoteNoteUuid, tag_id: remoteTagUuid, created_at: '2026-11-19T10:00:00.000Z' },
    });
    expect(relRes).toBe('deferred');
    expect(await db.sync_pending_ops.count()).toBe(1);

    // Arrive note & tag
    await engine.applyDecryptedOperation(generateUuidV4(), '2', deviceB, {
      payload_version: 1,
      operation_type: 'entity.upsert',
      entity_type: 'note',
      entity_id: remoteNoteUuid,
      revision_id: remoteNoteRev1,
      parent_revision_ids: [],
      created_at: '2026-11-19T10:01:00.000Z',
      data: { title: 'Remote', content: 'Base', category_id: null, folder_id: null, is_deleted: false, deleted_at: null, created_at: '2026-11-19T10:01:00.000Z', updated_at: '2026-11-19T10:01:00.000Z', metadata: {} },
    });
    await engine.applyDecryptedOperation(generateUuidV4(), '3', deviceB, {
      payload_version: 1,
      operation_type: 'entity.upsert',
      entity_type: 'tag',
      entity_id: remoteTagUuid,
      revision_id: remoteTagRev1,
      parent_revision_ids: [],
      created_at: '2026-11-19T10:01:05.000Z',
      data: { name: 'Alpha', color: '#4F46E5', created_at: '2026-11-19T10:01:05.000Z', updated_at: '2026-11-19T10:01:05.000Z' },
    });

    // Manually drain pending ops via internal test helper by re-evaluating pending list
    const pending = await db.sync_pending_ops.toArray();
    for (const p of pending) {
      const outcome = await engine.applyDecryptedOperation(
        p.operation_id,
        p.server_seq,
        p.device_id,
        p.decrypted_payload as any,
      );
      expect(outcome).toBe('applied');
      await db.sync_pending_ops.delete(p.id!);
    }
    const localRemoteNote = await db.notes.where('sync_id').equals(remoteNoteUuid).first();
    expect(localRemoteNote).toBeDefined();
    expect((await db.note_tags.where('note_id').equals(localRemoteNote!.id!).toArray()).length).toBe(1);

    // Concurrent edit locally vs remotely on same parent revision remoteNoteRev1
    await saveNote({ ...localRemoteNote!, content: 'Edition locale PWA' });
    const remoteConflictRes = await engine.applyDecryptedOperation(generateUuidV4(), '4', deviceB, {
      payload_version: 1,
      operation_type: 'entity.upsert',
      entity_type: 'note',
      entity_id: remoteNoteUuid,
      revision_id: generateUuidV4(),
      parent_revision_ids: [remoteNoteRev1],
      created_at: '2026-11-19T10:05:00.000Z',
      data: { title: 'Remote', content: 'Edition concurrente Desktop', category_id: null, folder_id: null, is_deleted: false, deleted_at: null, created_at: '2026-11-19T10:01:00.000Z', updated_at: '2026-11-19T10:05:00.000Z', metadata: {} },
    });
    expect(remoteConflictRes).toBe('conflict');

    // Local note must NOT be overwritten
    expect((await db.notes.get(localRemoteNote!.id!))!.content).toBe('Edition locale PWA');
    const conflicts = await db.sync_conflicts.where('status').equals('open').toArray();
    expect(conflicts.length).toBe(1);

    // Resolve via keep_both -> creates duplicate note + merge revision
    await engine.resolveConflict(conflicts[0].conflict_id, 'keep_both');
    expect((await db.sync_conflicts.where('status').equals('open').toArray()).length).toBe(0);
    expect(await db.notes.count()).toBe(2);

    // Enqueue snapshot handles idempotence cleanly
    const extraQueued = await enqueueExistingVaultSnapshot();
    expect(extraQueued).toBe(0);
  });
});
