# NOTEOR_SYNC_PROTOCOL.md (v1.1 — Contrat révisé)

**Spécification du protocole de synchronisation Noteor**  
**Version :** 1.1 — 19 novembre 2026 (résout les contradictions C1–C11 relevées dans `NOTEOR_SYNC_AUDIT_REPORT.md`)  
**Périmètre :** Noteor Desktop (Python / SQLite), Noteor PWA (React / TypeScript / Dexie / IndexedDB), Noteor Sync Server (FastAPI / SQLite WAL).  
**Documents associés :** `NOTEOR_SYNC_ARCHITECTURE.md`, `NOTEOR_SYNC_DATA_MODEL.md`, `NOTEOR_SYNC_CRYPTO.md`, `NOTEOR_SYNC_AUDIT_REPORT.md`, `NOTEOR_SYNC_CONFORMANCE_REPORT.md`.

---

## 1. Identifiants, séquences, horodatages et encodages

- **Identifiants (`vault_id`, `device_id`, `operation_id`, `entity_id`, `revision_id`, `media_id`, `pairing_id`) :** UUID v4 minuscule canonique à la génération (`550e8400-e29b-41d4-a716-446655440000`) ; validation canonique 8-4-4-4-12 minuscule (`[0-9a-f]{8}-...`).
- **Séquences (`device_seq`, `server_seq`) :** entiers positifs représentés en JSON par des chaînes décimales sans signe ni zéro initial (`/^[1-9][0-9]*$/`, `"0"` uniquement pour le curseur initial `since_seq`).
- **Horodatages :** UTC RFC 3339 / ISO 8601 avec millisecondes (`2026-11-19T12:41:00.000Z`).
- **Binaires :** **Base64 URL-safe sans remplissage `=`** (`[A-Za-z0-9_-]`), obligatoire pour toutes les enveloppes réseau, paquets VSK, clés scellées et QR codes.

---

## 2. Enveloppe réseau normative d'une opération (V1.1)

```json
{
  "protocol_version": 1,
  "crypto_version": 1,
  "key_epoch": 1,
  "vault_id": "550e8400-e29b-41d4-a716-446655440000",
  "device_id": "550e8400-e29b-41d4-a716-446655440001",
  "operation_id": "550e8400-e29b-41d4-a716-446655440002",
  "device_seq": "184",
  "nonce": "BASE64URL_NONCE_24B",
  "ciphertext": "BASE64URL_CIPHERTEXT_AND_TAG"
}
```
> **Correctif C2 :** `key_epoch` (entier ≥ 1) est obligatoire dans l'enveloppe réseau ET dans l'AAD afin de permettre au destinataire de dériver/sélectionner `K_OP(key_epoch)` avant déchiffrement tout en authentifiant l'époque.

### AAD canonique RFC 8785 (JCS)
```json
{
  "crypto_version": 1,
  "device_id": "550e8400-e29b-41d4-a716-446655440001",
  "device_seq": "184",
  "key_epoch": 1,
  "operation_id": "550e8400-e29b-41d4-a716-446655440002",
  "protocol_version": 1,
  "vault_id": "550e8400-e29b-41d4-a716-446655440000"
}
```
Les octets UTF-8 de la sérialisation RFC 8785 de cet objet exact forment l'AAD transmise à `XChaCha20-Poly1305-IETF`.

### Limites V1
- Enveloppe JSON max : `2 Mio` ; contenu déchiffré max : `1,5 Mio` ; lot max : `100` opérations (`MAX_BATCH_SIZE = 100`) ; fragment HTTP média max : `4 Mio` (`4 194 304` octets).

---

## 3. Contenu déchiffré (`payload`) et types d'opérations

```json
{
  "payload_version": 1,
  "operation_type": "entity.upsert",
  "entity_type": "note",
  "entity_id": "550e8400-e29b-41d4-a716-446655440010",
  "revision_id": "550e8400-e29b-41d4-a716-446655440011",
  "parent_revision_ids": [],
  "created_at": "2026-11-19T12:41:00.000Z",
  "data": {}
}
```

### Types d'opérations V1.1 (Correctif C8)
| `operation_type` | Rôle |
|---|---|
| `entity.upsert` | Création ou mise à jour complète (snapshot) d'une entité (`note`, `folder`, `category`, `tag`, `relation`). |
| `entity.trash` | Mise à la corbeille réversible (`data: { "deleted_at": "..." }`, positionne `is_deleted=true`). |
| `entity.restore` | Restauration depuis la corbeille (`data` contient le snapshot restauré avec `is_deleted=false, deleted_at: null`). |
| `entity.delete` | Suppression définitive (tombstone irréversible côté utilisateur, `data: { "deleted_at": "..." }`). |
| `media.register` | Déclaration d'une pièce jointe chiffrée (`entity_type: "attachment"`). **Sans** champ `media_nonce` (cf. C4). |
| `media.delete` | Suppression logique d'une pièce jointe (`data: { "media_id": "...", "deleted_at": "..." }`). |

