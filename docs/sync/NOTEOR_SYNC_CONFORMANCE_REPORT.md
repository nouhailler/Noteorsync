# NOTEOR SYNC V1.1 — RAPPORT DE CONFORMITÉ ET D'HARMONISATION

> **Mission :** Audit de conformité et passe corrective Noteor Sync v1.1 (`nouhailler/Noteorsync`)  
> **Portée :** `docs/sync/*`, `sync-server/app/*`, `sync/desktop/*`, `web/src/sync/*`, `web/src/db/*`, `tests/*`

---

## 1. Synthèse exécutive

La passe corrective V1.1 a permis d'aligner les quatre spécifications normatives dans `docs/sync/`, les clients Python Desktop et TypeScript PWA, les deux points d'entrée HTTP du serveur (`sync-server/app/main.py` et `sync-server/app/stdlib_server.py` adossés à `sync-server/app/services/sync_service.py`) ainsi que les suites de tests croisées.

Aucun paramètre cryptographique normatif n'a été modifié (`Argon2id` `65 536 KiB` / `t=3` / `p=1`, `HKDF-SHA-256`, `XChaCha20-Poly1305-IETF`, `NTRM1` secretstream, `X25519` `crypto_box_seal`, RFC 8785 JCS, Base64URL sans padding).

---

## 2. Conformité par phase

### Phase 1 — Normalisation documentaire

