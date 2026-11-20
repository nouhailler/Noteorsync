# Manuel d'Exploitation — Noteor Sync Server (`sync-server/`)

Ce manuel couvre l'exploitation courante, la sécurisation réseau, la sauvegarde à chaud, la restauration et le diagnostic du serveur relais chiffré **Noteor Sync v1.1** (`sync-server/`).

---

## 1. Architecture opérationnelle et composants vérifiés

- **Application ASGI (production)** : `sync-server/app/main.py` exécutée par `uvicorn app.main:app` (dépendances `fastapi>=0.115.0`, `uvicorn[standard]>=0.32.0`, `pydantic>=2.9.0` dans `sync-server/pyproject.toml`).
- **Serveur de repli sans dépendance (`stdlib`)** : `sync-server/app/stdlib_server.py` (`ThreadingHTTPServer`), dédié au développement local et aux tests E2E sans FastAPI.
- **Logique métier et stockage** : `sync-server/app/services/sync_service.py` + `sync-server/app/storage/database.py` (SQLite mode `WAL`, `PRAGMA synchronous = FULL`, `PRAGMA foreign_keys = ON`).
- **Arborescence persistante (`NOTEOR_DATA_DIR`)** :
  - `${NOTEOR_DATA_DIR}/noteor_sync.db` (+ `-wal` et `-shm`)
  - `${NOTEOR_DATA_DIR}/blobs/<vault_id>/<media_id>.bin` (et `.bin.part` durant upload chunké)
- **Assistant graphique `.deb` (`sync-server-manager/`)** : gestionnaire Option A (Docker Compose) via `noteor-sync-manager` (`sync-server-manager/main.py`).

---

## 2. Variables d'environnement et configuration

| Variable | Défaut dans le code | Usage en production |
| --- | --- | --- |
| `NOTEOR_DATA_DIR` | `./data` (`compose.yaml` : `/data`) | Répertoire persistant contenant `noteor_sync.db` et `blobs/` |
| `NOTEOR_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Liste séparée par des virgules des origines autorisées (ex. `https://noteor-pwa.pages.dev`) |
| `NOTEOR_ENV` | *(non défini)* | Mettre `NOTEOR_ENV=production` pour interdire au démarrage `NOTEOR_CORS_ORIGINS=*` (fail-fast `RuntimeError`) |
| `PORT` (`stdlib_server` uniquement) | `8787` | Port d'écoute du serveur `stdlib` uniquement |

> **Règle de sécurité CORS :** `NOTEOR_CORS_ORIGINS="*"` est refusé au démarrage dès que `NOTEOR_ENV=production`. Définissez toujours l'origine HTTPS exacte de la PWA (sans slash final), par exemple :
> `NOTEOR_CORS_ORIGINS="https://noteor-pwa.pages.dev"`

---

## 3. Démarrage du serveur

### 3.1 Option A : Docker Compose (recommandé sur serveur Linux)

Le fichier `sync-server/compose.yaml` lie le port `8787` uniquement sur `127.0.0.1:8787` et monte le volume nommé `noteor_sync_data` sur `/data`.

```bash
cd sync-server
# Vérifier/ajuster NOTEOR_CORS_ORIGINS et ajouter NOTEOR_ENV: production dans compose.yaml
docker compose up -d --build noteor-sync
```

Avec le reverse proxy Caddy inclus (uniquement si les ports 80/443 publics pointent vers votre serveur et après édition de `sync-server/Caddyfile.example` avec votre domaine réel) :

```bash
cd sync-server
docker compose up -d --build
```

### 3.2 Option B : Exécution native FastAPI / Uvicorn (`venv` + `systemd`)

Prérequis système vérifiés : Python `>=3.11`, `libsodium23` (pour les utilitaires Desktop/tests), `sqlite3`.

```bash
cd sync-server
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install .

mkdir -p /var/lib/noteor-sync
export NOTEOR_ENV=production
export NOTEOR_DATA_DIR=/var/lib/noteor-sync
export NOTEOR_CORS_ORIGINS="https://noteor-pwa.pages.dev"

uvicorn app.main:app --host 127.0.0.1 --port 8787 --workers 1
```