---

## 4. API HTTP unifiée (`/api/v1`) (Correctif C1)

Toutes les routes opérationnelles utilisent le préfixe `/api/v1` et `Authorization: Bearer <device_access_token>` (stocké côté serveur uniquement sous forme de condensat `SHA-256`), sauf les sondes de santé (`/healthz`, `/api/v1/health`), la création de coffre (`POST /api/v1/vaults`), et les étapes du jumelage initiées par l'appareil B avant émission de son jeton (`join`, `GET pairing/{id}`, `confirm`).

Format standard d'erreur JSON :
```json
{
  "error": {
    "code": "STRING_ERROR_CODE",
    "message": "Description lisible",
    "details": {}
  }
}
```
Codes d'authentification communs à toute route protégée :
- `401 AUTH_REQUIRED` : en-tête `Authorization: Bearer ...` absent ou vide.
- `401 AUTH_INVALID` : jeton inconnu.
- `403 DEVICE_REVOKED` : appareil révoqué (`status != 'active'`).
- `403 VAULT_MISMATCH` : le jeton appartient à un autre `vault_id` que celui de la route.

### 4.1 Matrice exhaustive des endpoints V1.1

| # | Méthode & Chemin exact | Auth | Paramètres / Query | Corps de requête (JSON ou binaire) | Réponse succès | Erreurs spécifiques | Idempotence & Autorisation |
|---|---|---|---|---|---|---|---|
| 1 | `GET /healthz` *(canonique)*<br>`GET /api/v1/health` *(alias)* | Aucune | — | — | `200`<br>`{ "status": "ok", "version": "1.1.0", "api_version": 1 }` *(et `/healthz`: `{ "status": "ok", "version": "1.1.0" }`)* | — | Idempotent, lecture seule, aucune donnée coffre exposée. |
| 2 | `POST /api/v1/vaults` | Aucune | — | `{ "vault_id": uuid, "device_id": uuid, "device_name": str, "device_type": "desktop"\|"pwa"\|"mobile", "public_key_x25519": b64u, "vsk_recovery_envelope"?: obj }` | `201`<br>`{ "vault_id", "device_id", "device_access_token", "current_key_epoch": 1 }` | `400 INVALID_PAYLOAD`<br>`409 VAULT_EXISTS` | Non idempotent (refuse réinitialisation via `409 VAULT_EXISTS`). Crée coffre + appareil A + époque `1`. |
| 3 | `GET /api/v1/vaults/{vault_id}` | Bearer (membre du coffre) | `vault_id` (path UUID) | — | `200`<br>`{ "vault_id", "protocol_version": 1, "current_key_epoch": int, "status": "active" }` | `400 INVALID_PAYLOAD`<br>`401/403`<br>`404 VAULT_NOT_FOUND` | Idempotent. Réservé aux appareils actifs du coffre `vault_id`. |
| 4 | `POST /api/v1/vaults/{vault_id}/operations:batch` | Bearer (membre du coffre) | `vault_id` (path UUID) | `{ "operations": [ OperationEnvelope (1..100) ] }` | `200`<br>`{ "accepted": [{ "operation_id", "server_seq", "duplicate": bool }], "next_expected_device_seq": str }` | `400 INVALID_PAYLOAD`, `400 BATCH_TOO_LARGE`, `403 DEVICE_MISMATCH`, `404 VAULT_NOT_FOUND`, `409 OPERATION_ID_REUSE`, `409 DEVICE_SEQUENCE_REUSE`, `409 DEVICE_SEQUENCE_GAP` | **Atomique tout-ou-rien** (SQLite `BEGIN IMMEDIATE`). Idempotent si `(vault_id, operation_id)` déjà reçu avec même `(device_seq, nonce, ciphertext)` (`duplicate: true`). Vérifie `op.vault_id == vault_id` et `op.device_id == auth_device.device_id`. |
| 5 | `GET /api/v1/vaults/{vault_id}/operations` | Bearer (membre du coffre) | `vault_id` (path UUID)<br>`since_seq` (query int ≥ 0, défaut `"0"`)<br>`limit` (query `1..200`, défaut `200`) | — | `200`<br>`{ "operations": [{ "server_seq", "protocol_version", "crypto_version", "key_epoch", "vault_id", "device_id", "operation_id", "device_seq", "nonce", "ciphertext", "received_at" }], "next_since_seq": str, "has_more": bool }` | `400 INVALID_PAYLOAD`<br>`401/403` | Idempotent (lecture pure triée par `server_seq ASC`). Pas de `/devices/ack` en V1. |
| 6 | `GET /api/v1/vaults/{vault_id}/keys/epochs` | Bearer (membre du coffre) | `vault_id` (path UUID) | — | `200`<br>`{ "current_key_epoch": int, "epochs": [{ "key_epoch", "wrapped_epoch_key": b64u \| null, "created_by_device_id", "reason", "created_at" }] }` | `400/401/403`<br>`404 VAULT_NOT_FOUND` | Idempotent. Ne retourne **que** les lignes où `target_device_id == auth_device.device_id`. |
| 7 | `POST /api/v1/vaults/{vault_id}/keys/epochs` | Bearer (membre du coffre) | `vault_id` (path UUID) | `{ "new_key_epoch": int, "reason": str, "wrapped_keys": { "<device_id>": b64u } }` | `201`<br>`{ "current_key_epoch": int }` | `400 INVALID_PAYLOAD`, `400 MISSING_WRAPPED_KEY`, `400 INVALID_WRAPPED_KEYS`, `404 VAULT_NOT_FOUND`, `409 INVALID_EPOCH` | Exige `new_key_epoch == current_key_epoch + 1` et `set(wrapped_keys.keys()) == set(appareils actifs)`. Rejette toute clé ciblant un appareil révoqué ou inconnu. |
| 8 | `GET /api/v1/vaults/{vault_id}/devices` | Bearer (membre du coffre) | `vault_id` (path UUID) | — | `200`<br>`{ "devices": [{ "device_id", "device_name", "device_type", "public_key_x25519", "status": "active"\|"revoked", "created_at", "last_seen_at", "revoked_at" }] }` | `400/401/403` | Idempotent. Liste les appareils du coffre (sans jamais exposer `token_hash_sha256`). |
| 9 | `POST /api/v1/vaults/{vault_id}/devices/{device_id}/revoke` | Bearer (membre du coffre) | `vault_id`, `device_id` (path UUIDs) | `{}` | `200`<br>`{ "status": "revoked", "revoked_at": iso }` | `400 CANNOT_REVOKE_SELF`, `401/403`, `404 DEVICE_NOT_FOUND` | Idempotent sur cible valide (met à jour `status='revoked'`). Interdit de se révoquer soi-même. |
| 10 | `POST /api/v1/vaults/{vault_id}/pairing` | Bearer (Appareil A) | `vault_id` (path UUID) | `{ "pairing_id": uuid, "device_a_id": uuid, "pairing_nonce": b64u, "expires_at": iso }` | `201`<br>`{ "pairing_id", "status": "created", "expires_at" }` | `400 INVALID_PAYLOAD`, `403 DEVICE_MISMATCH`, `404 VAULT_NOT_FOUND`, `409 PAIRING_EXISTS` | Vérifie `device_a_id == auth_device.device_id` et unicité `pairing_id`. |
| 11 | `POST /api/v1/vaults/{vault_id}/pairing/{pairing_id}/join` | Secret de session (`secret_proof_hmac` vérifié E2EE par A) | `vault_id`, `pairing_id` (path UUIDs) | `{ "device_b_id": uuid, "device_b_name": str, "device_b_type": "desktop"\|"pwa"\|"mobile", "device_b_public_key_x25519": b64u, "session_nonce_b": b64u, "secret_proof_hmac": b64u }` | `200`<br>`{ "status": "joined", "device_a_id", "pairing_nonce" }` | `400 INVALID_PAYLOAD`, `404 PAIRING_NOT_FOUND`, `409 PAIRING_INVALID_STATE`, `409 DEVICE_EXISTS`, `410 PAIRING_EXPIRED` | Transition unique `created -> joined` pendant la fenêtre TTL. Refuse si `device_b_id` existe déjà. |
| 12 | `GET /api/v1/vaults/{vault_id}/pairing/{pairing_id}` | Session `pairing_id` active (A ou B) | `vault_id`, `pairing_id` (path UUIDs) | — | `200`<br>`{ "pairing_id", "status", "device_a_id", "pairing_nonce", "device_b_id", "device_b_name", "device_b_type", "device_b_public_key_x25519", "session_nonce_b", "secret_proof_hmac", "bundle_nonce", "encrypted_vault_bundle", "expires_at" }` | `400 INVALID_PAYLOAD`, `404 PAIRING_NOT_FOUND`, `410 PAIRING_EXPIRED` | Idempotent en lecture durant le TTL. `encrypted_vault_bundle` est chiffré AEAD sous `K_PAIR`. |
| 13 | `POST /api/v1/vaults/{vault_id}/pairing/{pairing_id}/approve` | Bearer (Appareil A initiateur) | `vault_id`, `pairing_id` (path UUIDs) | `{ "bundle_nonce": b64u, "encrypted_vault_bundle": b64u }` | `200`<br>`{ "status": "approved" }` | `400 INVALID_PAYLOAD`, `401/403`, `403 DEVICE_MISMATCH`, `404 PAIRING_NOT_FOUND`, `409 PAIRING_INVALID_STATE`, `410 PAIRING_EXPIRED` | Seul `sess.device_a_id` peut approuver. Transition unique `joined -> approved`. |
| 14 | `POST /api/v1/vaults/{vault_id}/pairing/{pairing_id}/confirm` | Session approuvée (Appareil B) | `vault_id`, `pairing_id` (path UUIDs) | `{ "device_b_id": uuid, "confirm_hmac": b64u }` | `200`<br>`{ "status": "confirmed", "device_access_token": str }` | `400 INVALID_PAYLOAD`, `403 DEVICE_MISMATCH` *(verrouille à 5 essais → `status='locked'`)*, `404 PAIRING_NOT_FOUND`, `409 PAIRING_INVALID_STATE`, `409 DEVICE_EXISTS`, `410 PAIRING_EXPIRED` | **Usage unique** (`approved -> confirmed`). Crée l'appareil B actif et émet son jeton Bearer unique. |
| 15 | `POST /api/v1/vaults/{vault_id}/media/uploads` | Bearer (membre du coffre) | `vault_id` (path UUID) | `{ "media_id": uuid, "encrypted_size": int >= 49, "encrypted_sha256": hex64, "key_epoch": int >= 1, "chunk_size"?: int }` | `200`<br>`{ "media_id", "status": "uploading"\|"completed", "received_bytes": int }` | `400 INVALID_PAYLOAD`, `401/403`, `404 VAULT_NOT_FOUND` | **Idempotent / Reprenable** si même `(media_id, encrypted_sha256)` : retourne `completed` ou l'offset `received_bytes` courant. |
| 16 | `PUT /api/v1/vaults/{vault_id}/media/uploads/{media_id}` | Bearer (membre du coffre) | `vault_id`, `media_id` (path UUIDs)<br>Header `Content-Range: bytes <start>-<end>/<total>` | Octets bruts `application/octet-stream` (≤ `4 194 304` octets) | `200`<br>`{ "received_bytes": int }` | `400 INVALID_RANGE`, `404 MEDIA_NOT_FOUND`, `409 RANGE_CONFLICT`, `409 SIZE_EXCEEDED`, `413 CHUNK_TOO_LARGE` | Séquentiel strict sur `start == received_bytes`, `total == encrypted_size`, `start + len(chunk) <= encrypted_size`. |
| 17 | `POST /api/v1/vaults/{vault_id}/media/uploads/{media_id}/complete` | Bearer (membre du coffre) | `vault_id`, `media_id` (path UUIDs) | `{}` | `200`<br>`{ "media_id", "status": "completed", "encrypted_sha256": hex64 }` | `400 SIZE_MISMATCH`, `400 CHECKSUM_MISMATCH`, `404 MEDIA_NOT_FOUND` | **Idempotent** si déjà `completed`. Vérifie taille exacte + `SHA-256` avant promotion atomique `.bin.part -> .bin`. |
| 18 | `GET /api/v1/vaults/{vault_id}/media/{media_id}` | Bearer (membre du coffre) | `vault_id`, `media_id` (path UUIDs) | — | `200` `application/octet-stream` (flux binaire chiffré `NTRM1`) | `401/403`, `404 MEDIA_NOT_FOUND` | Idempotent en lecture seule tant que `status='completed' AND deleted_at IS NULL`. |
| 19 | `DELETE /api/v1/vaults/{vault_id}/media/{media_id}` | Bearer (membre du coffre) | `vault_id`, `media_id` (path UUIDs) | — | `200`<br>`{ "status": "deleted", "media_id" }` | `401/403`, `404 MEDIA_NOT_FOUND` | Idempotent sur l'état supprimé ; supprime le fichier disque et marque `status='deleted'`. |

> **Remarques d'harmonisation Phase 2 :**
> - `confirm_hmac` et `device_b_public_key_x25519` / `device_b_type` / `since_seq` / `/pairing/{id}/join` / `/devices/{id}/revoke` / `/media/uploads/{media_id}` reflètent exactement la convention unifiée implémentée dans `sync-server/app/main.py`, `sync-server/app/stdlib_server.py`, `sync/desktop/engine.py` et `web/src/sync/engine.ts`.
> - Pas de route `/devices/ack` en V1 : chaque appareil persiste localement son curseur `last_server_seq` après application transactionnelle réussie.
