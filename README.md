<div align="center">

# 📓 Noteor

**Application de prise de notes — 100 % locale avec synchronisation chiffrée de bout en bout (E2EE)**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://typescriptlang.org)
[![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![Cloudflare Pages](https://img.shields.io/badge/Cloudflare%20Pages-PWA-F38020?logo=cloudflarepages&logoColor=white)](https://pages.cloudflare.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

*Prenez des notes en Markdown, enregistrez votre voix, liez vos idées en graphe et synchronisez vos appareils en toute confidentialité grâce au chiffrement de bout en bout XChaCha20-Poly1305 & Argon2id.*

</div>

---

## 🗂️ Architecture multi-plateforme

| Composant | Rôle | Technologies | Dossier |
|---|---|---|---|
| **Mobile / Web (PWA)** | Application client hors-ligne & interface mobile/bureau | React 18, TypeScript, Dexie (IndexedDB), libsodium WASM, Workbox | `web/` |
| **Serveur Sync (`sync-server/`)** | Relais aveugle zéro-connaissance (opérations + médias `NTRM1`) | FastAPI (ou stdlib Python), SQLite WAL, stockage blobs | `sync-server/` |
| **Assistant Serveur (`.deb`)** | Interface graphique Noteor pour configurer et piloter Docker Compose | Python 3.11+, PyQt6 (+ repli Web UI stdlib), Bash, `dpkg-deb` | `sync-server-manager/` |
| **Modules Desktop** | Adaptateur SQLite WAL + moteur E2EE prêts à intégrer dans PyQt6 | Python 3.11+, `sqlite3`, `libsodium.so.23` (`ctypes`) | `sync/desktop/` |
| **Spécifications & Tests** | Architecture E2EE, guide de déploiement, vecteurs crypto & E2E | Markdown, Vitest (TS), `unittest` (Python) | `docs/`, `tests/` |

---

## 🚀 Déploiement complet (Serveur, Mobile Cloudflare & Desktop)

> Un guide détaillé complémentaire (avec modèles `systemd`, `cloudflared` et dépannage) est également disponible dans [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

### 1️⃣ Déploiement du serveur `sync-server/` sur votre machine

Le serveur Sync fonctionne entièrement en relais aveugle : il ne reçoit jamais vos mots de passe ni vos données en clair.

#### Option A — Avec Docker Compose (recommandé)

1. Ouvrez `sync-server/compose.yaml` et configurez `NOTEOR_CORS_ORIGINS` avec l'URL Cloudflare Pages de votre PWA :
   ```yaml
   environment:
     NOTEOR_DATA_DIR: /data
     NOTEOR_CORS_ORIGINS: "https://noteor-pwa.pages.dev,http://localhost:3000"
   ```
2. Démarrez le conteneur :
   ```bash
   cd sync-server
   docker compose up -d --build noteor-sync
   ```
3. Vérifiez l'état du serveur :
   ```bash
   curl -fsS http://127.0.0.1:8787/healthz
   # {"status":"ok","version":"1.1.0"}
   ```

> 💡 **Assistant graphique (`.deb`) disponible** : pour effectuer ces étapes (contrôle des dépendances, saisie de l'URL Cloudflare Pages dans `compose.yaml`, démarrage Docker, test `/healthz` et lecture des logs) depuis une interface graphique aux couleurs de Noteor, installez ou construisez `noteor-sync-manager` dans [`sync-server-manager/`](sync-server-manager/README.md) :
> ```bash
> bash sync-server-manager/packaging/build_deb.sh
> sudo apt install ./sync-server-manager/dist/noteor-sync-manager_1.1.0_all.deb
> noteor-sync-manager
> ```

#### Option B — En Python natif sur votre machine (sans Docker)

- **Avec FastAPI / Uvicorn (environnement dédié `sync-server/.venv`) :**
  ```bash
  cd sync-server
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e .

  mkdir -p ~/noteor-sync-data
  export NOTEOR_DATA_DIR=~/noteor-sync-data
  export NOTEOR_CORS_ORIGINS="https://noteor-pwa.pages.dev,http://localhost:3000"
  uvicorn app.main:app --host 127.0.0.1 --port 8787
  ```
- **Ou sans aucune dépendance externe (mode stdlib HTTP/1.1 intégré) :**
  ```bash
  cd sync-server
  NOTEOR_DATA_DIR=~/noteor-sync-data PORT=8787 python3 -m app.stdlib_server
  ```

#### Exposer le serveur en HTTPS (indispensable pour le mobile)

Les navigateurs mobiles bloquent les requêtes vers `http://` depuis une PWA en HTTPS. Exposez `127.0.0.1:8787` via l'une de ces 3 méthodes :
- **Cloudflare Tunnel** *(recommandé, aucun port à ouvrir sur votre box)* :
  ```bash
  cloudflared tunnel login
  cloudflared tunnel create noteor-sync
  cloudflared tunnel route dns noteor-sync sync.votre-domaine.tld
  cloudflared tunnel --url http://127.0.0.1:8787 run noteor-sync
  ```
- **Tailscale Serve** *(VPN privé)* :
  ```bash
  tailscale serve --bg 8787
  ```
- **Caddy** *(domaine public avec ports 80/443 ouverts)* : adaptez `sync-server/Caddyfile.example` puis exécutez `docker compose up -d --build`.

#### Sauvegarde à chaud du serveur

```bash
cd sync-server
./scripts/backup.sh ~/noteor-sync-backups
```

---

### 2️⃣ Déploiement du Mobile / PWA (`web/`) sur Cloudflare Pages

Les fichiers Cloudflare Pages sont préconfigurés dans le dépôt :
- `web/wrangler.toml` (`pages_build_output_dir = "dist"`)
- `web/public/_headers` (CSP autorisant `'wasm-unsafe-eval'` pour Argon2id/libsodium, permissions micro/caméra et règles de cache PWA `/sw.js` + `/assets/*`)
- `web/vite.config.ts` (exclusion Workbox `NetworkOnly` sur `/api/v1/*`)

#### Méthode A — Via GitHub & Dashboard Cloudflare

1. Dans **Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git**, sélectionnez ce dépôt.
2. Paramètres de build :
   - **Root directory** : `/` *(laisser à la racine pour les workspaces npm)*
   - **Build command** : `npm install && npm run build`
   - **Build output directory** : `web/dist`
   - **Variable d'environnement** : `NODE_VERSION = 22`
3. Déployez et notez l'URL attribuée (`https://<votre-projet>.pages.dev`).
4. Ajoutez cette URL dans `NOTEOR_CORS_ORIGINS` de `sync-server/` puis redémarrez `sync-server/`.

#### Méthode B — Via Wrangler CLI (depuis votre poste)

```bash
# 1. Compilation production (à la racine)
npm install
npm run build

# 2. Connexion & déploiement sur Cloudflare Pages
npx wrangler login
npx wrangler pages project create noteor-pwa --production-branch=main
cd web
npx wrangler pages deploy dist --project-name=noteor-pwa
```

#### Installation sur mobile

1. Ouvrez `https://<votre-projet>.pages.dev` sur **Safari (iOS)** ou **Chrome (Android)**.
2. Ajoutez à l'écran d'accueil (**Installer l'application** / **Sur l'écran d'accueil**).
3. Touchez l'icône bouclier (🛡) dans l'en-tête pour créer votre coffre chiffré avec l'URL HTTPS de votre serveur Sync, puis conservez précieusement votre **clé de secours 256 bits**.

---

### 3️⃣ Déploiement et intégration de Noteor Desktop sur votre machine

Le dossier `sync/desktop/` fournit les modules Python testés (`crypto.py`, `sqlite_sync_adapter.py`, `engine.py`) prêts à être branchés sur votre application PyQt6 locale (qui n'est pas incluse dans cet espace de travail).

#### Étape 1 — Prérequis système (Debian / Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip libsodium23 \
                    alsa-utils python3-dbus python3-gi ffmpeg
```

*(Aucune compilation C ou installation de `PyNaCl` n'est requise : `sync/desktop/crypto.py` utilise directement `libsodium.so.23` via `ctypes`.)*

#### Étape 2 — Sauvegarde de votre base Desktop existante

```bash
cp -a ~/.local/share/Noteor/notes.db ~/.local/share/Noteor/notes.db.bak.$(date +%Y%m%d)
```

#### Étape 3 — Intégration dans votre dépôt PyQt6 local

1. Copiez le répertoire `sync/` dans votre dépôt Noteor Desktop.
2. Remplacez les appels directs SQLite de `database.py` par `DesktopSyncDatabase` (`save_note`, `trash_note`, `restore_note`, `permanently_delete_note`, `save_category`, `save_folder`, `add_tag_to_note`, `remove_tag_from_note`, `add_attachment`, `enqueue_existing_snapshot`).
   - À l'ouverture, `DesktopSyncDatabase` migre automatiquement votre base SQLite v1 en v2 WAL (attribution de `sync_id` UUID v4, dates UTC RFC 3339, Wikilinks `[[Titre|@uuid]]`, déduplication Unicode des tags).
3. Connectez `DesktopSyncEngine` + `SyncHttpClient` + `VaultKeyring` dans votre fenêtre principale PyQt6 (`ui/main_window.py`) pour déclencher `engine.sync_now()`, gérer le QR code de jumelage avec le mobile et résoudre d'éventuels conflits via `engine.resolve_conflict(...)`.
4. Si vous distribuez un paquet `.deb`, ajoutez `libsodium23` aux dépendances dans `packaging/DEBIAN/control` et incluez `sync/` dans le package avant `./packaging/build_deb.sh`.

---

## 🛠️ Tests automatisés et développement local

### Développement web local

```bash
npm install
npm run dev        # Serveur Vite → http://localhost:3000
npm run lint       # Vérification TypeScript strict (tsc --noEmit)
npm run build      # Génération icônes PWA + bundle web/dist
```

### Suite de tests (Crypto croisée TS ↔ Python, Adaptateurs, Serveur & E2E)

```bash
# Tests TypeScript + Vitest (incluant E2E live PWA <-> Serveur Sync <-> Desktop Python)
npx vitest run

# Tests Python (vecteurs crypto, adaptateur Desktop SQLite WAL, serveur HTTP, E2E & gestionnaire .deb)
python3 -m unittest discover -s tests/python -v
```

---

## ✨ Fonctionnalités Noteor PWA, Sync & Outils

- **Éditeur Markdown complet** : barre de formatage, aperçu temps réel, auto-sauvegarde, liens `[[Note|@uuid]]`, vue graphe interactive et vue calendrier.
- **Médias chiffrés (`NTRM1`)** : enregistrement vocal, capture photo/import d'images, lecteur intégré et chiffrement par flux `crypto_secretstream_xchacha20poly1305` par blocs.
- **Organisation & IA** : catégories colorées, dossiers, tags normalisés, sélection multiple, corbeille, export/import JSON, et transcription audio OpenRouter (`:free`).
- **Synchronisation E2EE multi-appareils** :
  - Dérivation de clé maître **Argon2id** exécutée dans un Web Worker dédié ;
  - Coffre déverrouillé en RAM uniquement avec verrouillage automatique par inactivité et mise à zéro mémoire ;
  - Jumelage rapide entre appareils par **QR code / JSON** (HMAC + enveloppe éphémère) ;
  - **Détection de conflits par DAG de révisions** sans écrasement silencieux (`Garder locale`, `Prendre distante`, `Garder les deux`, `Fusionner`) ;
  - **Révocation d'appareil** avec rotation vers l'époque cryptographique suivante (`X25519 crypto_box_seal`).
- **Assistant serveur `.deb` (`noteor-sync-manager`)** : interface graphique PyQt6 (+ repli Web UI sans dépendance) pour vérifier Docker/Compose, configurer `NOTEOR_CORS_ORIGINS` / `NOTEOR_DATA_DIR`, démarrer `noteor-sync`, contrôler `/healthz` et consulter/filtrer les logs Docker.

---

## 📚 Documentation

- [`CHANGELOG.md`](CHANGELOG.md) — Historique des versions (`v1.0.0`, `v1.1.0`)
- [`CONTEXT.md`](CONTEXT.md) — Architecture complète du dépôt et état technique
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — Guide pas à pas (Serveur Sync, Cloudflare Pages, Desktop)
- [`sync-server-manager/README.md`](sync-server-manager/README.md) — Construction et usage du paquet `.deb`

---

## 📃 Licence

[MIT](LICENSE) — Noteor est un logiciel libre.
