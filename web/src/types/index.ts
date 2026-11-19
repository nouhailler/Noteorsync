export interface Note {
  id?: number;
  sync_id?: string;
  revision_id?: string;
  parent_revision_ids?: string[];
  title: string;
  content: string;
  category_id?: number;
  folder_id?: number;
  created_at: string;
  updated_at: string;
  is_deleted: boolean;
  deleted_at?: string;
  metadata?: Record<string, unknown>;
}

export interface Category {
  id?: number;
  sync_id?: string;
  revision_id?: string;
  parent_revision_ids?: string[];
  name: string;
  parent_id?: number;
  color: string;
  sort_order: number;
  created_at?: string;
  updated_at?: string;
}

export interface Folder {
  id?: number;
  sync_id?: string;
  revision_id?: string;
  parent_revision_ids?: string[];
  name: string;
  parent_id?: number;
  created_at: string;
  updated_at?: string;
}

export interface Tag {
  id?: number;
  sync_id?: string;
  revision_id?: string;
  parent_revision_ids?: string[];
  name: string;
  color: string;
  created_at?: string;
  updated_at?: string;
}

export interface NoteTag {
  note_id: number;
  tag_id: number;
  sync_id?: string;
  revision_id?: string;
  parent_revision_ids?: string[];
  created_at?: string;
}

export type MediaSyncStatus =
  | 'local_only'
  | 'pending_upload'
  | 'uploaded'
  | 'pending_download'
  | 'available'
  | 'error';

export interface Attachment {
  id?: number;
  sync_id?: string;
  media_id?: string;
  revision_id?: string;
  parent_revision_ids?: string[];
  note_id: number;
  type: 'image' | 'audio' | 'video';
  filename: string;
  data: Blob;
  thumbnail?: string;
  duration?: number;
  mime_type?: string;
  original_size?: number;
  encrypted_size?: number;
  encrypted_sha256?: string;
  key_epoch?: number;
  media_status?: MediaSyncStatus;
  created_at: string;
}

export type FilterType = 'all' | 'audio' | 'image' | 'video';
export type DateFilter = 'all' | 'today' | '7d' | '30d' | 'year';
export type View = 'list' | 'editor' | 'sidebar';

// ── Sync Persistence Types ──────────────────────────────────────────────────

export type SyncEntityType = 'note' | 'folder' | 'category' | 'tag' | 'relation' | 'attachment';

export type SyncOperationType =
  | 'entity.upsert'
  | 'entity.trash'
  | 'entity.restore'
  | 'entity.delete'
  | 'media.register'
  | 'media.delete';

export interface SyncOutboxOperation {
  id?: number;
  operation_id: string;
  device_seq: string;
  key_epoch?: number;
  operation_type: SyncOperationType;
  entity_type: SyncEntityType;
  entity_id: string;
  revision_id: string;
  parent_revision_ids: string[];
  created_at: string;
  payload_data: Record<string, unknown>;
  status: 'pending' | 'acked' | 'failed';
  media_local_attachment_id?: number;
  media_id?: string;
  server_seq?: string;
  error_message?: string;
}

export interface SyncStateRecord {
  key: string;
  value: string;
}

export interface SyncRevisionRecord {
  id?: number;
  entity_type: SyncEntityType;
  entity_id: string;
  revision_id: string;
  parent_revision_ids: string[];
  is_head: number; // 1 = active head, 0 = historical
  operation_type: SyncOperationType;
  created_at: string;
  device_id: string;
}

export interface SyncConflictRecord {
  id?: number;
  conflict_id: string;
  entity_type: SyncEntityType;
  entity_id: string;
  local_revision_id: string;
  remote_revision_id: string;
  local_snapshot: Record<string, unknown>;
  remote_snapshot: Record<string, unknown>;
  remote_device_id: string;
  detected_at: string;
  status: 'open' | 'resolved';
  resolution?: 'keep_local' | 'keep_remote' | 'keep_both' | 'manual_merge';
  resolved_at?: string;
}

export interface SyncPendingOpRecord {
  id?: number;
  operation_id: string;
  server_seq: string;
  device_id: string;
  entity_type: SyncEntityType;
  entity_id: string;
  missing_deps: string[];
  decrypted_payload: Record<string, unknown>;
  received_at: string;
}

export interface SyncTombstoneRecord {
  entity_key: string; // `${entity_type}:${entity_id}`
  entity_type: SyncEntityType;
  entity_id: string;
  revision_id: string;
  deleted_at: string;
}
