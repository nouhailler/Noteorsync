# Guide de déploiement pas à pas — Noteor (Serveur Sync, Mobile Cloudflare Pages & Desktop)

Ce document détaille l'installation et la mise en production complète de l'écosystème **Noteor E2EE** :

1. **Serveur relais chiffré (`sync-server/`)** sur votre machine Linux (Docker ou natif + HTTPS via Cloudflare Tunnel, Tailscale ou Caddy).
2. **Application Mobile / Web PWA (`web/`)** déployée sur **Cloudflare Pages** en HTTPS.
3. **Application Desktop (`sync/desktop/`)** sur votre machine Linux.

---

## Prérequis de sécurité et réseau

- **HTTPS obligatoire sur mobile (iOS / Android)** : la PWA déployée sur Cloudflare Pages (`https://...pages.dev`) ne peut pas appeler un serveur en `http://` depuis un téléphone (contenu mixte bloqué par les navigateurs). Votre serveur `sync-server/` doit donc être exposé en **HTTPS** avec un certificat valide (via **Cloudflare Tunnel**, **Tailscale Serve**, ou **Caddy**).
- **CORS (`NOTEOR_CORS_ORIGINS`)** : le serveur Sync doit autoriser explicitement l'origine Cloudflare Pages de votre PWA (ex. `https://noteor-pwa.pages.dev`).
- **Zéro secret côté serveur** : toutes les notes, tags, métadonnées et pièces jointes (`NTRM1`) sont chiffrés avant envoi par **XChaCha20-Poly1305** et **Argon2id**.

---

## 1. Déploiement de `sync-server/` sur votre machine

Le serveur écoute par défaut sur `127.0.0.1:8787` et stocke ses données (SQLite WAL + blobs chiffrés) dans `NOTEOR_DATA_DIR`.

### Étape 1.1 — Choisir le mode d'exécution

#### Option A : Docker Compose (recommandé en production)

```bash
cd sync-server
```

Éditez `compose.yaml` pour renseigner l'URL exacte de votre PWA Cloudflare Pages dans `NOTEOR_CORS_ORIGINS` :

```yaml
environment:
  NOTEOR_DATA_DIR: /data
  NOTEOR_CORS_ORIGINS: "https://noteor-pwa.pages.dev,http://localhost:3000"
```

Lancez uniquement le serveur Sync (si vous utilisez Cloudflare Tunnel ou Tailscale pour le HTTPS) :

```bash
docker compose up -d --build noteor-sync
```

*(Si vous exposez directement un nom de domaine public avec ports 80/443 ouverts sur votre routeur, adaptez `Caddyfile.example` avec votre domaine et lancez `docker compose up -d --build`.)*

Vérifiez que le serveur répond localement :

```bash
curl -fsS http://127.0.0.1:8787/healthz
# Attendu : {"status":"ok","version":"1.1.0"}
```

#### Option B : Exécution Python native sur votre machine (sans Docker)

**Mode FastAPI / Uvicorn (recommandé en natif) :**

```bash
cd sync-server
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

mkdir -p ~/noteor-sync-data
export NOTEOR_DATA_DIR=~/noteor-sync-data
export NOTEOR_CORS_ORIGINS="https://noteor-pwa.pages.dev,http://localhost:3000"

uvicorn app.main:app --host 127.0.0.1 --port 8787
```

**Mode sans dépendances tierces (bibliothèque standard Python uniquement) :**

```bash
cd sync-server
NOTEOR_DATA_DIR=~/noteor-sync-data PORT=8787 python3 -m app.stdlib_server
```

Pour un démarrage automatique au boot sous Linux (`systemd`), créez `/etc/systemd/system/noteor-sync.service` :

```ini
[Unit]
Description=Noteor Sync Server
After=network-online.target

[Service]
Type=simple
User=VOTRE_UTILISATEUR
WorkingDirectory=/chemin/vers/Noteor/sync-server
Environment="NOTEOR_DATA_DIR=/home/VOTRE_UTILISATEUR/noteor-sync-data"
Environment="NOTEOR_CORS_ORIGINS=https://noteor-pwa.pages.dev"
ExecStart=/chemin/vers/Noteor/sync-server/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now noteor-sync
```

### Étape 1.2 — Exposer `sync-server/` en HTTPS pour le mobile

Choisissez l'une des trois options suivantes (aucune ne nécessite de configurer le TLS à la main) :

1. **Cloudflare Tunnel (`cloudflared`) — idéal avec Cloudflare Pages, aucun port à ouvrir sur votre box** :
   - Installez `cloudflared` sur votre machine et connectez-le à votre compte Cloudflare :
     ```bash
     cloudflared tunnel login
     cloudflared tunnel create noteor-sync
     cloudflared tunnel route dns noteor-sync sync.votre-domaine.tld
     ```
   - Créez `~/.cloudflared/config.yml` :
     ```yaml
     tunnel: noteor-sync
     credentials-file: /home/VOTRE_UTILISATEUR/.cloudflared/<TUNNEL_UUID>.json
     ingress:
       - hostname: sync.votre-domaine.tld
         service: http://127.0.0.1:8787
       - service: http_status:404
     ```
   - Démarrez le tunnel (`cloudflared tunnel run noteor-sync`, ou `sudo cloudflared service install`).
   - *(Test éphémère sans domaine : `cloudflared tunnel --url http://127.0.0.1:8787`.)*

