# Noteor Sync Manager (`.deb`)

Utilitaire graphique Linux reprenant le thème **Noteor** (Indigo `#4F46E5`, fond `#F9FAFB`, icône carnet/crayon) pour orchestrer le déploiement local de `sync-server/` selon **l'Option A (Docker Compose)**.

## Fonctionnalités

1. **Vérification des dépendances (`scripts/check_deps.sh`)** : contrôle `docker`, l'accès au démon Docker, `docker compose` / `docker-compose`, `curl`, la présence des fichiers `sync-server/` et l'état du port local `8787`.
2. **Paramétrage de `sync-server/compose.yaml`** : champs graphiques pour renseigner l'URL **Cloudflare Pages** de la PWA, `NOTEOR_CORS_ORIGINS`, `NOTEOR_DATA_DIR` et le port hôte `127.0.0.1:<port>:8787` avec aperçu en direct avant écriture.
3. **Gestion du conteneur & contrôle `/healthz`** : exécute `cd sync-server && docker compose up -d --build noteor-sync`, arrêt/redémarrage, `docker compose ps` et vérification HTTP `GET http://127.0.0.1:8787/healthz` (`{"status":"ok","version":"1.1.0"}`).
4. **Diagnostic & logs Docker** : consultation des journaux (`noteor-sync`, `caddy`, `all`), filtre instantané par mot-clé et suivi temps réel (`logs -f`).

## Construction & installation du paquet `.deb`

```bash
# 1. Construire le paquet Debian
bash sync-server-manager/packaging/build_deb.sh

# 2. Installer sur Debian / Ubuntu
sudo apt install ./sync-server-manager/dist/noteor-sync-manager_1.1.0_all.deb

# 3. Lancer depuis le menu d'applications ou le terminal
noteor-sync-manager
```

## Exécution directe sans installation (Python / Web UI)

```bash
# Interface PyQt6 (ou repli Web UI automatique si PyQt6 absent)
python3 sync-server-manager/main.py --sync-dir ./sync-server

# Forcer l'interface Web locale dans le navigateur
python3 sync-server-manager/main.py --web --sync-dir ./sync-server

# Vérification des dépendances en ligne de commande (JSON)
python3 sync-server-manager/main.py --check-deps --sync-dir ./sync-server
```