> **Important SQLite WAL :** Utilisez `--workers 1` (mono-processus asynchrone) afin de préserver l'ordonnancement strict des transactions SQLite `BEGIN IMMEDIATE` et le verrou `threading.RLock()` de `SyncStorage`.

### 3.3 Mode développement local (`stdlib_server`)

Uniquement pour développement local ou CI sans FastAPI :

```bash
cd sync-server
NOTEOR_DATA_DIR=./data PORT=8787 python3 -m app.stdlib_server
```

---

## 4. Exposition HTTPS et réseau privé (Tailscale / WireGuard / Tunnel / Caddy)

La PWA Cloudflare Pages (`https://...pages.dev`) impose **HTTPS** pour contacter `sync-server/`. Ne jamais exposer le port `8787` en HTTP clair sur Internet.

### 4.1 Tailscale Serve (VPN privé chiffré sans ouverture de port)

```bash
tailscale serve --bg 8787
tailscale serve status
```

- L'URL HTTPS devient `https://<machine>.<tailnet>.ts.net`.
- Ajoutez cette URL si nécessaire côté client ; côté serveur `NOTEOR_CORS_ORIGINS` doit contenir l'origine **de la PWA** (`https://noteor-pwa.pages.dev`).

### 4.2 WireGuard (accès privé vers `127.0.0.1:8787` ou IP WireGuard + Caddy interne)

- Si vous utilisez WireGuard, publiez Caddy en TLS interne ou terminez TLS sur l'interface `wg0` avant de relayer vers `127.0.0.1:8787`.

### 4.3 Cloudflare Tunnel (`cloudflared`)

Dans la configuration d'ingress `cloudflared` :

```yaml
ingress:
  - hostname: sync.votre-domaine.tld
    service: http://127.0.0.1:8787
  - service: http_status:404
```

### 4.4 Caddy (`sync-server/Caddyfile.example`)

- Remplacer `sync.exemple.org` par votre nom de domaine réel.
- Recommandation d'exploitation : ajouter un rate-limiting proxy / `request_body { max_size 16MB }` devant Caddy/Cloudflare, le serveur applicatif limitant déjà à `16 MiB` les requêtes JSON et `4 MiB` les chunks PUT médias (`413 PAYLOAD_TOO_LARGE` / `413 CHUNK_TOO_LARGE`).

---

## 5. Vérifications de santé (`/healthz` et `/api/v1/health`)

```bash
# Probe technique (Docker HEALTHCHECK et surveillance locale)
curl -fsS http://127.0.0.1:8787/healthz
# Attendu : {"status":"ok","version":"1.1.0"}

# Probe API v1 (via reverse proxy HTTPS)
curl -fsS https://<votre-domaine-sync>/api/v1/health
# Attendu : {"status":"ok","version":"1.1.0","api_version":1}
```

---

## 6. Sauvegarde à chaud et restauration testée

### 6.1 Sauvegarde à chaud (SQLite WAL + blobs chiffrés)

Le script `sync-server/scripts/backup.sh` exécute `sqlite3.Connection.backup()` à chaud sans interrompre le service, puis copie `${NOTEOR_DATA_DIR}/blobs`.

Exécution en installation native :

```bash
cd sync-server
chmod +x scripts/backup.sh
NOTEOR_DATA_DIR=/var/lib/noteor-sync ./scripts/backup.sh /var/backups/noteor-sync
```

Exécution avec Docker Compose (le script est copié dans `/srv/noteor-sync/scripts/backup.sh` à l'intérieur de l'image) :

```bash
mkdir -p ./backups
docker compose -f sync-server/compose.yaml exec -T noteor-sync \
  /srv/noteor-sync/scripts/backup.sh /tmp/noteor-bak
docker cp noteor-sync-server:/tmp/noteor-bak/. ./backups/
docker compose -f sync-server/compose.yaml exec -T noteor-sync rm -rf /tmp/noteor-bak
```

> **Sécurité des sauvegardes :** Même si les notes et médias sont chiffrés E2EE (`XChaCha20-Poly1305` / `NTRM1`), la base contient les métadonnées structurelles (UUID, compteurs, jetons hachés SHA-256). Chiffrez vos archives hors-site avant réplication.

