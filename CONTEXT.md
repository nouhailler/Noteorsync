# Noteor — Contexte du projet

## Vue d'ensemble

Écosystème multi-plateforme de prise de notes **100 % local-first avec synchronisation chiffrée de bout en bout (E2EE)** entre l'application Web/Mobile PWA, l'application Desktop PyQt6 et un serveur relais aveugle auto-hébergé.

- **Version** : `1.1.0` (mise à jour le `2026-11-19`)
- **Dépôt GitHub** : `https://github.com/nouhailler/Noteor`
- **Documentation complémentaire** : [`README.md`](README.md), [`CHANGELOG.md`](CHANGELOG.md), [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md), [`docs/sync/`](docs/sync/)

---

## Architecture actuelle du dépôt

```
Noteor/
├── web/                              # PWA Mobile & Desktop (React 18, TypeScript, Vite, Dexie)
│   ├── public/_headers               # En-têtes Cloudflare Pages (CSP 'wasm-unsafe-eval', cache PWA)
│   ├── wrangler.toml                 # Configuration déploiement Cloudflare Pages (dist)
│   ├── vite.config.ts                # Workbox PWA (règle NetworkOnly sur /api/v1/*)
│   └── src/
│       ├── components/               # NoteList, NoteEditor, GraphView, CalendarView, SyncModal, ConflictResolver…
│       ├── db/index.ts               # Schéma Dexie (IndexedDB), sync_id UUID, journaux hors-ligne
│       ├── services/openrouter.ts    # Transcription audio OpenRouter
│       └── sync/                     # crypto.ts, crypto.worker.ts (Argon2id), vault.ts, engine.ts, ids.ts
├── sync/desktop/                     # Modules Python prêts à intégrer dans Noteor Desktop (PyQt6)
│   ├── crypto.py                     # Primitives E2EE via libsodium.so.23 (ctypes, zéro PyNaCl)
│   ├── sqlite_sync_adapter.py        # Migration SQLite v1 -> v2 WAL, UUID v4, RFC 3339, Wikilinks, tags
│   └── engine.py                     # Moteur de sync, file d'attente hors-ligne, DAG & client HTTP
├── sync-server/                      # Serveur relais aveugle zéro-connaissance
│   ├── app/main.py                   # API FastAPI (/healthz, /api/v1/*) + médias NTRM1
│   ├── app/stdlib_server.py          # Serveur HTTP/1.1 de repli 100 % stdlib Python
│   ├── app/services/sync_service.py  # Stockage opérations chiffrées, séquences, époques, médias
│   ├── app/storage/database.py       # SQLite WAL serveur
│   ├── compose.yaml & Dockerfile     # Déploiement Docker Compose (+ Caddy TLS optionnel)
│   └── scripts/backup.sh             # Sauvegarde à chaud SQLite WAL + blobs
├── sync-server-manager/              # Utilitaire graphique Linux packagé en .deb (Option A Docker)
│   ├── main.py & manager_core.py     # Lanceur CLI/GUI + gestion compose.yaml, Docker, /healthz
│   ├── gui_pyqt.py & gui_web.py      # Interface PyQt6 (thème Noteor) + repli Web UI locale sans dépendance
│   ├── scripts/check_deps.sh         # Contrôle docker, démon, docker compose, curl, sync-server/, port
│   ├── packaging/                    # DEBIAN/{control,postinst}, .desktop, build_deb.sh
│   └── dist/noteor-sync-manager_1.1.0_all.deb
├── docs/
│   ├── DEPLOYMENT.md                 # Guide pas à pas (Serveur, Mobile Cloudflare Pages, Desktop)
│   └── sync/                         # Architecture, Crypto, Protocole, Modèle de données & Audits
└── tests/
    ├── crypto-vectors/               # Vecteurs croisés Python <-> TypeScript (HKDF, Argon2id, XChaCha20, NTRM1)
    └── python/                       # Suite unittest (17 tests : crypto, Desktop WAL, serveur/E2E, sync-manager)
```

---

## Fonctionnalités implémentées (v1.1.0)

