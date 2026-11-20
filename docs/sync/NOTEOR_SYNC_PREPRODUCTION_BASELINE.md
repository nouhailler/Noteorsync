# NOTEOR_SYNC_PREPRODUCTION_BASELINE.md

**Date de gel de référence :** 20 novembre 2026  
**Mission :** Préproduction — Validation CI, Serveur FastAPI et Sécurité E2E de NoteorSync (`nouhailler/Noteorsync`)  
**Périmètre inspecté avant toute modification :** Ensemble de l'espace de travail `/app/applet`

---

## 1. Inventaire des composants présents dans le dépôt

| Composant | Chemin(s) | État dans le dépôt | Dépendances / Runtimes |
|---|---|---|---|
| **Documentation normative & guides** | `docs/sync/NOTEOR_SYNC_ARCHITECTURE.md`, `NOTEOR_SYNC_PROTOCOL.md`, `NOTEOR_SYNC_CRYPTO.md`, `NOTEOR_SYNC_DATA_MODEL.md`, `NOTEOR_SYNC_AUDIT_REPORT.md`, `NOTEOR_SYNC_CONFORMANCE_REPORT.md`, `NOTEOR_DESKTOP_AUDIT.md`, `docs/DEPLOYMENT.md`, `README.md`, `CONTEXT.md`, `CHANGELOG.md` | Présents | Markdown |
| **PWA Web / Mobile** | `web/src/**`, `web/public/_headers`, `web/netlify.toml`, `web/wrangler.toml`, `web/vite.config.ts`, `web/package.json`, `web/scripts/generate-icons.mjs` | Présent et compilable | `React 18`, `TypeScript 5.7.2`, `Vite 6.0.5`, `Dexie 3.2.7`, `libsodium-wrappers-sumo 0.8.4`, `@noble/hashes`, `canonicalize`, `vite-plugin-pwa` |
| **Modules Sync Desktop (couche sync)** | `sync/desktop/crypto.py`, `sync/desktop/sqlite_sync_adapter.py`, `sync/desktop/engine.py` | Présents (couche sync uniquement) | Python `>=3.11`, `sqlite3` stdlib, `libsodium.so.23` via `ctypes` |
| **Application Desktop PyQt6 complète** | *(absente de l'espace de travail)* | **Absente** (`main.py`, `database.py`, `ui/*`, `core/*` hors dépôt courant) | PyQt6 absent |
| **Serveur Sync (`sync-server/`)** | `sync-server/app/main.py`, `app/stdlib_server.py`, `app/services/sync_service.py`, `app/storage/database.py`, `pyproject.toml`, `Dockerfile`, `compose.yaml`, `Caddyfile.example`, `scripts/backup.sh` | Présent ; tests dédiés `sync-server/tests/` absents au gel | `fastapi>=0.115.0`, `uvicorn[standard]>=0.32.0`, `pydantic>=2.9.0`, dev: `pytest>=8.0`, `httpx>=0.27` |
| **Assistant Serveur `.deb`** | `sync-server-manager/manager_core.py`, UI/scripts/packaging | Présent | Python 3.11+ stdlib (+ PyQt6 optionnel) |
| **Vecteurs & Suites de tests existants** | `tests/crypto-vectors/*`, `tests/python/test_crypto_vectors.py`, `test_desktop_adapter_and_engine.py`, `test_server_and_e2e.py`, `test_sync_manager.py`, `web/src/sync/__tests__/crypto.test.ts`, `db_and_engine.test.ts`, `sync_e2e.test.ts` | Présents | `unittest` Python, `vitest` (`fake-indexeddb`) |
| **CI GitHub Actions** | `.github/workflows/` | **Absent au gel initial** | — |
| **Métadonnées Git locales** | `.git/` | **Absent** (`git status` : `fatal: not a git repository`) | `git` CLI présent mais pas d'historique `.git` local |

---

## 2. Outillages, versions et dépendances mesurés dans l'environnement

| Outil / Bibliothèque | Version / État mesuré |
|---|---|
| Python | `3.11.2` (`/usr/bin/python3`) |
| SQLite (module Python `sqlite3`) | `3.40.1` |
| `libsodium` système (`libsodium.so.23`) | `1.0.18` (`ctypes`) |
| Node.js | `v22.23.2` |
| npm | `10.9.8` |
| Bun | `1.4.4` (`bun.lock` présent à la racine, aucun `package-lock.json`) |
| Outillages Python absents | `pip`, `pip3`, `ensurepip`, `uv`, `pytest`, `ruff`, `mypy`, `black`, `fastapi`, `uvicorn`, `pydantic`, `httpx` |
| Outillages système absents | `docker`, `sqlite3` CLI |

### Mesures de performance Argon2id (baseline)
Paramètres normatifs (`memory_kib=65536`, `iterations=3`, `parallelism=1`) :
- Python (`libsodium 1.0.18` natif) : **`219.0 ms`**
- TypeScript / Node (`libsodium-wrappers-sumo` WASM) : **`439.0 ms`**

---

## 3. Résultats exacts de l'état initial (avant toute modification)

| Vérification | Commande exacte | Statut initial | Résultat observé |
|---|---|---|---|
| Compilation bytecode Python | `python3 -m py_compile sync-server/app/main.py sync-server/app/stdlib_server.py sync-server/app/services/sync_service.py sync-server/app/storage/database.py sync/desktop/crypto.py sync/desktop/engine.py sync/desktop/sqlite_sync_adapter.py sync-server-manager/manager_core.py` | **SUCCÈS** (`exit 0`) | Aucune erreur de syntaxe |
| Tests unitaires & E2E Python (`stdlib_server`) | `python3 -m unittest discover -s tests/python -v` | **SUCCÈS** | **`20` tests OK en `3.948s`** (`test_crypto_vectors`: 8, `test_desktop_adapter_and_engine`: 3, `test_server_and_e2e`: 3, `test_sync_manager`: 6) |
| Import serveur FastAPI (`app.main`) | `python3 -c "import sys; sys.path.insert(0, 'sync-server'); import app.main"` | **ÉCHEC** (`exit 1`) | `ModuleNotFoundError: No module named 'fastapi'` (`fastapi`, `uvicorn`, `pydantic`, `httpx`, `pytest` non installés dans le conteneur) |
| Vérification statique TypeScript | `npm run lint` (`tsc --noEmit` dans `web/`) | **SUCCÈS** (`exit 0`) | 0 erreur TypeScript |
| Tests Vitest PWA & interop | `npx vitest run` | **SUCCÈS** | **`3` fichiers, `12` tests passés en `3.20s`** (`crypto.test.ts`: 7, `db_and_engine.test.ts`: 4, `sync_e2e.test.ts`: 1 via `app.stdlib_server`) |
| Build production PWA | `npm run build` (`node scripts/generate-icons.mjs && tsc && vite build`) | **SUCCÈS** (`exit 0`, `8.59s`) | Génère `web/dist/` (`crypto.worker-*.js`, `sw.js`, `workbox-*.js`, `_headers`) |
| Audit dépendances `npm` | `npm audit --json` | **ÉCHEC** (`exit 1`) | `ENOLOCK` (le dépôt verrouille ses paquets via `bun.lock`, pas de `package-lock.json`) |
| Audit dépendances `bun` | `bun audit` | **ALERTES** (`exit 0` avec vulnérabilités) | **`6` vulnérabilités signalées** sur dépendances de build/dev : `sharp@0.34.5` (3 `high` : libvips, libheif, librsvg), `glob@11.1.0` (`moderate`), `minimatch@10.2.6 / 5.1.9` (`moderate`), `postcss-selector-parser@6.0.10 / 6.1.6` (`moderate`) |
| Recherche secrets évidents | `grep -rnE "BEGIN ... PRIVATE KEY\|AKIA...\|ghp_...\|xox...\|password=..." sync sync-server sync-server-manager web/src` | **SUCCÈS** (`0` correspondance) | Aucune clé privée ni secret hardcodé détecté |

---

## 4. Lacunes structurelles et blocages identifiés dès la Phase 0

1. **Absence totale de CI GitHub Actions (`.github/workflows/` inexistant)** : aucune vérification automatique PR/push n'existe pour Python, TypeScript, vecteurs croisés ou serveur FastAPI.
2. **Absence de suite de tests FastAPI / Uvicorn dédiée (`sync-server/tests/` inexistant)** : `sync-server/app/main.py` n'a jamais été testé en exécution réelle avec FastAPI/Pydantic/Uvicorn/TestClient/httpx dans ce dépôt ; seuls `SyncService` et `stdlib_server.py` ont été exercés en HTTP réel.
3. **Impossibilité locale d'exécuter FastAPI/Uvicorn et Docker** : le conteneur courant ne dispose ni de `pip`/`fastapi`/`uvicorn`/`httpx`/`pytest`, ni de `docker`, ce qui empêchera toute preuve locale directe d'exécution Uvicorn/FastAPI (à documenter explicitement comme non exécutable localement et bloquant pour une validation production complète).
4. **Absence d'historique `.git` local** et impossibilité d'exécuter GitHub Actions à distance depuis cet environnement.
5. **Surface d'audit déjà repérée pour les phases suivantes (sans modification à ce stade)** :
   - CORS par défaut permissif (`"*"` lorsque `NOTEOR_CORS_ORIGINS` est absent) ;
   - Lecture intégrale du corps HTTP en RAM sans plafond préalable dans `stdlib_server.py` et `put_media_chunk` (`main.py`) ;
   - Risque de traversée de chemin potentiel sur `file_name` / `media_id` lors du téléchargement média Desktop (`sync/desktop/engine.py`) ;
   - Absence de borne supérieure sur les paramètres `memory_kib`/`iterations` dans `derive_argon2id_kek` / `deriveArgon2idKek`, et absence de vérification de longueur 32 octets des clés publiques/privées avant appel `ctypes` C `crypto_box_seal*` en Python ;
   - Comparaison datetime naïve/aware possible sur `expires_at` dans `pairing_sessions` (`sync_service.py`) ;
   - Absence d'authentification cryptographique de l'émetteur sur `epoch_keys` (`crypto_box_seal` anonyme) et non-vérification serveur de `confirm_hmac` dans le protocole V1.1 (limites architecturales à documenter et proposer sans rupture silencieuse du protocole).