2. **Tailscale Serve (VPN privé chiffré sans domaine public)** :
   ```bash
   tailscale serve --bg 8787
   ```
   Votre URL HTTPS devient `https://<machine>.<tailnet>.ts.net` (accessible depuis vos appareils connectés au même Tailnet, y compris iPhone/Android).

3. **Caddy (`sync-server/Caddyfile.example`)** : si vous avez un VPS ou des ports 80/443 redirigés vers votre machine.

### Étape 1.3 — Sauvegarde à chaud (`scripts/backup.sh`)

```bash
cd sync-server
chmod +x scripts/backup.sh
NOTEOR_DATA_DIR=~/noteor-sync-data ./scripts/backup.sh ~/noteor-sync-backups
```

*(Avec Docker, montez ou copiez le volume `noteor_sync_data` avant sauvegarde.)* Le script utilise `sqlite3.Connection.backup()` pour garantir une copie cohérente à chaud de la base SQLite WAL et copie le dossier `blobs/`.

---

## 2. Déploiement du Mobile / PWA (`web/`) sur Cloudflare Pages

Le dossier `web/` est déjà configuré pour Cloudflare Pages :
- `/web/wrangler.toml` : déclare `pages_build_output_dir = "dist"` (`noteor-pwa`).
- `/web/public/_headers` : configure les en-têtes de sécurité (dont `Content-Security-Policy` avec `'wasm-unsafe-eval'` indispensable pour Argon2id/libsodium WASM et `Permissions-Policy: microphone=(self), camera=(self)`), les règles de cache immuables sur `/assets/*`, et la revalidation immédiate de `/sw.js` et `/manifest.webmanifest`.
- `/web/vite.config.ts` : exclut `/api/v1/*` du cache Service Worker Workbox (`NetworkOnly` + `navigateFallbackDenylist`).

### Option A — Déploiement automatique depuis Git (Dashboard Cloudflare)

1. Connectez-vous sur **Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git**.
2. Sélectionnez votre dépôt Git et configurez les paramètres de build :
   - **Root directory** : `/` *(laisser à la racine pour conserver les workspaces npm et les dépendances crypto de `/package.json`)*
   - **Build command** : `npm install && npm run build`
   - **Build output directory** : `web/dist`
   - **Variable d'environnement** : `NODE_VERSION = 22`
3. Déployez : Cloudflare fournit une URL `https://<projet>.pages.dev`.
4. **Important** : ajoutez cette URL exacte (`https://<projet>.pages.dev`) dans `NOTEOR_CORS_ORIGINS` sur `sync-server/` puis redémarrez le serveur Sync.

### Option B — Déploiement manuel en ligne de commande (Wrangler CLI)

Depuis la racine du projet :

```bash
# 1. Installer les dépendances et compiler la PWA (génère aussi les icônes PNG + dist/_headers)
npm install
npm run build

# 2. Authentifier Wrangler (une seule fois)
npx wrangler login

# 3. Créer le projet Cloudflare Pages (première fois uniquement)
npx wrangler pages project create noteor-pwa --production-branch=main

# 4. Déployer le dossier web/dist
cd web
npx wrangler pages deploy dist --project-name=noteor-pwa
```

### Vérification sur mobile (iOS / Android)