### 1. Application Web / Mobile PWA (`web/`)
- Création / édition Markdown avec barre d'outils, aperçu temps réel, auto-sauvegarde et liens internes `[[Titre|@uuid]]`
- Catégories colorées, dossiers, tags normalisés avec déduplication Unicode, sélection multiple et corbeille
- Filtres de recherche (texte, type, date, catégorie, dossier, tag), vue graphe interactive (`GraphView`) et vue calendrier (`CalendarView`)
- Enregistrement vocal, capture/import d'images, export/import JSON et transcription audio via OpenRouter
- **Coffre & Sync E2EE (`SyncModal.tsx`, `ConflictResolver.tsx`)** :
  - Dérivation Argon2id déportée dans un Web Worker (`crypto.worker.ts`)
  - Clés maintenues en RAM uniquement avec verrouillage par inactivité et effacement mémoire (`vault.ts`)
  - Clé de secours 256 bits (`VSK`), jumelage inter-appareils par QR code / JSON (HMAC + enveloppe éphémère)
  - Résolution visuelle des conflits par DAG de révisions (`Garder locale`, `Prendre distante`, `Garder les deux`, `Fusionner`)
  - Révocation d'appareil avec rotation d'époque cryptographique (`X25519 crypto_box_seal`)
- **Déploiement Cloudflare Pages** : configuration prête dans `web/wrangler.toml` et `web/public/_headers` (CSP incluant `'wasm-unsafe-eval'`, exclusion Workbox `NetworkOnly` sur `/api/v1/*`)

### 2. Serveur relais & Assistant graphique `.deb` (`sync-server/` & `sync-server-manager/`)
- **Relais aveugle (`sync-server/`)** : FastAPI + SQLite WAL (ou `stdlib_server.py` sans dépendance externe), transport de médias chiffrés par blocs `NTRM1` (`crypto_secretstream_xchacha20poly1305`), Docker Compose (`noteor-sync` + `caddy` optionnel) et sauvegarde à chaud (`scripts/backup.sh`)
- **Assistant `.deb` (`noteor-sync-manager`)** :
  - Reprend le thème Indigo/Slate (`#4F46E5` / `#F9FAFB`) et l'icône Noteor (`web/public/icon.svg`)
  - Vérifie les prérequis système via `scripts/check_deps.sh` (Docker CLI, accès démon Docker, `docker compose` / `docker-compose`, `curl`, fichiers `sync-server/`, port local)
  - Met à jour de façon paramétrable `sync-server/compose.yaml` (`NOTEOR_DATA_DIR`, `NOTEOR_CORS_ORIGINS` avec fusion de l'URL Cloudflare Pages, port local)
  - Pilote le conteneur (`docker compose up -d --build noteor-sync`, `restart`, `stop`, `ps`), vérifie `/healthz` (`{"status":"ok","version":"1.1.0"}`) et affiche/filtre les logs Docker (dont suivi `logs -f`)
  - Génère le paquet Debian `sync-server-manager/dist/noteor-sync-manager_1.1.0_all.deb` via `build_deb.sh`

### 3. Modules Desktop Python (`sync/desktop/`)
- Intégrables directement dans le dépôt PyQt6 local (`Noteor` Desktop) sans compilation C (`libsodium.so.23` via `ctypes`)
- Migration SQLite v1 → v2 WAL (`sync_id` UUID v4, dates UTC RFC 3339, Wikilinks `[[Titre|@uuid]]`)

---

## Rappel — Socle historique Noteor Desktop (dépôt PyQt6 local)

> L'application PyQt6 Desktop complète réside dans le dépôt Desktop séparé ; ce dépôt embarque son module `sync/desktop/` ainsi que les rappels techniques suivants issus des sessions initiales :
> - **Base locale** : `~/.local/share/Noteor/notes.db` (tables `notes`, `categories`, `tags`, `note_tags`, `attachments` migrées en v2 WAL par `DesktopSyncDatabase`).
> - **Rôles `QListWidgetItem`** : `UserRole + 0..5` (`ROLE_NOTE_ID`, `ROLE_DATE`, `ROLE_CAT_COLOR`, `ROLE_AUDIO`, `ROLE_IMAGE`, `ROLE_VIDEO`).
> - **Audio / Capture / Webcam sous Linux** : backend primaire `arecord`/`aplay` (ALSA) avec repli `sounddevice` ; capture d'écran Wayland via portail XDG D-Bus (ou `QT_QPA_PLATFORM=xcb`) ; webcam MP4 via `ffmpeg -f v4l2` (`SIGINT`).

---

## Commandes de vérification et de build

```bash
# 1. Build et lint du frontend PWA
npm install
npm run lint
npm run build

# 2. Suite de tests TypeScript / E2E live (Vitest)
npx vitest run

# 3. Suite de tests Python (crypto, adaptateur Desktop, serveur, sync-manager)
python3 -m unittest discover -s tests/python -v

# 4. Construire le paquet .deb de l'assistant serveur
bash sync-server-manager/packaging/build_deb.sh
```