### 6.2 Procédure de restauration

1. **Arrêter le serveur** pour éviter toute écriture concurrente pendant le remplacement des fichiers :
   ```bash
   # Docker :
   docker compose -f sync-server/compose.yaml stop noteor-sync
   # OU systemd :
   sudo systemctl stop noteor-sync
   ```
2. **Sauvegarder l'état courant par précaution**, puis nettoyer d'anciens résidus `-wal` / `-shm` avant de copier la sauvegarde :
   ```bash
   STAMP_BAK="noteor-sync-YYYYMMDDTHHMMSSZ"
   rm -f "${NOTEOR_DATA_DIR}/noteor_sync.db-wal" "${NOTEOR_DATA_DIR}/noteor_sync.db-shm"
   cp "/var/backups/noteor-sync/${STAMP_BAK}/noteor_sync.db" "${NOTEOR_DATA_DIR}/noteor_sync.db"
   rm -rf "${NOTEOR_DATA_DIR}/blobs"
   if [ -d "/var/backups/noteor-sync/${STAMP_BAK}/blobs" ]; then
     cp -a "/var/backups/noteor-sync/${STAMP_BAK}/blobs" "${NOTEOR_DATA_DIR}/blobs"
   fi
   ```
3. **Vérifier l'intégrité SQLite avant redémarrage** :
   ```bash
   python3 -c "import sqlite3; c=sqlite3.connect('${NOTEOR_DATA_DIR}/noteor_sync.db'); print(c.execute('PRAGMA integrity_check').fetchone())"
   # Attendu : ('ok',)
   ```
4. **Redémarrer et contrôler** :
   ```bash
   docker compose -f sync-server/compose.yaml start noteor-sync
   curl -fsS http://127.0.0.1:8787/healthz
   ```

---

## 7. Logs, diagnostic, arrêt/redémarrage et mises à jour

### 7.1 Consultation des logs

```bash
# Docker Compose
docker compose -f sync-server/compose.yaml logs -f --tail=200 noteor-sync
docker compose -f sync-server/compose.yaml logs -f --tail=100 caddy

# Systemd (installation native Uvicorn)
journalctl -u noteor-sync -f -n 200
```

### 7.2 Arrêt, redémarrage et mise à jour

```bash
# Arrêt propre Docker
docker compose -f sync-server/compose.yaml stop noteor-sync

# Redémarrage après modification de NOTEOR_CORS_ORIGINS ou NOTEOR_ENV
docker compose -f sync-server/compose.yaml up -d --force-recreate noteor-sync

# Mise à jour après git pull (toujours sauvegarder avant)
./sync-server/scripts/backup.sh ./backups
docker compose -f sync-server/compose.yaml up -d --build noteor-sync
curl -fsS http://127.0.0.1:8787/healthz
```

### 7.3 Diagnostic rapide des incidents fréquents

| Symptôme | Cause probable | Action corrective |
| --- | --- | --- |
| PWA bloquée en erreur CORS / navigateur console | Origine exacte absente de `NOTEOR_CORS_ORIGINS` | Ajouter l'origine HTTPS exacte (ex. `https://noteor-pwa.pages.dev`) et recréer le service |
| HTTP `403 DEVICE_REVOKED` côté client | Appareil révoqué par un autre appareil du coffre | Purger localement ou ré-appairer après vérification utilisateur |
| HTTP `413 PAYLOAD_TOO_LARGE` / `CHUNK_TOO_LARGE` | JSON > 16 MiB ou chunk média > 4 MiB | Vérifier la taille de batch (`<= 100` ops) et chunk (`<= 4 MiB`) |
| HTTP `409 DEVICE_SEQUENCE_GAP` | Désalignement `device_seq` après restauration ou corruption | Resynchroniser ou réconcilier depuis l'outbox client |
| Erreur `RuntimeError` au démarrage | `NOTEOR_ENV=production` avec `NOTEOR_CORS_ORIGINS=*` | Remplacer `*` par la liste explicite des origines autorisées |
| Fichiers `.bin.part` anciens dans `blobs/` | Uploads médias interrompus avant `/complete` | Purger uniquement les `.bin.part` sans session `uploading` active récente pendant fenêtre de maintenance |