- Les 4 documents normatifs de référence résident désormais dans `docs/sync/` avec références croisées unifiées :
  - `docs/sync/NOTEOR_SYNC_ARCHITECTURE.md` (document d'architecture complet + Annexe §25 de réconciliation v1.1).
  - `docs/sync/NOTEOR_SYNC_PROTOCOL.md` (protocole HTTP `/api/v1`, encodages, limites et matrice exhaustive des 19 routes).
  - `docs/sync/NOTEOR_SYNC_CRYPTO.md` (primitives, hiérarchie de clés, AAD JCS, flux binaire `NTRM1`, protocole crypto de jumelage QR V1.1).
  - `docs/sync/NOTEOR_SYNC_DATA_MODEL.md` (schéma unifié Dexie v2 / SQLite WAL, identifiants UUID, horodatages UTC RFC 3339, Wikilinks `@uuid`, DAG de révisions et conflits).
- `README.md` mis à jour (`## 📚 Documentation`) pour pointer exactement vers ces fichiers et le présent rapport.

### Phase 2 — Harmonisation du protocole HTTP (`/api/v1`)

Corrections appliquées :
1. **`GET /api/v1/vaults/{vault_id}` & `GET /api/v1/health`** ajoutés dans `sync_service.py` (`get_vault_info`), `main.py`, `stdlib_server.py`, `sync/desktop/engine.py` (`get_vault_info`) et `web/src/sync/engine.ts` (`getVaultInfo`).
2. **Durcissement des validations serveur (`sync_service.py`) :**
   - `rotate_epoch` : vérifie désormais `set(wrapped_keys.keys()) == active_ids` ; rejette toute clé destinée à un appareil révoqué ou inconnu via `400 INVALID_WRAPPED_KEYS`.
   - `init_media_upload` : vérifie l'existence du coffre (`404 VAULT_NOT_FOUND`) et borne `chunk_size` optionnel entre `1` et `4 194 304`.
   - `upload_media_chunk` : impose `MAX_HTTP_CHUNK_SIZE = 4 194 304` (`413 CHUNK_TOO_LARGE`), rejette les fragments vides (`400 INVALID_RANGE`) et les dépassements de taille (`409 SIZE_EXCEEDED`).
   - `create_pairing` / `join_pairing` / `confirm_pairing` : gardes explicites `404 VAULT_NOT_FOUND`, `409 PAIRING_EXISTS` et `409 DEVICE_EXISTS` avant insertion SQLite (plus de `500 IntegrityError`).
3. **Robustesse HTTP (`stdlib_server.py`) :** `_read_body()` déplacé dans le bloc `try ... except` avec validation stricte de `Content-Length` (entier `>= 0` -> `400 INVALID_PAYLOAD`) et rejet des corps JSON non-objet.

| Endpoint | `main.py` (FastAPI) | `stdlib_server.py` | Client Python | Client PWA TS |
|---|---|---|---|---|
| `GET /healthz` | Oui | Oui | — | — |
| `GET /api/v1/health` | Oui | Oui | — | — |
| `POST /api/v1/vaults` | Oui | Oui | `bootstrap_vault` | `bootstrapVault` |
| `GET /api/v1/vaults/{vault_id}` | Oui | Oui | `get_vault_info` | `getVaultInfo` |
| `POST /api/v1/vaults/{vault_id}/operations:batch` | Oui | Oui | `push_operations` | `pushOperations` |
| `GET /api/v1/vaults/{vault_id}/operations` | Oui | Oui | `pull_operations` | `pullOperations` |
| `GET /api/v1/vaults/{vault_id}/keys/epochs` | Oui | Oui | `list_epoch_keys` | `listEpochKeys` |
| `POST /api/v1/vaults/{vault_id}/keys/epochs` | Oui | Oui | `rotate_epoch` | `rotateEpoch` |
| `GET /api/v1/vaults/{vault_id}/devices` | Oui | Oui | `list_devices` | `listDevices` |
| `POST /api/v1/vaults/{vault_id}/devices/{device_id}/revoke` | Oui | Oui | `revoke_device` | `revokeDevice` |
| `POST /api/v1/vaults/{vault_id}/pairing` | Oui | Oui | `create_pairing_session` | `createPairingSession` |
| `POST /api/v1/vaults/{vault_id}/pairing/{id}/join` | Oui | Oui | `join_pairing_session` | `joinPairingSession` |
| `GET /api/v1/vaults/{vault_id}/pairing/{id}` | Oui | Oui | `get_pairing_status` | `getPairingStatus` |
| `POST /api/v1/vaults/{vault_id}/pairing/{id}/approve` | Oui | Oui | `approve_pairing_session` | `approvePairingSession` |
| `POST /api/v1/vaults/{vault_id}/pairing/{id}/confirm` | Oui | Oui | `confirm_pairing_session` | `confirmPairingSession` |
| `POST /api/v1/vaults/{vault_id}/media/uploads` | Oui | Oui | `init_media_upload` | `initMediaUpload` |
| `PUT /api/v1/vaults/{vault_id}/media/uploads/{id}` | Oui | Oui | `upload_media_chunk` | `uploadMediaChunk` |
| `POST /api/v1/vaults/{vault_id}/media/uploads/{id}/complete` | Oui | Oui | `complete_media_upload` | `completeMediaUpload` |
| `GET /api/v1/vaults/{vault_id}/media/{id}` | Oui | Oui | `download_media_blob` | `downloadMediaBlob` |
| `DELETE /api/v1/vaults/{vault_id}/media/{id}` | Oui | Oui | — *(extension future)* | — *(extension future)* |

### Phase 3 — Harmonisation cryptographique Python ↔ TypeScript

| Primitive / Règle | Python (`sync/desktop/crypto.py`) | TypeScript (`web/src/sync/crypto.ts`) | Statut |
|---|---|---|---|
| **Argon2id13** (`65536 KiB`, `t=3`, `p=1`) | `libsodium` (`crypto_pwhash`) | `libsodium-wrappers-sumo` (`crypto_pwhash`) | Parité binaire vérifiée |
| **HKDF-SHA-256** (`RFC 5869 A.1` + hiérarchie Noteor) | `hmac` + `sha256` | `@noble/hashes/hkdf` (`sha256`) | Parité binaire vérifiée |
| **XChaCha20-Poly1305-IETF** (opérations + `VSK`) | `libsodium` | `libsodium-wrappers-sumo` | Parité binaire vérifiée |
| **RFC 8785 (JCS)** | `_jcs_serialize` + `_jcs_encode_number` *(corrigé ECMA-262)* | `canonicalize` | Parité exacte vérifiée (`1.0`->`1`, `-0.0`->`0`, exposants ES6, tri UTF-16) |
| **Base64URL sans padding + rejet canonique** | `base64.urlsafe_b64*` + round-trip strict | `sodium.base64_variants.URLSAFE_NO_PADDING` + round-trip strict | Conforme |
| **UUID -> 16 octets** | `uuid.UUID.bytes` | `uuidToBytes` (hex) | Conforme |
| **Secretstream `NTRM1`** | `crypto_secretstream_xchacha20poly1305` + header AAD `1..32` | `crypto_secretstream_xchacha20poly1305` + header AAD `1..32` | Parité binaire bidirectionnelle vérifiée |
| **`crypto_box_seal` (rotation d'époque)** | `crypto_box_seal` + payload JCS lié `(vault_id, key_epoch, target_device_id)` | `crypto_box_seal` + payload JCS lié `(vault_id, key_epoch, target_device_id)` | Parité bidirectionnelle vérifiée |
| **Jumelage (`secret_proof_hmac`, `K_PAIR`, `confirm_hmac`)** | Implémenté et vérifié | Implémenté et vérifié | Parité bidirectionnelle vérifiée |

> **Correctif crypto notable :** Dans `sync/desktop/crypto.py`, `_jcs_encode_number` produisait précédemment `"1.0"` pour `float(1.0)` et `"1e-07"` pour `1e-7`. La fonction applique désormais fidèlement les règles `Number::toString(10)` d'ECMA-262 / RFC 8785, garantissant l'identité avec `canonicalize` côté TypeScript.

### Phase 4 — Modèle de données, migrations locales et fonctionnement hors ligne

- **Migrations v1 -> v2 :** Attribution de `sync_id` UUID v4 canoniques minuscules, normalisation des horodatages en RFC 3339 UTC (`YYYY-MM-DDTHH:mm:ss.sssZ`), réécriture des Wikilinks en `[[Titre|@uuid]]`, déduplication insensible à la casse des tags (`note_tags` remappés sans erreur).
- **Transactions & file d'attente :** Journal `sync_operations` atomique avec séquence locale monotone `device_seq`, DAG de révisions (`parent_revision_ids`), file de dépendances différées (`sync_pending_ops`) et création explicite de `sync_conflicts` sans écrasement silencieux.
- **Mode autonome hors ligne :** Vérifié par tests dédiés Python et TypeScript qu'en l'absence de `vault_id` configuré, l'application fonctionne intégralement en local (CRUD, corbeille/restauration, tags, catégories) sans échec et sans empiler de trafic réseau.

---

## 3. Registre strict de vérification

### 3.1 Faits vérifiés dans le dépôt

- Inspection directe de tous les documents `docs/sync/*.md`, de `README.md`, de `sync-server/app/*`, `sync/desktop/*`, `web/src/sync/*`, `web/src/db/index.ts`, `web/public/_headers`, `web/netlify.toml`, `web/vite.config.ts` et de l'ensemble des fichiers `tests/`.
- Absence vérifiée par `grep` de toute journalisation ou persistance non chiffrée de secrets (`VSK`, `KEK`, `EpochRootKey`, `device_access_token` en clair dans `console.log` ou `localStorage`) dans `web/src/sync/`, `web/src/components/SyncModal.tsx`, `sync/` et `sync-server/app/`.
- Régénération déterministe réussie des vecteurs croisés (`python3 tests/crypto-vectors/generate_vectors.py`), incluant les nouveaux vecteurs `jcs/` et `malformed/`.

### 3.2 Tests réellement exécutés et réussis

| Suite | Commande exécutée | Résultat vérifié |
|---|---|---|
| **Compilation bytecode Python** | `python3 -m py_compile sync-server/app/main.py sync-server/app/stdlib_server.py sync-server/app/services/sync_service.py sync-server/app/storage/database.py sync/desktop/crypto.py sync/desktop/engine.py sync/desktop/sqlite_sync_adapter.py` | Code de sortie `0` (aucune erreur de syntaxe) |
| **Tests Python (`unittest`)** | `python3 -m unittest discover -s tests/python -v` | **`20` tests réussis (`OK`) en `4.075s`** : vecteurs Argon2id/HKDF/AEAD/`NTRM1`/Pairing/JCS/rejets malformés, interop TS->Python en Node ESM, adapter SQLite WAL + mode hors-ligne autonome, serveur HTTP live + E2E Desktop↔Desktop + cas de sécurité V1.1, et utilitaires `sync-server-manager` |
| **Tests TypeScript (`Vitest`)** | `npx vitest run` (dans `web/`) | **`3` fichiers de test, `12` tests réussis (`passed`) en `3.38s`** : vecteurs croisés `crypto.test.ts` (`7`), migration Dexie v2 / outbox / DAG / mode hors-ligne `db_and_engine.test.ts` (`4`), E2E live PWA TS <-> Serveur HTTP Python <-> Desktop Python `sync_e2e.test.ts` (`1`) |
| **Build & Lint Applet Web** | `npm run build` (`compile_applet`) & `npm run lint` (`lint_applet`) | Vérifiés conjointement lors de la passe finale de validation |

### 3.3 Tests échoués

- **Aucun test exécuté n'a échoué.**

### 3.4 Éléments non testables dans cet environnement

1. **Exécution runtime de `sync-server/app/main.py` (FastAPI / Uvicorn / Pydantic) :** Les paquets Python `fastapi`, `uvicorn`, `pydantic`, `httpx`, `pytest`, `pynacl`, `cryptography` et `rfc8785` ne sont pas installés dans le conteneur courant (`ModuleNotFoundError`). `main.py` est vérifié par `py_compile` et par partage direct de la couche métier `SyncService` (elle-même testée via `stdlib_server.py`), mais les validateurs FastAPI/Uvicorn n'ont pas été exécutés en HTTP réel ici.
2. **Application Desktop PyQt6 complète (`main.py`, `database.py`, `ui/*`, `core/*`) :** Seule la couche de synchronisation `sync/desktop/` (`crypto.py`, `sqlite_sync_adapter.py`, `engine.py`) et `sync-server-manager/` sont présentes dans cet espace de travail ; l'application graphique PyQt6 complète et la dépendance `PyQt6` ne sont ni présentes ni exécutables ici.
3. **Déploiement réel Cloudflare Pages / Workbox SW / Docker / TLS :** Le Service Worker généré par `vite-plugin-pwa`, les en-têtes CSP appliqués par Cloudflare/Netlify en production et l'orchestration `docker compose` sur machine hôte relèvent de la vérification en environnement cible.

### 3.5 Hypothèses

- L'application Noteor Desktop complète (hors dépôt courant) branche ses opérations CRUD sur `DesktopSyncDatabase` (`sync/desktop/sqlite_sync_adapter.py`) et `DesktopSyncEngine` (`sync/desktop/engine.py`) lors de son intégration finale.
- L'environnement serveur de production déploie `sync-server/app/main.py` derrière un reverse-proxy HTTPS/TLS (ou VPN WireGuard/Tailscale) conformément à `DEPLOYMENT.md`.

### 3.6 Risques résiduels

- **Divergence potentielle future entre `main.py` et `stdlib_server.py` :** Atténuée par la délégation commune vers `SyncService`, mais toute nouvelle route ou en-tête HTTP devra continuer d'être testée sous `uvicorn`/FastAPI dans une CI disposant des dépendances `sync-server/requirements.txt`.
- **Décalage entre l'intégration PyQt6 finale et l'adaptateur `sqlite_sync_adapter.py` :** Nécessitera une validation d'intégration dans le dépôt Desktop complet une fois `sync/desktop/` relié aux signaux UI.
