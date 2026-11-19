# NOTEOR DESKTOP — AUDIT TECHNIQUE ET CONTRAT D'INTÉGRATION (PHASE 1)

**Date :** 19 novembre 2026  
**Statut :** Audit partiel — sources Python absentes de l'espace de travail  

---

## 1. Constat d'accès aux sources Desktop

- **[Fait vérifié]** Aucun fichier Python Desktop (`main.py`, `config.py`, `database.py`, `core/*`, `ui/*`, `packaging/*`, `requirements.txt`, `pyproject.toml`) n'est présent sur disque dans l'espace de travail.
- **[Fait vérifié]** Aucun dépôt `.git` local n'existe, et la requête de vérification vers `https://api.github.com/repos/nouhailler/Noteor` a retourné `HTTP 404` (dépôt privé ou inaccessible sans authentification).
- **Conséquence :** l'audit direct des requêtes SQL et des méthodes de `database.py` ne peut pas être effectué dans cet environnement. Conformément aux règles du projet, aucune fonction interne inexistante n'est supposée ni inventée.

---

## 2. Inventaire vérifié depuis `CONTEXT.md` et `README.md`

| Élément | Preuve documentaire | Incidence pour Noteor Sync |
|---|---|---|
| Stockage SQLite local | `CONTEXT.md:8` (`~/.local/share/Noteor/notes.db`), `README.md:295` (mode WAL). | L'adaptateur sync doit activer/préserver `PRAGMA journal_mode=WAL` et exécuter les écritures métier + `sync_operations` dans une transaction `BEGIN IMMEDIATE` unique. |
| Tables documentées | `CONTEXT.md:34` : `notes`, `categories`, `tags`, `note_tags`, `attachments`. | **Point à vérifier à la réintégration :** `README.md:231` cite « dossiers hiérarchiques » côté Desktop, mais `CONTEXT.md` (commit `8506f89`) ne liste pas de table `folders`. L'adaptateur Desktop doit vérifier/créer la table `folders` et la colonne `notes.folder_id` si absentes. |
| Contraintes FK SQLite | `CONTEXT.md:35-36` : `notes ← categories (ON DELETE SET NULL)`, `notes ← attachments (ON DELETE CASCADE)`. | Une suppression SQL en cascade ne doit pas contourner le journal de synchronisation : l'adaptateur doit émettre les opérations explicites avant/avec la mise à jour locale. |
| Types de pièces jointes | `CONTEXT.md:37,61-66` : `'image'`, `'audio'`, `'video'` stockés sur disque sous `media/audio/`, `media/images/`, `media/thumbnails/`, `media/video/`. | L'adaptateur Desktop reconstruit des fichiers locaux à la réception d'un média déchiffré et chiffre les fichiers par flux (`secretstream`) à l'envoi sans charger tout le fichier en RAM. |
| Format de date UI | `CONTEXT.md:43` mentionne `ROLE_DATE` au format `"YYYY-MM-DD HH:MM"`. | Le stockage protocole impose UTC RFC 3339 (`YYYY-MM-DDTHH:mm:ss.sssZ`). L'affichage UI Desktop continue de formatter pour l'utilisateur sans utiliser `updated_at` comme curseur sync. |
| Dépendances Python | `CONTEXT.md:159`, `README.md:292-301` : Python 3.10+/3.13, PyQt6, numpy, pillow, markdown, sounddevice. | Ajouter `PyNaCl`, `cryptography`, `rfc8785`, `httpx` pour le moteur de synchronisation hors thread Qt. |

---

## 3. Module préparé côté Desktop (`sync/desktop/`)

En attendant la restauration des fichiers UI/métier Desktop depuis le dépôt complet de l'utilisateur, le module autonome et testable suivant est implémenté dans `sync/desktop/` :

1. `sync/desktop/crypto.py` : couche cryptographique conforme à `NOTEOR_SYNC_CRYPTO.md` v1.1 (`PyNaCl` / `libsodium.so.23`, HKDF-SHA-256, Argon2id13, XChaCha20-Poly1305-IETF + AAD RFC 8785, `crypto_secretstream`, X25519 `crypto_box_seal`).
2. `sync/desktop/sqlite_sync_adapter.py` : schéma SQLite complet + migration idempotente (`sync_id`, `revision_id`, `parent_revision_ids`, `folders`, `sync_operations`, `sync_state`, `sync_revisions`, `sync_conflicts`, `sync_pending_ops`) + opérations métier transactionnelles (`BEGIN IMMEDIATE`).
3. `sync/desktop/engine.py` : moteur de synchronisation hors réseau et client HTTP conforme à `NOTEOR_SYNC_PROTOCOL.md` v1.1.

### Points d'accroche attendus dans `database.py` lors de la réintégration
- Remplacer ou encapsuler les méthodes de mutation (`save_note`, `delete_note`, `restore_note`, `permanently_delete_note`, `add_tag_to_note`, `remove_tag_from_note`, `save_category`, `delete_category`, `save_folder`, `delete_folder`, `add_attachment`, `delete_attachment`) par les primitives transactionnelles de `SqliteSyncAdapter`.
- Exécuter `SyncEngine.sync_once()` dans un `QThread` / worker dédié afin de ne jamais bloquer la boucle événementielle PyQt6.
