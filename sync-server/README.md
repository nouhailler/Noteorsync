# Noteor Sync Server (v1.1)

Serveur relais aveugle chiffré de bout en bout (FastAPI + SQLite WAL + stockage blobs chiffrés `NTRM1`).

## Démarrage rapide (production avec Docker + Caddy TLS)

```bash
cd sync-server
docker compose up -d
```

## Démarrage local sans dépendances externes (stdlib Python)

Pour développement local ou tests E2E sans FastAPI/uvicorn :

```bash
cd sync-server
NOTEOR_DATA_DIR=./data PORT=8787 python3 -m app.stdlib_server
```

## VPN privé (Tailscale / WireGuard)

Le port `8787` dans `compose.yaml` est lié sur `127.0.0.1:8787` par défaut. Pour une exposition via **Tailscale Serve** :

```bash
tailscale serve --bg 8787
```

La PWA accède ainsi au serveur via l'URL HTTPS `https://<machine>.tailnet.ts.net` sans ouvrir de port public.

## Sauvegarde et restauration

Exécuter `./scripts/backup.sh /chemin/backups` : effectue une copie cohérente à chaud de SQLite WAL via `sqlite3.Connection.backup()` et copie les blobs chiffrés.

Pour restaurer : arrêter le serveur, remplacer `noteor_sync.db` et `blobs/`, puis redémarrer le service.
