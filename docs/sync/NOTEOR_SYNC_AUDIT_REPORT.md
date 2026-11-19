# NOTEOR SYNC — RAPPORT D'AUDIT ET DE CONFORMITÉ (MISSION 0)

**Date :** 19 novembre 2026  
**Périmètre audité :**
- Documents de référence :
  - `public/assets/aistudio/NOTEOR_SYNC_ARCHITECTURE_9bbdbda2.md` (`NOTEOR_SYNC_ARCHITECTURE.md`, v1.0)
  - `public/assets/aistudio/NOTEOR_SYNC_PROTOCOL_d9fb92eb.md` (`NOTEOR_SYNC_PROTOCOL.md`, v1.0)
  - `public/assets/aistudio/NOTEOR_SYNC_CRYPTO_5c70f66e.md` (`NOTEOR_SYNC_CRYPTO.md`, v1.0)
- Code applicatif présent dans l'espace de travail : Noteor PWA (`web/`)
- Documentation résiduelle de Noteor Desktop : `CONTEXT.md`, `README.md`

---

## 0. Synthèse exécutive et périmètre vérifiable

### 0.1 État général de préparation
Le projet **n'est pas prêt à démarrer l'implémentation de Noteor Sync**.  
Bien que la vision architecturale globale (Local-First, journal d'opérations append-only, chiffrement E2E sans connaissance côté serveur) soit solide dans ses intentions, **trois verrous bloquants** empêchent tout développement immédiat :
1. **Inaccessibilité du code source Noteor Desktop** dans l'espace de travail actuel (voir §0.2).
2. **Contradictions normatives critiques** entre `NOTEOR_SYNC_ARCHITECTURE.md`, `NOTEOR_SYNC_PROTOCOL.md` et `NOTEOR_SYNC_CRYPTO.md` (notamment sur la distribution/révocation des clés par époque, l'absence de `key_epoch` dans l'enveloppe et l'AAD, le format de chiffrement des médias, les endpoints HTTP et le jumelage).
3. **Absence totale de socle de synchronisation et de tests** dans la PWA (`web/`), accompagnée d'écarts structurels (absence d'UUID, dates non UTC/RFC 3339, mutations non transactionnelles, bug de mise à la corbeille dans `NoteEditor.tsx`).

### 0.2 Éléments impossibles à vérifier dans l'espace de travail actuel (Fait vérifié)
Lors de la vérification du système de fichiers et de Git :
- Il n'existe **aucun dépôt `.git`** dans `/app/applet`.
- Tous les fichiers sources Python de **Noteor Desktop** (`main.py`, `config.py`, `database.py`, `core/*`, `ui/*`, `packaging/*`, `requirements.txt`, `pyproject.toml`, `install.sh`) ainsi que tout éventuel serveur `sync-server/` sont **absents du disque** (supprimés lors de la migration préalable vers l'environnement Node/Vite).
- Seuls subsistent pour Desktop les fichiers documentaires `CONTEXT.md` (daté du 20–21 mars 2026, référence commit `8506f89`) et `README.md`.

Par conséquent, les éléments suivants **ne peuvent pas être vérifiés dans le code réel** et constituent un prérequis bloquant avant la Phase B :
- Le schéma SQLite exact de `database.py` (colonnes, types, contraintes `CHECK`, `FOREIGN KEY`, `ON DELETE`, index, mode `PRAGMA journal_mode=WAL`).
- La présence effective d'une table `folders` côté Desktop : `README.md:231` mentionne « dossiers hiérarchiques » côté Desktop, mais `CONTEXT.md:34` ne liste que 5 tables (`notes`, `categories`, `tags`, `note_tags`, `attachments`) et le fichier `CONTEXT.md` est antérieur aux ajouts visibles dans l'arborescence initiale (`ui/help_window.py`, `packaging/`).
- Toutes les méthodes de mutation de `database.py`, la gestion des transactions SQLite, le format des dates côté Desktop (`CONTEXT.md:43` mentionne uniquement un rôle UI `"YYYY-MM-DD HH:MM"`).
- Le format exact des Wikilinks côté Desktop (s'ils existent côté Python).
- Les dépendances Python exactes (`requirements.txt` / `pyproject.toml`) et la présence ou non de tests automatisés Desktop.
- Le code de **Noteor Sync Server** (FastAPI / SQLite), qui n'existe pas encore.

> **Convention appliquée dans ce rapport :** chaque constat distingue explicitement les **[Fait vérifié]**, **[Non vérifiable — code absent]**, **[Hypothèse]** et **[Recommandation]**.

---

## 1. Les éléments conformes (PWA auditée et socle documentaire)

| Domaine | Constat vérifié | Preuve dans le code / documents |
|---|---|---|
| Fonctionnement local et hors ligne PWA | **[Fait vérifié]** La PWA stocke 100 % des données localement dans IndexedDB via Dexie sans dépendance serveur pour les notes, catégories, dossiers, tags et médias. | `web/src/db/index.ts:4-25`, `web/vite.config.ts:8-36` (`VitePWA` avec `globPatterns` Workbox). |
| Conservation des identifiants locaux auto-incrémentés | **[Fait vérifié]** Toutes les tables principales utilisent des clés primaires numériques auto-incrémentées (`++id`) ou composite (`[note_id+tag_id]`), prêtes à être conservées localement en parallèle d'un futur champ `sync_id`. | `web/src/db/index.ts:14-21`. |
| Corbeille (soft-delete) existante sur les notes | **[Fait vérifié]** Le modèle `Note` inclut déjà `is_deleted: boolean` et `deleted_at?: string`, avec bascule corbeille, restauration et suppression définitive dans la liste. | `web/src/types/index.ts:9-10`, `web/src/db/index.ts:92-104`, `web/src/components/NoteList.tsx:171-179,331-340`. |
| Unicité insensible à la casse des tags | **[Fait vérifié]** L'ajout d'un tag normalise en `trim().toLowerCase()` et vérifie l'existence via `equalsIgnoreCase`. Conforme à l'exigence de `NOTEOR_SYNC_ARCHITECTURE.md §5.6`. | `web/src/db/index.ts:113-116`, `web/src/db/index.ts:366`. |
| Architecture de séparation OpenRouter | **[Fait vérifié]** L'appel IA de transcription audio est déclenché explicitement par l'utilisateur sur des blobs locaux et n'est pas couplé à une base distante. | `web/src/components/NoteEditor.tsx:288-316`, `web/src/services/openrouter.ts:26-69`. |
| En-têtes de sécurité de base (Netlify) | **[Fait vérifié]** `X-Frame-Options = "DENY"`, `X-Content-Type-Options = "nosniff"`, `Referrer-Policy` et `Permissions-Policy` sont configurés pour la production Netlify. | `web/netlify.toml:11-17`. |
| Principes cryptographiques généraux | **[Fait vérifié]** Les trois documents s'accordent sur le modèle Local-First, l'absence de secrets en clair côté serveur, l'usage de XChaCha20-Poly1305-IETF (nonce 24 octets, tag 16 octets), Argon2id et HKDF-SHA-256. | `ARCHITECTURE.md §9`, `PROTOCOL.md §6`, `CRYPTO.md §3.1, §4, §5, §7`. |

---

## 2. Les éléments non conformes (écarts du code PWA existant vs spécifications)

### 2.1 Schéma Dexie et modèles métier incomplets (`web/src/types/index.ts`, `web/src/db/index.ts`)
- **[Fait vérifié]** La base Dexie `noteor` est uniquement en `version(1)` (`web/src/db/index.ts:14-21`) et aucune table ne possède d'identifiant global `sync_id` / `entity_id` (UUID), de `revision_id` ni de `parent_revision_ids`.
- **[Fait vérifié]** Champs absents par rapport à `NOTEOR_SYNC_ARCHITECTURE.md §5.3–5.7` :
  - `Note` (`web/src/types/index.ts:1-11`) : pas de `sync_id`, `revision`, `metadata` (ni `category_sync_id`/`folder_sync_id`).
  - `Category` (`web/src/types/index.ts:13-19`) : pas de `sync_id`, `created_at`, `updated_at`, `revision`.
  - `Folder` (`web/src/types/index.ts:21-26`) : pas de `sync_id`, `updated_at`, `revision`. De plus, `Sidebar.tsx:74` passe `created_at: ''` (écrasé par `saveFolder` en `web/src/db/index.ts:167`).
  - `Tag` (`web/src/types/index.ts:28-32`) : pas de `sync_id`, `created_at`, `updated_at`, `revision`.
  - `NoteTag` (`web/src/types/index.ts:34-37`) : clé primaire composite `[note_id+tag_id]` sans `id`, sans `sync_id`, sans `revision`.
  - `Attachment` (`web/src/types/index.ts:39-48`) : pas de `sync_id`, `mime_type` (pourtant présent sur `data.type` du `Blob`), `size`, `encrypted_object_id`/`media_id`, `encrypted_sha256`, `encryption_version`/`media_crypto_version`, `metadata`. Par ailleurs, `thumbnail` est stocké en Data URL JPEG inline (`NoteEditor.tsx:648`, `string` en base64), contrairement à Desktop qui stocke des fichiers dans `media/thumbnails/` (`CONTEXT.md:63`).

### 2.2 Format des dates non conforme et surcharge sémantique de `updated_at`
- **[Fait vérifié]** `now()` (`web/src/db/index.ts:27-29`) retourne `new Date().toISOString().slice(0, 19).replace('T', ' ')` (ex. `"2026-11-19 06:54:00"`), supprimant le `T`, les millisecondes et le suffixe `Z`. Même format tronqué utilisé dans le filtre `getNotes()` (`web/src/db/index.ts:77`) et dans `NoteList.formatDate()` (`web/src/components/NoteList.tsx:163`).
  - **Écart :** `NOTEOR_SYNC_PROTOCOL.md §3.3` et `NOTEOR_SYNC_ARCHITECTURE.md §7.2` exigent le format **UTC RFC 3339 / ISO 8601** complet (`2026-11-19T12:41:00.000Z`).
  - À noter : `exportAllNotes()` / `exportSelectedNotes()` (`web/src/db/index.ts:271,333`) utilisent déjà `new Date().toISOString()` pour `exported_at`, créant deux formats de dates différents dans le même fichier.
- **[Fait vérifié]** `CalendarView.tsx:125-130` (`redate`) permet à l'utilisateur de **modifier manuellement `note.updated_at`** (`db.notes.update(noteId, { updated_at: `${newDate} ${time}` })`) pour déplacer une note dans le calendrier.
  - **Risque/Écart :** bien que le protocole n'utilise pas `updated_at` comme curseur de sync, cette fonctionnalité mélange « date affichée/planifiée » et « horodatage de modification ». En synchronisation, modifier `updated_at` doit produire une opération `entity.upsert` et ne pas casser les filtres de tri ou la représentation ISO 8601.

### 2.3 Absence d'atomicité transactionnelle et mutations dispersées hors de la couche DB
`NOTEOR_SYNC_ARCHITECTURE.md §10.3` et `NOTEOR_SYNC_PROTOCOL.md §9.1, §9.3` imposent que toute mutation métier soit enregistrée **dans la même transaction Dexie** que la création de révision, l'incrément de `device_seq` et l'insertion dans le journal local.
- **[Fait vérifié]** **Aucune** fonction de `web/src/db/index.ts` n'utilise `db.transaction(...)`. Toutes les mutations multi-tables ou multi-lignes sont séquentielles et non atomiques :
  - `permanentlyDeleteNote` (`web/src/db/index.ts:100-104`) : 3 `await` séparés (`attachments`, `note_tags`, `notes`).
  - `addTagToNote` (`web/src/db/index.ts:113-125`) : lecture puis création éventuelle du tag puis création du lien `note_tags`.
  - `deleteCategory` (`web/src/db/index.ts:154-158`) : 3 opérations séparées (notes, sous-catégories, catégorie).
  - `deleteFolder` (`web/src/db/index.ts:170-174`) : 3 opérations séparées (notes, sous-dossiers, dossier).
  - `updateWikilinksOnRename` (`web/src/db/index.ts:202-224`) : boucle `for` de `db.notes.update` individuels.
  - `moveNotesBulk`, `tagNotesBulk`, `deleteNotesBulk` (`web/src/db/index.ts:228-245`) : boucles `for` sans transaction.
  - `importNotes` (`web/src/db/index.ts:338-382`) : boucle non transactionnelle avec création de notes, tags et `note_tags`.
- **[Fait vérifié]** Un composant UI contourne directement les helpers de `db/index.ts` :
  - `CalendarView.tsx:129` appelle directement `db.notes.update(...)`.
- **[Fait vérifié — BUG EXISTANT]** Dans `NoteEditor.tsx:585-589`, le bouton corbeille confirme « Mettre cette note à la corbeille ? » mais appelle `await saveNote({ ...note, id: noteIdRef.current });` **sans passer `is_deleted: true` ni `deleted_at`, et sans appeler `deleteNote(id)`**. Conséquence : le bouton supprimer dans l'éditeur ne supprime pas la note (il met simplement à jour `updated_at` avec l'ancien objet `note`). De plus, si la note n'a jamais été sauvegardée (`noteIdRef.current` est `undefined`), il crée une note vide en base.

### 2.4 Wikilinks incompatibles avec les UUID (`NoteEditor.tsx`, `GraphView.tsx`, `db/index.ts`)
- **[Fait vérifié]** Le format canonique actuel dans la PWA est `[[Titre|123]]` avec identifiant numérique strict `\d+` :
  - `NoteEditor.tsx:84` : `/\[\[([^\]|[\n]+)\|(\d+)\]\]/g`
  - `NoteEditor.tsx:218` : `[[${noteTitle}|${id}]]`
  - `NoteEditor.tsx:324` : `const id = Number(rest.slice(0, colon));`
  - `web/src/db/index.ts:188` (`getBacklinks`) : `` `|${noteId}]]` ``
  - `web/src/db/index.ts:209` (`updateWikilinksOnRename`) : `` `\\[\\[${escaped}\\|${noteId}\\]\\]` ``
  - `GraphView.tsx:60` (`parseTargetIds`) : `/\[\[([^\]|[\n]+)\|(\d+)\]\]/g` et `Number(id)`.
  - Aucun de ces parseurs ne reconnaît le format cible indicatif `[[Titre|@uuid]]` mentionné dans `NOTEOR_SYNC_ARCHITECTURE.md §6.2`.

### 2.5 Import/Export JSON incomplet et incompatible avec la synchronisation
- **[Fait vérifié]** `exportAllNotes()` / `exportSelectedNotes()` (`web/src/db/index.ts:247-336`) n'exportent ni `categories`, ni `folders`, ni `category_id`, ni `folder_id`, ni les données binaires des pièces jointes (`filepath` recopie uniquement `a.filename`).
- **[Fait vérifié]** `importNotes()` (`web/src/db/index.ts:345-363`) déduplique uniquement sur `note.id` numérique local (`existingIds.has(noteData.id)`), ne génère pas de `sync_id` et ignore les liens/catégories/dossiers. Ce mécanisme entrerait en collision inter-appareils si utilisé avec des données synchronisées.

### 2.6 Audit Desktop via `CONTEXT.md` (état partiel non vérifiable dans le code)
- **[Fait vérifié dans `CONTEXT.md:34-38`, non vérifiable dans `.py`]** `CONTEXT.md` indique des cascades SQL locales (`notes ← categories ON DELETE SET NULL`, `notes ← attachments ON DELETE CASCADE`). `NOTEOR_SYNC_ARCHITECTURE.md §5.5` et `§10.7` exigent que les effets de suppression (dossiers, catégories, notes) ne dépendent pas uniquement des cascades SQL locales mais produisent des opérations explicites et identiques sur les deux clients.
- **[Fait vérifié dans `CONTEXT.md:4-38` vs `README.md:231`]** `CONTEXT.md` (commit `8506f89`) ne mentionne ni la table `folders`, ni les Wikilinks, ni les vues Graphe/Calendrier, ni le support d'import/export JSON sur Desktop (seulement import `.txt`/`.md` et export PDF). Si Desktop n'implémente pas `folders` ou les Wikilinks au même niveau que la PWA, des données créées sur la PWA pourraient être ignorées ou corrompues côté Desktop si le comportement n'est pas spécifié.

---

## 3. Les éléments absents du code

Tous les composants suivants décrits dans les trois spécifications sont **totalement absents** de `web/` (et aucun code serveur ou sync module n'existe dans le dépôt) :

1. **Identités et état de synchronisation (`web/src/db/index.ts`, `web/src/types/index.ts`)** :
   - Aucune table ou colonne pour `sync_id` / `entity_id`, `revision_id`, `parent_revision_ids`, `vault_id`, `device_id`, `device_seq`, `last_server_cursor`, `key_epoch`.
   - Aucun journal local (`sync_operations` / outbox) ni file d'opérations entrantes en attente de dépendances (`§12.3` de `PROTOCOL.md`).
   - Aucun stockage d'historique de révisions ni de branches concurrentes (`conflicts`).
2. **Couche cryptographique (`web/package.json`, `web/src/`)** :
   - Aucune dépendance cryptographique (`libsodium-wrappers`, `@noble/*`, `rfc8785` / `canonicalize`, `uuid`) dans `web/package.json:12-20`.
   - Aucune implémentation de `VSK`, `K_OP`, `K_MEDIA_EPOCH`, `K_MEDIA`, `KEK` (Argon2id), `HKDF-SHA-256`, `XChaCha20-Poly1305-IETF`, `crypto_secretstream`, ni canonisation RFC 8785.
   - Aucun stockage protégé de clés (`CryptoKey` Web Crypto, enveloppe VSK chiffrée, verrouillage de session / timeout d'inactivité).
3. **Moteur de synchronisation et adaptateurs (`sync/`, `web/src/`)** :
   - Aucun moteur push/pull, pagination par `server_seq`, gestion d'idempotence ou reprise après interruption.
   - Aucune gestion d'upload/download média par chunks ni vérification `encrypted_sha256`.
   - Aucun protocole de jumelage (QR code / approbation / confirmation) ni révocation d'appareil.
4. **Interface utilisateur Sync (`web/src/components/`)** :
   - `Settings.tsx` ne gère que la clé OpenRouter et l'import/export JSON ; aucune UI pour le serveur, le coffre, le verrouillage, les appareils, la récupération ou la résolution de conflits.
5. **Serveur Noteor Sync (`sync-server/`)** :
   - Entièrement inexistant (aucun endpoint FastAPI, aucune table SQLite WAL serveur, aucun Dockerfile/compose).
6. **Tests automatisés et vecteurs croisés (`tests/`)** :
   - **[Fait vérifié]** Aucun test unitaire, d'intégration ou E2E n'est présent dans `web/` (`web/package.json:6-11` ne contient ni `vitest`, ni `jest`, ni `playwright`, ni script `test`). Le répertoire `tests/crypto-vectors/` n'existe pas.

---

## 4. Contradictions et incohérences entre les trois documents

### 4.1 Tableau de synthèse des contradictions normatives

| # | Sujet | `NOTEOR_SYNC_ARCHITECTURE.md` | `NOTEOR_SYNC_PROTOCOL.md` | `NOTEOR_SYNC_CRYPTO.md` | Impact |
|---|---|---|---|---|---|
| C1 | **Endpoints HTTP** | `§12.2` : `/api/v1/devices/pair`, `/devices`, `/devices/{id}`, `/sync/push`, `/sync/pull`, `/sync/status`, `/devices/ack`, `/media/uploads`, `/media/uploads/{id}/chunks/{n}`, `/media/{id}`. | `§11.3–11.7, §14.4–14.5` : `/api/v1/health`, `/vaults/{vault_id}`, `/vaults/{vault_id}/operations:batch`, `/vaults/{vault_id}/operations`, `/vaults/{vault_id}/media`, `/vaults/{vault_id}/media/{media_id}/uploads/{upload_id}`, `.../complete`. **`§11.7` exclut `devices/ack` en V1**, et **aucun endpoint de jumelage/révocation** n'est défini. | `§9.5–9.9, §10` suppose des endpoints de jumelage, approbation, confirmation, distribution de clés d'époque et révocation, sans définir leurs URL ni schémas. | **Bloquant** : impossible d'implémenter le client HTTP ou FastAPI sans contrat d'API unifié. |
| C2 | **Époque clé (`key_epoch`) absente de l'enveloppe et de l'AAD** | `§9.4` inclut `key_id` dans l'enveloppe indicative. | `§5.1` enveloppe normative stricte : `protocol_version`, `crypto_version`, `vault_id`, `device_id`, `operation_id`, `device_seq`, `nonce`, `ciphertext`. **Aucun champ `key_epoch` ni `key_id`**. `§16.3` interdit d'ajouter un champ à l'enveloppe V1. `§6.4` AAD n'inclut pas `key_epoch`. | `§4.4.1` dérive `K_OP` **par `key_epoch`**. `§4.5` affirme : *« Le format des métadonnées chiffrées doit contenir l'époque nécessaire à la sélection de la bonne clé »*. `§7.3` AAD n'inclut pas `key_epoch`. | **Bloquant (problème de la poule et de l'œuf)** : si `key_epoch` est dans le contenu chiffré, le destinataire ne sait pas quelle clé `K_OP(epoch)` dériver pour déchiffrer (et `key_epoch` n'est pas authentifié dans l'AAD). |
| C3 | **Révocation via `key_epoch` vs dérivation HKDF depuis `VSK`** | `§9.7` & `§10.4` : révocation + rotation des clés après compromission. | `§6.2` : un appareil révoqué ne peut plus télécharger de nouvelles opérations, mais conserve ce qu'il a déjà copié. | `§2 (règle 9)`, `§10.2 (6)`, `§10.4`, `§11.2` : *« Un appareil révoqué ne reçoit plus les nouvelles clés d'époque »*, *« les appareils encore autorisés reçoivent la nouvelle clé d'époque par un mécanisme E2E »*. **MAIS `§4.4.1–4.4.2` dérive `K_OP(epoch)` et `K_MEDIA_EPOCH(epoch)` de manière 100 % déterministe à partir de `VSK` + `vault_id` via HKDF !** | **Faille logique majeure** : tout appareil révoqué possède déjà le `VSK` dans sa copie locale. Il peut donc calculer `K_OP(2)`, `K_OP(3)`… tout seul sans aucune distribution. De plus, `§11.3` interdit de tourner le `VSK` à chaque révocation, et le mécanisme `DEVICE-WRAP` / `KEY-ENVELOPE` cité en `§4.4` n'est spécifié nulle part (aucune paire de clés asymétriques d'appareil n'est définie). |
| C4 | **Chiffrement des médias : `media_nonce` vs `secretstream`** | `§5.7` utilise `encrypted_object_id`, `encrypted_hash`, `encryption_version`, `media_type`, `filename`. | `§14.2` (`media.register`) utilise `media_id`, `encrypted_sha256`, `file_name` et inclut **`"media_nonce": "BASE64URL_NONCE"`** (suggérant un AEAD simple à nonce unique), tout en renvoyant à `CRYPTO.md` (`§14.2` ligne 864). | `§8.2–8.4` impose **`crypto_secretstream_xchacha20poly1305`**, où il n'y a pas de `media_nonce` séparé mais un `secretstream_header` de 24 octets encapsulé dans un format binaire (`Magic + media_crypto_version + media_id + key_epoch + secretstream_header + chunks`). | **Bloquant** : contradiction directe entre métadonnée JSON `media_nonce` et en-tête binaire `secretstream_header`, et format binaire média explicitement non figé (`§8.4` ligne 769). |
| C5 | **Taille de bloc média (4 Mio en clair + tag > 4 Mio HTTP)** | `§11.2–11.3` : découpage éventuel en blocs, upload reprenable. | `§14.4` : `chunk_size: 4194304` (4 Mio) et *« La taille maximale d'un fragment HTTP `PUT` est de 4 Mio »*. | `§8.5` : `chunk_size = 4 MiB` pour les **données en clair**, chaque fragment chiffré portant un surcoût d'authentification (`crypto_secretstream_xchacha20poly1305_ABYTES = 17 octets`, soit **4 194 321 octets > 4 Mio**, sans compter l'en-tête binaire du chunk 0). | **Incompatibilité arithmétique** : un chunk chiffré secretstream issu de 4 Mio de clair dépasse systématiquement la limite HTTP `PUT` de 4 Mio si les deux découpages sont couplés, ou alors le transport HTTP découpe aveuglément des tranches d'octets du blob binaire déjà chiffré (à préciser explicitement). |
| C6 | **Nommage des champs et modèle de révisions** | `§5.2–5.7` : `sync_id`, `entity_sync_id`, `revision` (entier `1 -> 2` en `§7.3`), `parent_revision`, `deleted`, `media_type`, `filename`, `encrypted_object_id`, `encrypted_hash`. | `§3.1, §7.1, §8.1, §14.2` : `entity_id`, `revision_id` (**UUID v4**), `parent_revision_ids` (**tableau d'UUID v4**), `file_name`, `media_id`, `encrypted_sha256`. | `§7.4` reprend `PROTOCOL.md` (`entity_id`, `revision_id`, `parent_revision_ids`), mais `§3.3` liste `key_envelope_version` tandis que `§5.6` utilise `envelope_version` et `§9.4` utilise `pairing_version`. | **Ambiguïté de schéma** : divergence systématique entre `ARCHITECTURE.md` et `PROTOCOL.md`, plus incohérence interne des noms de version dans `CRYPTO.md`. |
| C7 | **Vecteur HKDF-SHA-256 RFC 5869 A.1 corrompu** | `§17.2` exige des vecteurs communs. | `§20.1` exige des vecteurs communs. | `§13.4` (`NOTEOR_SYNC_CRYPTO_5c70f66e.md:1265-1273`) : le JSON de test HKDF est **tronqué et corrompu** — `ikm_hex` contient `154 198` caractères (`77 099` octets `0x0b`) au lieu de 22 octets, le JSON n'est pas fermé, et `salt_hex`, `info_hex`, `L` et `okm_hex` sont absents. | **Bloquant pour Phase C** : le seul vecteur concret HKDF du document est inutilisable en l'état. |
| C8 | **Suppression Corbeille vs Suppression définitive** | `§8.3` définit 3 étapes : Corbeille (réversible) → Suppression définitive (tombstone synchronisé) → Purge physique après rétention. | `§7.2, §7.5, §7.6` ne définit qu'une paire `entity.delete` (`{ deleted_at }`) / `entity.restore` (ce qui correspond à la **Corbeille** dans `web/src/db/index.ts:92-98`, pas à la suppression définitive !). | Ne traite pas la sémantique de suppression. | **Ambiguïté critique** : impossible de distinguer « mettre à la corbeille » (restaurable sur l'autre appareil) de « vider la corbeille / supprimer définitivement ». |
| C9 | **Encodage Base64URL (`=` padding)** | `§9.4` écrit `"base64"`. | `§5.2` impose strictement **Base64 URL-safe sans remplissage (`=`)**. | `§5.6` et `§9.4` écrivent `BASE64URL_*` sans préciser si le remplissage `=` est interdit. | Risque de divergence mineure sur les enveloppes VSK / QR code si un client tolère `=` et l'autre le rejette. |
| C10 | **Paramètre `p=1` Argon2id vs RFC 9106** | `§9.2` cite Argon2id sans paramètres. | `§22.1` renvoie à `CRYPTO.md`. | `§5.2` fixe `memory=65536 KiB`, `t=3`, `p=1` en précisant explicitement que ce n'est pas le profil contraint RFC 9106 (`p=4`) à cause de `libsodium`. | Acceptable si testé et figé, mais incompatible avec toute lib/outil utilisant les défauts RFC 9106 (`p=4`). |
| C11 | **Règle de liaison cryptographique du jumelage** | `§9.6` : *« Le nouvel appareil génère ses propres clés. Les appareils établissent un échange authentifié »*. | `§1.2` déclare le jumelage hors périmètre. | `§9.5` B génère un « nonce aléatoire de session » et « les éléments nécessaires à son identité locale » (non définis) ; `§9.7` dérive `K_PAIR = HKDF(pairing_secret, salt=pairing_nonce, info="NOTEOR/SYNC/V1/PAIRING")` **sans inclure** `vault_id`, `pairing_id`, `device_a_id`, `device_b_id` ni le nonce de session B, tout en écrivant que le contexte doit inclure ces éléments. | **Spécification incomplète** : aucun AAD de jumelage, aucune forme pour la « confirmation cryptographique » de B (`§9.9`), ni mécanisme d'émission du `DEVICE_ACCESS_TOKEN`. |

---

## 5. Décisions techniques restant à prendre (bloquantes avant implémentation)

Au-delà des listes déjà ouvertes dans `NOTEOR_SYNC_ARCHITECTURE.md §19.2` et `NOTEOR_SYNC_PROTOCOL.md §22`, l'audit identifie les décisions concrètes suivantes à trancher formellement :

1. **Modèle cryptographique des clés et de la révocation (`VSK` vs `epoch_key`)** :
   - Décider si V1 assume qu'un appareil révoqué connaissant déjà le `VSK` n'est bloqué qu'au niveau du serveur (et que toute vraie révocation cryptographique exige une rotation complète du `VSK` + ré-chiffrement), **OU** remplacer le `VSK` déterministe par une **clé d'époque aléatoire indépendante** distribuée via des clés publiques d'appareil (X25519 / `crypto_box_seal` ou `crypto_box`), auquel cas `K_OP(epoch)` n'est **pas** dérivée du `VSK` mais d'une `EpochRootKey(epoch)` aléatoire enveloppée par appareil (`DEVICE-WRAP`).
2. **Placement et authentification de `key_epoch` dans l'enveloppe d'opération** :
   - Ajouter `key_epoch` dans l'enveloppe réseau (`PROTOCOL.md §5.1`) et dans l'AAD canonique (`PROTOCOL.md §6.4` / `CRYPTO.md §7.3`) avant de figer la V1, ou définir une table d'en-tête binaire/versionnée. Sans cela, un client ne peut pas savoir quelle clé utiliser lors d'une rotation.
3. **Format définitif des données métier (`NOTEOR_SYNC_DATA_MODEL.md`)** :
   - Harmoniser les champs manquants dans la PWA (`Category.created_at/updated_at`, `Folder.updated_at`, `Tag.created_at/updated_at`, `Attachment.mime_type/size/thumbnail`) et dans Desktop (`database.py` à auditer).
   - Décider comment synchroniser les miniatures d'images : dans la PWA (`NoteEditor.tsx:648`), `thumbnail` est une Data URL JPEG (~quelques Ko) stockée dans `attachments.thumbnail`, alors que Desktop la stocke en fichier dans `media/thumbnails/`.
   - Décider si les relations `note_tags` sont des entités autonomes (`entity_type: "relation"` ou `"note_tag"`) ou embarquées dans le snapshot de `note` (`tag_ids` / `tag_sync_ids`). Note : `NoteEditor.tsx` et `BulkActionBar.tsx` modifient les tags indépendamment de la sauvegarde du texte de la note (`addTagToNote`, `removeTagFromNote`, `tagNotesBulk`).
4. **Sémantique des deux niveaux de suppression et des cascades** :
   - Distinguer explicitement dans `PROTOCOL.md §7.2` : mise à la corbeille (`is_deleted = true`, via `entity.upsert` ou `entity.trash`) vs suppression définitive (tombstone `entity.delete`) vs purge physique locale/serveur.
   - Définir le comportement exact des suppressions en cascade : quand `deleteCategory` ou `deleteFolder` passe `category_id` / `folder_id` à `undefined` sur N notes locales (`web/src/db/index.ts:155,171`), faut-il générer N opérations `entity.upsert` sur les notes ou considérer la référence vers un dossier/catégorie en tombstone comme `null` côté applicatif ?
5. **Format définitif des Wikilinks** :
   - Figer la syntaxe exacte (ex. `[[Titre|@<uuid>]]` vs `[[Titre|<uuid>]]`), la stratégie d'extraction/parsing, et la gestion des conflits lors de `updateWikilinksOnRename` (renommer une note C modifie actuellement silencieusement le `content` des notes A et B qui la citent — chaque note modifiée doit générer une révision et une opération `entity.upsert`, ce qui peut créer des conflits de contenu si A a aussi été éditée hors ligne sur l'autre appareil !).
6. **Spécification binaire définitive des médias (`CRYPTO.md §8.4` & `PROTOCOL.md §14`)** :
   - Trancher entre `secretstream` et AEAD par chunks ; définir la taille exacte du `Magic`, l'endianness de `media_crypto_version` et `key_epoch`, la représentation binaire (16 octets) de `media_id`, les `ad` passés à `secretstream_init_push` / `push`, et séparer clairement le découpage cryptographique (`secretstream` plaintext chunk) du découpage de transport HTTP `PUT` (`Content-Range` sur le fichier chiffré final ≤ 4 Mio).
   - Décider si la déduplication (`ARCHITECTURE.md §11.3`) est abandonnée en V1 (car `K_MEDIA` dépend du `media_id` unique par fichier, deux blobs chiffrés d'un même média ont des `encrypted_sha256` différents).
7. **Contrat complet du jumelage, de la récupération et des jetons serveur** :
   - Spécifier les endpoints HTTP (`/pairing/*`, `/devices/*`), le format de l'AAD et du paquet `K_PAIR`, l'authentification initiale de l'appareil B auprès du serveur avant approbation, la génération et rotation de `DEVICE_ACCESS_TOKEN`, et le format humain du secret de récupération 256 bits (`CRYPTO.md §12.3`).
8. **Règles concrètes de fusion et résolution de conflits** :
   - Définir pour chaque entité (`note`, `category`, `folder`, `tag`, `relation`, `attachment`) ce qui est auto-fusionnable ou bloquant (ex. modification simultanée du texte vs suppression/corbeille d'un côté et édition de l'autre ; création simultanée d'un même tag `"travail"` avec deux `sync_id` différents sur Desktop et PWA).
9. **Stratégie de stockage sécurisé des clés dans le navigateur (PWA) et authentification CORS/origine** :
   - Décider comment protéger le `VSK` en IndexedDB sur iOS Safari / Android Chrome / Desktop (Web Crypto wrapping key vs enveloppe KEK mot de passe à chaque session) et comment la PWA déployée sur Netlify communique avec le serveur personnel derrière WireGuard/Tailscale (CORS, certificat TLS valide de confiance sur iOS/Android sans alerte auto-signée bloquante pour `fetch`/Service Worker).

---

## 6. Risques de sécurité

1. **Contradiction cryptographique majeure sur la révocation (`VSK` → `K_OP`) [Critique]** :
   - Comme démontré en §4.1 (C3), tant que `K_OP(epoch)` et `K_MEDIA_EPOCH(epoch)` sont dérivés via HKDF à partir du `VSK`, un appareil révoqué possédant le `VSK` peut dériver toutes les clés d'époques futures sans jamais recevoir d'enveloppe de clé. Si un attaquant intercepte ou obtient une sauvegarde serveur post-révocation, il déchiffre les nouvelles notes.
2. **Absence d'authentification de `key_epoch` dans l'AAD [Élevé]** :
   - `key_epoch` est absent de l'AAD (`PROTOCOL.md §6.4`, `CRYPTO.md §7.3`). Un serveur compromis pourrait rejouer ou substituer des métadonnées liées à une autre époque si l'époque n'est pas liée cryptographiquement à l'enveloppe AEAD.
3. **Risque XSS / vol de secrets dans la PWA [Élevé]** :
   - `web/netlify.toml` ne définit **aucune Content Security Policy (CSP)**, alors que `NOTEOR_SYNC_CRYPTO.md §6.3` l'exige explicitement.
   - `web/src/services/openrouter.ts:84-102` stocke actuellement la clé API OpenRouter en clair dans `localStorage`. Si du JS malveillant s'exécute dans l'origine, il accède à `localStorage`, à IndexedDB, aux clés déverrouillées en mémoire et à toute `CryptoKey` non extractible (oracle de déchiffrement).
4. **QR code de jumelage en secret porteur avec `K_PAIR` non lié aux identités [Élevé]** :
   - `CRYPTO.md §9.7` dérive `K_PAIR` uniquement depuis `pairing_secret` et `pairing_nonce` (tous deux dans le QR code). Sans liaison formelle dans l'AAD (ou la dérivation) avec `vault_id`, `pairing_id`, `device_a_id`, `device_b_id` et le nonce de session B, un observateur ou serveur malveillant pourrait tenter une substitution de session si l'AAD est mal vérifiée. De plus, sans mécanisme d'authentification du serveur envers A et B, un serveur compromis peut répondre à B ou retenir/relayer la requête hors délai si l'expiration n'est pas vérifiée localement avec une tolérance d'horloge bornée.
5. **Re-datation / comportement `CalendarView` et fuite de métadonnées [Moyen]** :
   - Les identifiants `vault_id`, `device_id`, `operation_id`, `device_seq`, tailles et timestamps serveur restent visibles du serveur (`PROTOCOL.md §17.1`). Un serveur malveillant peut corréler la taille des enveloppes et le rythme d'édition/média (ex. détection de petits payloads vs longs textes, corrélation d'upload média et d'opération `media.register` immédiate).
6. **Service Worker Workbox et cache HTTP PWA [Moyen]** :
   - `web/vite.config.ts:26-35` configure `VitePWA` (`registerType: 'autoUpdate'`). Si une future configuration Workbox inclut par défaut une route générique ou `navigateFallback` mal bornée, il faudra explicitement exclure `/api/v1/*` (`navigateFallbackDenylist` et `NetworkOnly`) afin de ne jamais servir ni mettre en cache SW des réponses d'API ou des jetons/blobs chiffrés inattendus.
7. **Éviction du stockage navigateur (iOS/Safari ITP & quota IndexedDB) [Moyen]** :
   - Sur iOS/Safari, IndexedDB peut être purgé après inactivité prolongée si `navigator.storage.persist()` n'est pas accordé (et même avec, sur certains contextes non installés). Couplé à `PROTOCOL.md §10.5` (interdiction absolue de réutiliser un `device_id` après perte de base locale, sous peine de collision/rejet de `device_seq`), cela peut provoquer des ré-appairages forcés ou perte des opérations `pending` non encore synchronisées.
8. **Sécurité du transport PWA (Netlify HTTPS) ↔ Serveur domestique (VPN) [Moyen]** :
   - La PWA est servie en HTTPS public (`netlify.toml`) tandis que le serveur Sync est à domicile sur VPN privé (`ARCHITECTURE.md §13.2`). Les navigateurs modernes bloquent le contenu mixte (`http://`) et exigent pour `fetch` depuis une origine HTTPS un certificat TLS valide reconnu par l'OS/navigateur du téléphone (sinon échec silencieux ou refus complet sur PWA installée iOS/Android).

---

## 7. Risques d'incompatibilité Python / TypeScript

1. **`crypto_secretstream_xchacha20poly1305` entre PyNaCl et libsodium-wrappers [Critique]** :
   - `NOTEOR_SYNC_CRYPTO.md §3.1` et `§8.2` reconnaissent que la compatibilité exacte de `crypto_secretstream` n'a **pas encore été vérifiée**.
   - Vérification requise : dans `libsodium-wrappers` (npm), les fonctions `crypto_secretstream_xchacha20poly1305_*` ne sont complètes que dans le build **`libsodium-wrappers-sumo`** (ou versions récentes spécifiques de `libsodium-wrappers` standard qui omettaient historiquement certaines primitives « sumo » ou diffèrent sur la gestion mémoire des états `state` en WASM). Côté Python, `PyNaCl` (`nacl.bindings`) doit exposer exactement `crypto_secretstream_xchacha20poly1305_init_push/push/init_pull/pull` et les tags `TAG_MESSAGE`, `TAG_FINAL`, `TAG_REKEY`, `TAG_PUSH`.
   - Le format du header binaire (`Magic`, endianness u16/u32/u64, encodage des UUID en 16 octets big-endian vs string ASCII) n'est pas encore figé et pourrait diverger immédiatement entre `struct.pack` Python et `DataView` TypeScript.
2. **Argon2id dans le navigateur (`64 MiB`, `t=3`, `p=1`) [Élevé]** :
   - Allouer 64 MiB de mémoire linéaire WASM pour Argon2id dans `libsodium-wrappers` sur navigateur mobile (iOS Safari notamment, dont le tas WASM est limité sur certains appareils ou onglets contraints) peut provoquer un `OutOfMemory` ou un gel de l'UI si non exécuté dans un **Web Worker**.
   - `PyNaCl` (`crypto_pwhash_alg_argon2id13()`) vs `libsodium-wrappers` : vérifier la correspondance exacte de `ALG_ARGON2ID13` et que `opsLimit=3`, `memLimit=67108864` (65536 × 1024) sont acceptés sans erreur par `crypto_pwhash` dans les deux bindings.
   - Gestion des mots de passe UTF-8 (`CRYPTO.md §5.4`) : JS/TypeScript utilise des chaînes UTF-16 (`TextEncoder` encode en UTF-8, en remplaçant d'éventuels surrogates isolés par `U+FFFD`), Python 3 utilise des chaînes Unicode (`str.encode('utf-8')` lève `UnicodeEncodeError` sur surrogates isolés).
3. **Canonicalisation JSON RFC 8785 (JCS) [Élevé]** :
   - Python `json.dumps(..., sort_keys=True, separators=(',', ':'))` **n'est PAS conforme au RFC 8785** (notamment sur la représentation des nombres flottants selon ES6/Number.prototype.toString, le traitement de `-0`, et l'ordre de tri lexicographique UTF-16 des clés Unicode hors ASCII vs points de code Python).
   - Il est impératif d'utiliser des bibliothèques JCS dédiées et testées des deux côtés, même si l'AAD V1 actuelle ne contient que des entiers, chaînes ASCII et UUID.
4. **Conversion des UUID en 16 octets pour HKDF (`CRYPTO.md §4.4`) [Moyen]** :
   - La spec dit *« convertis en 16 octets selon leur représentation canonique, sans tirets »*. En Python, `uuid.UUID(s).bytes` produit l'ordre RFC 4122 big-endian (contrairement à `.bytes_le`). En TypeScript, il faut décoder explicitement les 32 caractères hexadécimaux après retrait des tirets dans le même ordre big-endian.
5. **Base64 URL-safe sans padding (`=`) [Moyen]** :
   - Python `base64.urlsafe_b64encode` ajoute des `=` par défaut (et `urlsafe_b64decode` échoue si le padding manque sans ajustement ; de plus `urlsafe_b64decode` accepte des caractères non-base64 sauf `validate=True`).
   - En TypeScript/libsodium, `sodium.to_base64(..., base64_variants.URLSAFE_NO_PADDING)` et `from_base64` doivent être vérifiés sur le rejet strict des variantes `ORIGINAL`, du padding `=` et des caractères hors alphabet `[A-Za-z0-9_-]`.
6. **Precision des entiers 64 bits (`device_seq`, `server_seq`) et types Date [Faible/Moyen]** :
   - `PROTOCOL.md §3.2` résout correctement `MAX_SAFE_INTEGER` en imposant des chaînes décimales, mais les deux clients doivent valider strictement `/^[1-9][0-9]*$/` (ou `"0"` uniquement pour le curseur initial) et utiliser `BigInt` en TS / `int` en Python pour toute comparaison arithmétique (éviter le tri lexicographique `"10" < "2"` !).
   - Format de date UTC : JS `new Date().toISOString()` produit toujours `.SSSZ` (3 décimales, ex. `2026-11-19T12:41:00.000Z`). En Python, `datetime.now(timezone.utc).isoformat()` produit `+00:00` et 6 décimales (microsecondes) sauf formatage explicite `timespec='milliseconds'` + remplacement `+00:00` → `Z`.
7. **Unicité insensible à la casse des tags (`NOTEOR_SYNC_ARCHITECTURE.md §5.6`) [Moyen]** :
   - En cas de création hors ligne d'un tag `"Idée"` (ou `"idée"`) des deux côtés avant sync, Python `str.lower()` / SQLite `COLLATE NOCASE` (ASCII-only en SQLite par défaut !) diffère de JavaScript `.toLowerCase()` et de Dexie `equalsIgnoreCase` sur les caractères accentués Unicode (ex. `É` → `é`). Deux tags peuvent donc être fusionnés côté PWA et dupliqués côté SQLite si une règle stricte de normalisation (ex. Unicode case-folding / NFC + `.casefold()`) n'est pas définie.

---

## 8. Dépendances et bibliothèques à valider

Aucune bibliothèque sync ou crypto n'est actuellement installée dans `web/package.json` (et `requirements.txt`/`pyproject.toml` sont absents). Avant toute implémentation, un prototype d'interopérabilité doit valider les candidats suivants :

| Environnement | Rôle | Bibliothèque candidate | Points critiques à valider par prototype |
|---|---|---|---|
| **PWA (TypeScript / Navigateur)** | Primitives sodium (`XChaCha20-Poly1305-IETF`, `Argon2id`, `secretstream`, `base64url`) | `libsodium-wrappers` vs `libsodium-wrappers-sumo` | Vérifier si `crypto_secretstream_xchacha20poly1305_*` et `crypto_pwhash` (Argon2id13 avec `64 MiB`, `t=3`, `p=1`) sont exposés et fonctionnels dans le bundle Vite (ESM + WASM) et dans un Web Worker, sans blocage CSP (`wasm-unsafe-eval` éventuel si requis) et sans OOM sur iOS Safari. |
| **PWA (TypeScript)** | HKDF-SHA-256 & SHA-256 | Web Crypto API (`crypto.subtle.deriveBits('HKDF', ...)` / `digest('SHA-256', ...)`) ou `@noble/hashes` (`hkdf`, `sha256`) | `libsodium` n'expose pas HKDF-SHA-256 standard RFC 5869 (`crypto_kdf` de libsodium utilise BLAKE2b, **incompatible**). Web Crypto exige un contexte sécurisé (`https://` ou `localhost`) ; `@noble/hashes` est pur TS, auditable et synchrone/streamable pour SHA-256 sur gros blobs. |
| **PWA (TypeScript)** | RFC 8785 JCS | `canonicalize` | Conformité stricte RFC 8785, bundle size, support ESM/TS. |
| **PWA (TypeScript)** | UUID v4 | `crypto.randomUUID()` (natif) ou `uuid` | Disponibilité en Web Worker et validation de format strict UUID v4 minuscule. |
| **Desktop / Serveur (Python)** | Primitives sodium | `PyNaCl` (`nacl.bindings`) | Vérifier l'exposition réelle des bindings `crypto_secretstream_xchacha20poly1305_*`, `crypto_aead_xchacha20poly1305_ietf_*` et `crypto_pwhash` (`argon2id13`, `opslimit=3`, `memlimit=67108864`) sur la version cible de PyNaCl / libsodium système, et compatibilité packaging `.deb` (`packaging/`). |
| **Desktop (Python)** | HKDF-SHA-256 | `cryptography` (`HKDF(hashes.SHA256(), ...)`) | Validé contre RFC 5869 A.1 et contre Web Crypto / `@noble/hashes`. |
| **Desktop (Python)** | RFC 8785 JCS | `rfc8785` ou `jcs` | Produit exactement les mêmes octets UTF-8 que `canonicalize` JS sur tous les vecteurs (Unicode, nombres, ordre de clés). |
| **Serveur (Python)** | API & HTTP | `fastapi`, `uvicorn`, `pydantic`, `httpx` (client Desktop) | Aucun `pyproject.toml` existant ; compatibilité Python 3.13 (`CONTEXT.md:8`), SQLite WAL et streaming binaire `PUT` / `GET` sans charger les médias en RAM. |

---

## 9. Recommandations de correction

### 9.1 Corrections documentaires impératives (avant tout code)
1. **Résoudre le modèle de révocation/rotation des clés (`NOTEOR_SYNC_CRYPTO.md`)** :
   - **Option recommandée** : dissocier `EpochRootKey(epoch)` aléatoire de `VSK` si l'on veut réellement empêcher un appareil révoqué de lire les époques futures ; spécifier explicitement les clés asymétriques par appareil (ex. paire X25519 générée au jumelage) et le format des enveloppes `DEVICE-WRAP` / `KEY-ENVELOPE` mentionnés en `§4.4`. Sinon, retirer les affirmations selon lesquelles une rotation d'époque empêche un appareil révoqué possédant le `VSK` de déchiffrer sans rotation du `VSK`.
2. **Ajouter `key_epoch` dans l'enveloppe réseau et dans l'AAD (`NOTEOR_SYNC_PROTOCOL.md §5.1, §6.4` & `NOTEOR_SYNC_CRYPTO.md §7.3`)** :
   - Inclure `"key_epoch": 1` (entier positif) dans l'enveloppe V1 et dans l'objet JSON canonique AAD avant de figer la V1.
3. **Unifier les spécifications API HTTP et jumelage dans `NOTEOR_SYNC_PROTOCOL.md`** :
   - Aligner `NOTEOR_SYNC_ARCHITECTURE.md §12.2` sur les routes normatives `/api/v1/vaults/{vault_id}/...` de `NOTEOR_SYNC_PROTOCOL.md §11` (et supprimer la référence à `/devices/ack` en V1).
   - Ajouter dans `PROTOCOL.md` (ou un document dédié) les endpoints complets et schémas JSON du jumelage, de la liste des appareils, de la révocation et de l'obtention du `DEVICE_ACCESS_TOKEN`, en liant `K_PAIR` et l'AAD du paquet de jumelage à `vault_id`, `pairing_id`, `device_a_id`, `device_b_id` et au nonce de session de B.
4. **Corriger le vecteur HKDF RFC 5869 A.1 (`NOTEOR_SYNC_CRYPTO.md §13.4`) et ajouter les vecteurs complets** :
   - Remplacer la ligne corrompue de `ikm_hex` (77 099 octets) par le vrai vecteur RFC 5869 A.1 (`ikm_hex` = `0b` × 22, `salt_hex` = `000102030405060708090a0b0c`, `info_hex` = `f0f1f2f3f4f5f6f7f8f9`, `L` = 42, `okm_hex` = `3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865`) et générer les vecteurs attendus pour Argon2id, AEAD XChaCha20-Poly1305 + AAD RFC 8785, `secretstream` et format binaire média.
5. **Figer le format binaire média et découpler chunking crypto / HTTP (`NOTEOR_SYNC_CRYPTO.md §8.4–8.5` & `NOTEOR_SYNC_PROTOCOL.md §14.2, §14.4`)** :
   - Supprimer `media_nonce` de `media.register` s'il est remplacé par `secretstream_header` dans le blob binaire.
   - Définir le layout exact en octets du header binaire média (`Magic` fixe, versions, UUID 16 octets big-endian, `key_epoch`, `secretstream_header` 24 octets, AAD du flux).
   - Préciser que l'upload `PUT` découpe le **blob chiffré complet** par tranches HTTP ≤ 4 Mio (`chunk_size = 4194304`), indépendamment de la taille de bloc en clair utilisée par `secretstream`.
6. **Créer `NOTEOR_SYNC_DATA_MODEL.md`** (référencé en `PROTOCOL.md §1.1`) :
   - Figer les champs exacts de `note`, `category`, `folder`, `tag`, `relation`/`note_tag`, `attachment` (y compris la représentation des miniatures).
   - Séparer `entity.trash` / `is_deleted` (Corbeille) de `entity.delete` (suppression définitive / tombstone), définir les règles de cascade (ex. suppression de catégorie/dossier/note) et le format final des Wikilinks UUID + comportement de `updateWikilinksOnRename`.
   - Standardiser `BASE64URL` sans `=` partout (`PROTOCOL.md §5.2`, `CRYPTO.md §5.6, §9.4`).

### 9.2 Corrections préparatoires recommandées dans la PWA (`web/`) (hors Mission 0)
1. Corriger le bug de suppression dans `NoteEditor.tsx:585-589` pour appeler `deleteNote(noteIdRef.current)` au lieu de `saveNote(...)`.
2. Centraliser **toutes** les mutations DB dans `web/src/db/index.ts` (supprimer l'appel direct `db.notes.update` dans `CalendarView.tsx:129` et dissocier idéalement la date de calendrier/tri métier si souhaité, ou traiter la re-datation comme une mutation explicite) et encapsuler chaque mutation multi-opérations dans `db.transaction('rw', ...)`.
3. Faire passer `now()` (`web/src/db/index.ts:27-29`) en RFC 3339 UTC complet (`new Date().toISOString()`) avec migration des dates existantes lors de la montée de version Dexie (`version(2)`).
4. Ajouter une politique CSP stricte dans `web/netlify.toml` (compatible WASM libsodium le moment venu) et configurer Workbox dans `web/vite.config.ts` pour exclure `/api/v1/*` du précaching/fallback SPA.
5. Mettre en place un framework de tests dans `web/` (`vitest`) dès le début de la phase suivante.

---

## 10. Proposition d'ordre d'implémentation

L'ordre proposé tient compte de l'absence actuelle du code Desktop et des contradictions détectées :

1. **Étape 0 — Récupération & Audit du code Desktop réel (Prérequis absolu)**  
   Ré-importer ou accéder au dépôt complet `nouhailler/Noteor` contenant `database.py`, `config.py`, `ui/`, `core/`, `requirements.txt` et auditer exhaustivement le schéma SQLite, les cascades, `folders`, les Wikilinks et les dates côté Python.
2. **Étape 1 — Révision normative des spécifications & `NOTEOR_SYNC_DATA_MODEL.md`**  
   Corriger les contradictions C1–C11 dans les trois documents (`key_epoch` dans l'enveloppe/AAD, modèle de révocation/distribution des clés, endpoints HTTP unifiés, format binaire média, Corbeille vs tombstone, vecteur HKDF A.1) et figer `NOTEOR_SYNC_DATA_MODEL.md` (schémas, relations, Wikilinks UUID, règles de conflits).
3. **Étape 2 — Prototype cryptographique croisé Python / TypeScript + Vecteurs**  
   Valider les dépendances (`PyNaCl`, `libsodium-wrappers[-sumo]`, `cryptography` / `@noble/hashes`, `rfc8785` / `canonicalize`), exécuter les tests croisés (Argon2id, HKDF-SHA-256, XChaCha20-Poly1305 + AAD RFC 8785, `secretstream` par chunks, enveloppe VSK, jumelage) et figer `tests/crypto-vectors/`.
4. **Étape 3 — Assainissement et migrations locales (Desktop & PWA)**  
   Corriger `NoteEditor.tsx:585-589`, centraliser et transactionnaliser toutes les mutations SQLite et Dexie (`db.transaction`), migrer les schémas locaux (UUID `sync_id`, dates RFC 3339 UTC, Wikilinks UUID avec rétrocompatibilité numérique) et ajouter les tests unitaires de migration/non-régression.
5. **Étape 4 — Journaux locaux, révisions et résolution de conflits (hors réseau)**  
   Implémenter les tables locales `sync_operations`, `sync_state` (`device_id`, `device_seq`, `last_server_cursor`), la file de dépendances manquantes et le graphe de révisions (`revision_id`, `parent_revision_ids`) dans les deux clients, testables via un transport simulé.
6. **Étape 5 — Serveur Noteor Sync (`sync-server/`)**  
   Implémenter FastAPI + SQLite WAL : auth par jeton appareil, `vaults`, `devices`, jumelage, révocation, `operations:batch` (atomique, idempotence, vérification séquences), `operations` paginé, upload/download média par fragments avec vérification `encrypted_sha256`, sauvegardes et conteneurisation Docker/Compose + TLS/VPN.
7. **Étape 6 — Intégration E2E clients ↔ serveur, sécurité navigateur et UI Sync**  
   Connecter les clients au serveur, implémenter le stockage sécurisé des clés (Keyring Linux / Web Crypto + verrouillage PWA + Worker Argon2id + CSP stricte dans `netlify.toml`), ajouter les écrans UI (jumelage QR, gestion appareils, récupération, conflits, progression médias) et exécuter la suite de tests E2E inter-clients en CI.
