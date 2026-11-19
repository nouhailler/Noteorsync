# Changelog — Noteor

Toutes les évolutions notables de **Noteor** sont documentées dans ce fichier.

---

## [1.1.0] — 2026-11-19

### Ajouté
- **Synchronisation chiffrée de bout en bout (E2EE) multi-appareils** :
  - Primitives cryptographiques strictement interopérables entre TypeScript (`libsodium-wrappers-sumo`) et Python (`libsodium.so.23` via `ctypes`) : dérivation **Argon2id**, hiérarchie **HKDF-SHA-256**, chiffrement authentifié **XChaCha20-Poly1305**, enveloppes asymétriques **X25519 `crypto_box_seal`** et flux médias chunkés **`NTRM1`** (`crypto_secretstream_xchacha20poly1305`).
  - Dérivation Argon2id isolée dans un Web Worker (`web/src/sync/crypto.worker.ts`) et gestion du coffre en RAM avec verrouillage automatique et mise à zéro mémoire (`web/src/sync/vault.ts`).
  - Jumelage rapide par **QR code / JSON** (HMAC + enveloppe éphémère) dans `web/src/components/SyncModal.tsx`.
  - Détection des conflits par **DAG de révisions** sans écrasement silencieux et interface de résolution (`web/src/components/ConflictResolver.tsx` : *Garder locale*, *Prendre distante*, *Garder les deux*, *Fusionner*).
  - Révocation d'appareil avec rotation vers une nouvelle époque cryptographique.
- **Modules Desktop Python (`sync/desktop/`)** :
  - `crypto.py`, `sqlite_sync_adapter.py` et `engine.py` prêts à être branchés sur l'application PyQt6 locale (`DesktopSyncDatabase`, migration automatique SQLite v1 → v2 WAL, file d'attente hors-ligne et gestion des dépendances).
- **Serveur relais aveugle (`sync-server/`)** :
  - API FastAPI (`/healthz`, `/api/v1/*`) + serveur de repli sans dépendance externe (`app/stdlib_server.py`), stockage SQLite WAL et blobs médias `NTRM1`.
  - Déploiement **Docker Compose** (`compose.yaml`, `Dockerfile`, `Caddyfile.example`) et script de sauvegarde à chaud (`scripts/backup.sh`).
- **Assistant graphique Linux `.deb` (`sync-server-manager/`)** :
  - Application packagée en `.deb` (`noteor-sync-manager_1.1.0_all.deb`) reprenant le thème Noteor (Indigo `#4F46E5`, fond `#F9FAFB`, icône carnet/crayon) avec interface native **PyQt6** (`gui_pyqt.py`) et interface **Web UI locale sans dépendance** (`gui_web.py` via `--web`).
  - Vérification automatisée des prérequis (`scripts/check_deps.sh` : Docker CLI, démon Docker, Docker Compose, `curl`, `sync-server/`, disponibilité du port).
  - Édition paramétrable de `sync-server/compose.yaml` (URL PWA **Cloudflare Pages**, `NOTEOR_CORS_ORIGINS`, `NOTEOR_DATA_DIR`, port local) avec aperçu YAML.
  - Contrôle du conteneur (`docker compose up -d --build noteor-sync`, `restart`, `stop`, `ps`), test HTTP `GET /healthz` et consultation/filtrage des logs Docker (incluant le suivi en direct `logs -f`).
- **Déploiement Cloudflare Pages & documentation** :
  - Fichiers `web/wrangler.toml` et `web/public/_headers` (en-têtes de sécurité, CSP `'wasm-unsafe-eval'`, règles de cache PWA) et exclusion Workbox `NetworkOnly` sur `/api/v1/*` dans `web/vite.config.ts`.
  - Guide pas à pas complet dans `docs/DEPLOYMENT.md` et spécifications dans `docs/sync/`.
- **Tests croisés et intégration** :
  - Vecteurs cryptographiques communs (`tests/crypto-vectors/`), suite `unittest` Python (`tests/python/` : 17 tests) et suite Vitest TypeScript incluant E2E live PWA ↔ Serveur ↔ Desktop Python (`web/src/sync/__tests__/` : 10 tests).

### Modifié
- Migration des identifiants et horodatages vers des UUID v4 (`sync_id`), dates UTC RFC 3339, Wikilinks `[[Titre|@uuid]]` et déduplication Unicode des tags dans `web/src/db/index.ts` et `sync/desktop/sqlite_sync_adapter.py`.
- Durcissement de l'éditeur Markdown (`web/src/components/NoteEditor.tsx`) et mise à jour de `README.md`, `CONTEXT.md` et `docs/DEPLOYMENT.md`.

---

## [1.0.0] — 2026-03-21

### Ajouté
- Application Noteor Desktop initiale (Python 3 / PyQt6 / SQLite) et PWA (`web/` : React 18, TypeScript, Dexie) :
  - Prise de notes Markdown avec barre de formatage, aperçu temps réel et auto-sauvegarde.
  - Catégories hiérarchiques colorées, dossiers, tags, recherche full-text, filtres par type/date, vue graphe et vue calendrier.
  - Enregistrement audio (`arecord`/`aplay` + Web Audio), import/capture d'images, import vidéo et enregistrement webcam (`ffmpeg` / `v4l2`).
  - Export/import JSON, corbeille avec restauration et transcription audio via OpenRouter.