1. Ouvrez `https://<projet>.pages.dev` dans **Safari (iOS)** ou **Chrome (Android)**.
2. Installez la PWA sur l'écran d'accueil (*Partager → Sur l'écran d'accueil* sur iOS, ou *Installer l'application* sur Android).
3. Vérifiez dans l'inspecteur réseau que `https://<projet>.pages.dev/_headers` applique bien la CSP (`wasm-unsafe-eval`) et que le Service Worker n'intercepte pas les requêtes vers votre serveur Sync.

---

## 3. Déploiement et intégration de Noteor Desktop sur votre machine

Les modules de synchronisation Desktop sont prêts dans `sync/desktop/` (`crypto.py`, `sqlite_sync_adapter.py`, `engine.py`). Comme les sources PyQt6 originales (`main.py`, `database.py`, `ui/`) se trouvent sur votre poste local et non dans ce conteneur, voici la procédure exacte pour les raccorder :

### Étape 3.1 — Prérequis système sur Linux (Debian / Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip libsodium23 \
                    alsa-utils python3-dbus python3-gi ffmpeg
```

> **Note :** `sync/desktop/crypto.py` charge directement la bibliothèque système `libsodium.so.23` via `ctypes` : vous n'avez besoin d'aucun paquet `PyNaCl` ou compilateur C supplémentaire pour la cryptographie.

### Étape 3.2 — Sauvegarde de la base locale Desktop avant migration

Avant toute première exécution avec l'adaptateur v2, faites une copie de sauvegarde de votre base locale existante :

```bash
cp -a ~/.local/share/Noteor/notes.db ~/.local/share/Noteor/notes.db.bak.$(date +%Y%m%d)
```

Au premier démarrage avec `DesktopSyncDatabase("~/.local/share/Noteor/notes.db")`, l'adaptateur migre automatiquement le schéma v1 vers v2 en mode WAL :
- ajout des colonnes `sync_id` (UUID v4) et `revision_id` ;
- conversion des dates en UTC RFC 3339 (`YYYY-MM-DDTHH:MM:SS.sssZ`) ;
- conversion des Wikilinks `[[Titre|id]]` vers `[[Titre|@uuid]]` ;
- déduplication NFC + `casefold()` des tags ;
- création des tables de journalisation `sync_operations`, `sync_revisions`, `sync_conflicts`, `sync_pending_ops`, `sync_tombstones`, `sync_state`.

### Étape 3.3 — Raccordement de `sync/desktop/` à votre code PyQt6

Dans votre dépôt Desktop local :

1. **Copier le dossier `sync/`** à la racine de votre projet Desktop.
2. **Remplacer les écritures SQLite directes de `database.py`** par les méthodes transactionnelles de `DesktopSyncDatabase` (`save_note`, `trash_note`, `restore_note`, `permanently_delete_note`, `save_category`, `delete_category`, `save_folder`, `add_tag_to_note`, `remove_tag_from_note`, `add_attachment`, `enqueue_existing_snapshot`) afin que chaque édition crée atomiquement son entrée dans `sync_operations`.
3. **Brancher `DesktopSyncEngine`** sur un `QThread` / `QTimer` dans `ui/main_window.py` :
   - Instancier `SyncHttpClient(server_url, access_token)` + `VaultKeyring.from_vsk(...)` ;
   - Appeler `engine.sync_now()` pour pousser les médias `NTRM1` + opérations chiffrées et tirer les opérations distantes ;
   - En cas d'erreur HTTP `403 DEVICE_REVOKED`, bloquer la sync et notifier l'utilisateur ;
   - Lister `SELECT * FROM sync_conflicts WHERE status='open'` pour proposer les 4 résolutions via `engine.resolve_conflict(...)`.

### Étape 3.4 — Script de test/jumelage rapide Desktop ↔ Serveur en CLI

Vous pouvez tester dès maintenant votre machine Linux contre `sync-server/` avec un script Python minimal (sans modifier votre UI PyQt6) :

```bash
PYTHONPATH=.:sync-server python3 - <<'PY'
from sync.desktop import crypto as pyc
from sync.desktop.sqlite_sync_adapter import DesktopSyncDatabase
from sync.desktop.engine import DesktopSyncEngine, SyncHttpClient, VaultKeyring

# Exemple : lecture du code de jumelage JSON généré par la PWA Mobile (onglet "Jumelage QR")
import json, os
qr = json.loads(input("Collez le JSON de jumelage de la PWA : "))
db = DesktopSyncDatabase("~/.local/share/Noteor/notes.db")
# ... rejoindre la session via client.join_pairing_session(...) puis eng.sync_now()
print("Base migrée et prête :", db.db_path)
db.close()
PY
```

### Étape 3.5 — Packaging `.deb` (si présent sur votre machine)

Si vous construisez le paquet Debian depuis votre dépôt Desktop, vérifiez que `packaging/DEBIAN/control` inclut `libsodium23` dans `Depends:` et que le dossier `sync/` est bien copié dans l'arborescence installée avant d'exécuter `./packaging/build_deb.sh`.

---

## 4. Mise en route complète (Checklist de recette)

1. **Serveur** : démarrez `sync-server/` + exposition HTTPS (Cloudflare Tunnel / Tailscale / Caddy) → vérifiez `curl https://<votre-serveur-sync>/healthz`.
2. **Mobile (Cloudflare Pages)** : déployez `web/dist`, ouvrez `https://<projet>.pages.dev` sur téléphone, cliquez sur l'icône bouclier **Synchronisation** → **Créer un coffre** avec l'URL HTTPS de votre serveur et un mot de passe maître fort, puis notez votre **clé de secours 256 bits**.
3. **Synchronisation initiale** : créez une note et un enregistrement audio sur mobile → cliquez sur **Synchroniser maintenant**.
4. **Jumelage d'un second appareil** : dans **Synchronisation → Jumelage QR**, générez un QR code (ou copiez le JSON), scannez/collez-le sur le second mobile ou sur le Desktop, puis approuvez depuis le premier appareil.
5. **Test de résilience** : modifiez la même note hors-ligne sur deux appareils → resynchronisez → vérifiez la détection dans l'onglet **Conflits** et la résolution sans perte.
