# NOTEOR_SYNC_PROTOCOL.md (v1.1 — Contrat révisé)

**Spécification du protocole de synchronisation Noteor**  
**Version :** 1.1 — 19 novembre 2026 (résout les contradictions C1–C11 relevées dans `NOTEOR_SYNC_AUDIT_REPORT.md`)  
**Périmètre :** Noteor Desktop (Python / SQLite), Noteor PWA (React / TypeScript / Dexie / IndexedDB), Noteor Sync Server (FastAPI / SQLite WAL).  
**Documents associés :** `NOTEOR_SYNC_DATA_MODEL.md`, `NOTEOR_SYNC_CRYPTO.md`, `NOTEOR_SYNC_AUDIT_REPORT.md`.

---

## 1. Identifiants, séquences, horodatages et encodages

- **Identifiants (`vault_id`, `device_id`, `operation_id`, `entity_id`, `revision_id`, `media_id`, `upload_id`, `pairing_id`) :** UUID v4 minuscule canonique (`550e8400-e29b-41d4-a716-446655440000`).
- **Séquences (`device_seq`, `server_seq`) :** entiers positifs représentés en JSON par des chaînes décimales sans signe ni zéro initial (`/^[1-9][0-9]*$/`, `"0"` uniquement pour le curseur initial).
- **Horodatages :** UTC RFC 3339 / ISO 8601 avec millisecondes (`2026-11-19T12:41:00.000Z`).
- **Binaires :** **Base64 URL-safe sans remplissage `=`** (`[A-Za-z0-9_-]`), obligatoire pour toutes les enveloppes réseau, paquets VSK et QR codes.

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
- Enveloppe JSON max : `2 Mio` ; contenu déchiffré max : `1,5 Mio` ; lot max : `100` opérations.

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

Toutes les routes opérationnelles utilisent le préfixe `/api/v1` et `Authorization: Bearer DEVICE_ACCESS_TOKEN` (sauf `health` et les appels initiaux d'appairage `pairing/{id}/request` / `confirm` authentifiés par ticket/secret de jumelage).

### 4.1 Santé & Coffre
- `GET /api/v1/health` → `{ "status": "ok", "api_version": 1 }`
- `POST /api/v1/vaults` (enregistrement initial d'un coffre et du premier appareil A avec sa clé publique `device_pub_key_x25519`, retournant `vault_id`, `device_id`, `device_access_token`)
- `GET /api/v1/vaults/{vault_id}` → `{ "vault_id": "...", "protocol_version": 1, "current_key_epoch": 1, "status": "active" }`

### 4.2 Journal d'opérations (pas de `/devices/ack` en V1)
- `POST /api/v1/vaults/{vault_id}/operations:batch`  
  - Tout-ou-rien atomique dans SQLite WAL.  
  - Vérifie unicité `(vault_id, operation_id)` (même contenu = idempotent, contenu différent = `409 OPERATION_ID_REUSE`) et `(vault_id, device_id, device_seq)` (contenu différent = `409 DEVICE_SEQUENCE_REUSE`, trou = `409 DEVICE_SEQUENCE_GAP` avec `expected_device_seq`).  
  - Attribue `server_seq` croissant et retourne `{ "accepted": [...], "next_expected_device_seq": "..." }`.
- `GET /api/v1/vaults/{vault_id}/operations?after={cursor}&limit=100`  
  - Retourne `{ "operations": [{ "server_seq": "...", "envelope": {...} }], "next_cursor": "...", "has_more": bool }`.

### 4.3 Médias chiffrés (Correctif C5)
- `POST /api/v1/vaults/{vault_id}/media`  
  Corps : `{ "media_id", "encrypted_size", "encrypted_sha256" }` → `{ "upload_id", "chunk_size": 4194304, "received_bytes": 0, "status": "uploading" }`.
- `PUT /api/v1/vaults/{vault_id}/media/{media_id}/uploads/{upload_id}`  
  `Content-Type: application/octet-stream`, `Content-Range: bytes <start>-<end>/<total>`.  
  **Découpage découplé :** le client découpe simplement les octets du **blob binaire chiffré final** en fragments HTTP de taille ≤ `4 194 304` octets (4 Mio).
- `POST /api/v1/vaults/{vault_id}/media/{media_id}/uploads/{upload_id}/complete`  
  Vérifie taille exacte + `SHA-256` du fichier chiffré assemblé puis publie le média (`status: "available"`).
- `GET /api/v1/vaults/{vault_id}/media/{media_id}` → flux binaire chiffré brut.

### 4.4 Jumelage, appareils et clés d'époque (Correctif C1 & C3)
- `POST /api/v1/vaults/{vault_id}/pairing` (Appareil A authentifié)  
  Crée la session : `{ "pairing_id", "secret_hash_sha256", "device_a_id", "device_a_pub_key", "expires_at" }`.
- `POST /api/v1/vaults/{vault_id}/pairing/{pairing_id}/request` (Appareil B)  
  Soumet `{ "device_b_id", "device_name", "platform", "device_b_pub_key", "session_nonce_b", "secret_proof_hmac" }`.
- `GET /api/v1/vaults/{vault_id}/pairing/{pairing_id}` (Poll par A pour voir la demande, ou par B pour récupérer le paquet approuvé).
- `POST /api/v1/vaults/{vault_id}/pairing/{pairing_id}/approve` (Appareil A authentifié)  
  Enregistre le paquet chiffré par `K_PAIR` (`nonce`, `ciphertext`) contenant le `VSK`, la liste des `epoch_keys` autorisées et la confirmation A.
- `POST /api/v1/vaults/{vault_id}/pairing/{pairing_id}/confirm` (Appareil B)  
  Fournit `confirmation_hmac` prouvant le déchiffrement réussi du paquet ; le serveur marque B autorisé, génère et retourne `{ "device_id", "device_access_token" }` et consomme le ticket (`usage unique`).
- `GET /api/v1/vaults/{vault_id}/devices` → liste des appareils autorisés/révoqués (avec `device_id`, `device_name`, `platform`, `public_key`, `revoked_at`).
- `DELETE /api/v1/vaults/{vault_id}/devices/{device_id}` → révoque immédiatement l'appareil et invalide son `DEVICE_ACCESS_TOKEN`.
- `POST /api/v1/vaults/{vault_id}/keys/epochs` → enregistre une nouvelle époque `key_epoch` et les enveloppes scellées (`crypto_box_seal`) de `EpochRootKey(key_epoch)` pour chaque appareil actif non révoqué.
- `GET /api/v1/vaults/{vault_id}/keys/epochs` → retourne à l'appareil demandeur ses propres enveloppes d'époques scellées.
