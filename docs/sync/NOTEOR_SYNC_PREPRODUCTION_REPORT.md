# Rapport de Validation Préproduction — Noteor Sync v1.1

**Date d'audit :** 2026-11-20  
**Référentiel baseline :** `docs/sync/NOTEOR_SYNC_PREPRODUCTION_BASELINE.md`  
**Périmètre :** CI/CD GitHub Actions, serveur `sync-server/` (FastAPI / Uvicorn / `SyncService` / `stdlib_server`), sécurité cryptographique et E2EE (`sync/desktop/` + `web/src/sync/`), exploitation et sauvegardes.

---

## 1. Verdict exécutif

| Critère | État |
| --- | --- |
| **Statut global de mise en production** | **`NON VALIDÉ`** |
| **Tests locaux Python (`unittest`)** | `PASS` — **25 / 25 tests** (`4.327s`, incluant 5 nouveaux tests adversariaux et interop TS↔Py) |
| **Tests locaux TypeScript (`vitest`)** | `PASS` — **4 fichiers / 16 tests** (`3.38s`, incluant 4 tests adversariaux et E2E complet) |
| **Build & Typecheck PWA (`web/`)** | `PASS` — `tsc --noEmit` et `vite build` sans erreur |
| **Serveur réel FastAPI + Uvicorn (`app.main`)** | **`NON EXÉCUTÉ DANS CE CONTENEUR`** (`fastapi`, `uvicorn`, `httpx`, `pytest` absents de l'environnement) |
| **GitHub Actions (`.github/workflows/ci.yml`)** | **`NON EXÉCUTÉ`** (workflow créé dans ce dépôt, exécution distante requise après push) |
| **Application Desktop complète PyQt6** | **`HORS CONTENEUR`** (`sync/desktop/` vérifié sous Python 3.11 + SQLite, sources GUI PyQt6 absentes) |

**Motivation stricte du verdict (`NON VALIDÉ`) :**  
Toutes les vérifications exécutables localement (cryptographie croisée Python ↔ TypeScript, adaptateur SQLite Desktop, moteur PWA Dexie, tests adversariaux Python & TypeScript, serveur HTTP `stdlib_server`, audit runtime JS `bun audit --production`, compilation bytecode et build PWA Vite/Workbox) passent à 100 % après les correctifs de phase 4. En revanche, **la validation en production exige l'exécution réelle de la suite `sync-server/tests/test_fastapi_uvicorn.py` contre `fastapi` + `uvicorn` (ASGI en production)** ainsi que le passage vert de la pipeline `.github/workflows/ci.yml` sur GitHub Actions (incluant matrice Python 3.11/3.12 et `pip-audit`).

---

## 2. Cartographie exhaustive des vérifications

| Vérification | Commande | Résultat | Périmètre couvert / limite |
| --- | --- | --- | --- |
| Compilation Python | `python3 -m py_compile ...` | **PASS** | Tous les fichiers `.py` (`sync/`, `sync-server/`, `sync-server-manager/`, `tests/`) |
| Tests Python unitaires, vecteurs, E2E & sécurité | `python3 -m unittest discover -s tests/python -v` | **PASS (25 tests)** | Vecteurs RFC/FIPS/libsodium, interop TS→Py, SQLite WAL adapter, 5 suites adversariales, E2E Desktop↔Server |
| Tests TypeScript & E2E PWA | `npx vitest run` | **PASS (4 fichiers / 16 tests)** | Vecteurs Py↔TS, Dexie/engine, 4 suites adversariales, E2E PWA↔Server↔Desktop (`stdlib_server`) |
| Vérification typage TS | `npm run lint` (`tsc --noEmit`) | **PASS** | Workspace `web/` complet |
| Build PWA production | `npm run build` | **PASS** | `generate-icons.mjs` + `tsc` + `vite build` + `_headers` + `sw.js` |
| Audit dépendances JS runtime | `bun audit --production --audit-level=low` | **PASS (0 CVE)** | 231 paquets runtime vérifiés (`No vulnerabilities found`) |
| Audit dépendances JS complet (dev inclus) | `bun audit` | **6 avis build-time** | 3 `high` (`sharp@0.34.5` script d'icônes), 3 `moderate` (`glob`, `minimatch`, `postcss-selector-parser`) |
| Recherche statique de secrets | Inspection `grep` + script Python | **PASS (0 fuite)** | Aucune clé privée ni jeton dur dans le code applicatif |
| Import ASGI FastAPI (`app.main`) | `python3 -c "import app.main"` | **FAIL (conteneur)** | `ModuleNotFoundError: No module named 'fastapi'` |
| Suite serveur ASGI + Uvicorn | `pytest -v sync-server/tests` | **NON EXÉCUTABLE ICI** | Écrite dans `sync-server/tests/test_fastapi_uvicorn.py`, prête pour CI / hôte Linux |
| Exécution CI GitHub Actions | Push / PR vers `main` | **NON EXÉCUTABLE ICI** | Workflow créé dans `.github/workflows/ci.yml` |
| Docker Compose & Caddy | `docker compose up` | **NON EXÉCUTABLE ICI** | Démon Docker absent du conteneur ; cohérence `compose.yaml` validée par test |

---

## 3. Audit CI GitHub Actions & Gouvernance de branche

### 3.1 Workflow créé : `.github/workflows/ci.yml`

- **Moindre privilège :** `permissions: contents: read` au niveau du workflow.
- **Concurrence & bornes :** `concurrency` avec `cancel-in-progress: true` et `timeout-minutes` explicite sur chacun des 4 jobs (`10` à `15` min).
- **Zero masquage d'erreur :** aucune directive `continue-on-error: true` ni `|| true`.
- **Actions épinglées par SHA immuable :**
  - `actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803` (`# v6.1.0`)
  - `actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1` (`# v6.3.0`)
  - `oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6` (`# v2.2.0`)
  - `actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38` (`# v6.5.0`)
- **4 jobs distincts :**
  1. `python-and-crypto-interop` : matrice Python `3.11` / `3.12`, `libsodium23`, `Node 22` + `Bun` (requis pour le test bidirectionnel TS→Py), `py_compile`, `ruff check --select E9,F63,F7,F82`, `unittest`.
  2. `web-pwa-and-e2e` : `bun install --frozen-lockfile`, `bun run lint`, `bunx vitest run`, `bun run build`, vérification de présence de `web/dist/{index.html,_headers,sw.js,manifest.webmanifest}`.
  3. `fastapi-uvicorn-server` : installation `pip install -e "./sync-server[dev]"`, import `app.main`, exécution `pytest -v sync-server/tests` (TestClient + sous-processus `uvicorn`).
  4. `security-and-audit` : `bun audit --production --audit-level=low`, `bun audit --audit-level=critical`, `pip-audit`, vérification des invariants de sécurité (`127.0.0.1:8787`, CSP `_headers`, Workbox `NetworkOnly`, absence de secrets).

### 3.2 Règles de protection de branche recommandées (`main`)

À activer dans **GitHub Repository Settings → Branches → Branch protection rules** (`main`) :
1. **Require a pull request before merging** (au moins 1 approbation humaine).
2. **Require status checks to pass before merging** + **Require branches to be up to date before merging** en exigeant :
   - `Python Matrix (3.11) & Interop`
   - `Python Matrix (3.12) & Interop`
   - `Web PWA TypeScript, Vitest & Build`
   - `FastAPI & Uvicorn Server Tests`
   - `Dependency Audit & Security Invariants`
3. **Require signed commits** et **Do not allow bypassing the above settings** (`include administrators`).
4. **Block force pushes** et **Block deletions**.

---

## 4. Validation du serveur `sync-server/`

### 4.1 Parité `app/main.py` vs `app/stdlib_server.py` vs `SyncService`

Les 16 routes `/healthz`, `/api/v1/health`, `/api/v1/vaults*`, `/operations*`, `/keys/epochs`, `/devices*`, `/pairing*`, `/media*` délèguent intégralement à la classe unique `SyncService` (`sync-server/app/services/sync_service.py`), garantissant un comportement métier, SQLite (`BEGIN IMMEDIATE`, `WAL`, `FULL`) et format d'erreur JSON `{"error": {"code", "message", "details"}}` identiques entre FastAPI et `stdlib_server`.

### 4.2 Durcissements serveur vérifiés

- **CORS fail-fast en production :** `_resolve_cors_origins()` refuse `NOTEOR_CORS_ORIGINS="*"` lorsque `NOTEOR_ENV=production` et applique `["http://localhost:3000", "http://127.0.0.1:3000"]` par défaut si la variable est vide (au lieu d'un wildcard permissif).
- **Bornage anti-DoS pré-lecture :** rejet `413 PAYLOAD_TOO_LARGE` (> 16 MiB sur routes JSON) et `413 CHUNK_TOO_LARGE` (> 4 MiB sur `PUT .../media/uploads/{id}`) avant allocation mémoire complète ; lecture streamée côté `main.py` (`request.stream()`) et lecture hash/taille streamée par blocs de 1 MiB dans `complete_media_upload`.
- **Téléchargement blob streamé :** `stdlib_server._stream_file` (blocs 64 KiB) et `FastAPI` `FileResponse` évitent la charge mémoire complète du blob chiffré.
- **Validation stricte des timestamps :** `_parse_iso()` rejette les horodatages naïfs sans fuseau horaire (`400 INVALID_PAYLOAD` au lieu de `TypeError` 500 dans `_get_valid_pairing_in_tx`).
- **Correction critique du verrouillage de jumelage :** les tentatives échouées (`failed_attempts` et `status='locked'` à 5 échecs) sont désormais commises avant de lever `403 DEVICE_MISMATCH`.

### 4.3 Limite de validation restante

- `sync-server/tests/test_fastapi_uvicorn.py` couvre `TestClient` (ASGI), le lancement réel d'un sous-processus `uvicorn app.main:app`, la persistance SQLite WAL + blobs après redémarrage, la concurrence de tirage, les bornes 413 et le script `scripts/backup.sh`, **mais n'a pas pu être exécuté dans ce conteneur** faute de paquets Python tiers (`fastapi`, `uvicorn`, `httpx`, `pytest`).

---

## 5. Analyse sécurité E2EE & Modèle de menace

### 5.1 Hiérarchie des clés extraite directement du code (`sync/desktop/crypto.py` & `web/src/sync/crypto.ts`)

```text
Master Password (UTF-8) + vsk_salt (16 B)
  └─ Argon2id (m=65536 KiB, t=3, p=1, max 524288 KiB / t<=20) ──► KEK (32 B)
                                                                       │ (AAD: vsk_recovery_aad)
Recovery Key (32 B, affichage Base64URL) ──────────────────────► Optionnel : enveloppe VSK (XChaCha20-Poly1305)

VSK (Vault Secret Key, 32 B aléatoires)
  └─ HKDF-SHA-256 (salt=UUID_bytes(vault_id) [16 B], info="noteor-sync-epoch-v1:<uint32 BE epoch>")
       └─ Epoch Root Key E_k (32 B)
            ├─ HKDF-SHA-256 (salt="", info="noteor-sync-op-key-v1")     ──► K_OP_k   (32 B, XChaCha20-Poly1305, AAD RFC 8785)
            └─ HKDF-SHA-256 (salt="", info="noteor-sync-media-key-v1")  ──► K_MEDIA_k (32 B, secretstream NTRM1, chunks 1 MiB)

Clés par appareil :
  - Paire asymétrique X25519 (pk_dev, sk_dev) [32 B / 32 B] : scellement crypto_box_seal des clés d'époque E_k (rotation)
  - device_access_token ("ntr_dev_...") : stocké haché SHA-256 côté serveur (devices.token_hash_sha256)

Jumelage éphémère (Device A ↔ Device B) :
  - Secret 128 bits (16 B) + pairing_nonce + session_nonce_b + UUID bytes
  └─ HKDF-SHA-256 ──► K_PAIR (32 B, XChaCha20-Poly1305 pour bundle VSK/epoch + HMAC secret_proof / confirm_hmac)
```

### 5.2 Actifs, surfaces et frontières de confiance

- **Serveur relais (`sync-server/`) :** non fiable sur la confidentialité du contenu ; fiable uniquement pour l'ordonnancement `server_seq`, le stockage durable et la conservation de l'annuaire des clés publiques `devices.public_key_x25519`.
- **Clients (`sync/desktop/`, `web/`) :** frontière de confiance E2EE. Tout déchiffrement requiert le contrôle strict du `vault_id`, de la monotonie `server_seq`, des types canoniques UUIDv4/Base64URL et des chemins locaux de pièces jointes.

---

## 6. Vulnérabilités & non-conformités relevées (classées par gravité)

| ID | Gravité | Composant / Preuve | Constat & scénario d'attaque | État & remédiation |
| --- | --- | --- | --- | --- |
| **SEC-01** | **CRITIQUE** | `sync/desktop/engine.py` (`download_pending_media`, `_safe_attachment_dest`) | `file_name` déchiffré passé dans `os.path.join(self.media_dir, ...)` sans confinement : un pair compromis ou une note forgée avec `file_name="../../.ssh/authorized_keys"` permettait une écriture arbitraire (**Path Traversal**). | **CORRIGÉE PHASE 4** — UUID `media_id` validé, nom nettoyé (`PurePosixPath`/`PureWindowsPath` + regex), vérification `.resolve().is_relative_to(media_dir.resolve())` ; vérifiée par test adversarial. |
| **SEC-02** | **ÉLEVÉE** | `sync-server/app/services/sync_service.py` (`confirm_pairing`, lignes 560-570) | La levée de `ApiError(403, ...)` à l'intérieur de `with self.storage.transaction()` provoquait un `ROLLBACK` SQLite : `failed_attempts` et `status='locked'` au 5e échec n'étaient **jamais persistés**, annulant le verrouillage anti-brute-force. | **CORRIGÉE PHASE 4** — Commit explicite de `failed_attempts` et `status='locked'` hors exception avant de lever `ApiError(403)` ; prouvé par `test_security_adversarial.py`. |
| **SEC-03** | **ÉLEVÉE** | `sync-server/app/services/sync_service.py` (`epoch_keys`, `rotate_epoch`) | **Limite architecturale V1.1 :** les enveloppes `crypto_box_seal` de rotation d'époque ne portent pas de signature asymétrique d'émetteur (Ed25519). Un serveur compromis détenant les `public_key_x25519` peut injecter une fausse clé d'époque `E_{k+1}` scellée vers la victime. | **PROPOSÉE V1.2** (non modifiée pour compatibilité filaire V1.1) — Ajouter une paire Ed25519 par appareil et signer chaque `(vault_id, key_epoch, target_device_id, wrapped_epoch_key)` avec vérification client avant déchiffrement. |
| **SEC-04** | **ÉLEVÉE** | `sync-server/app/services/sync_service.py` (`get_pairing_status`, `confirm_pairing`) | **Limite architecturale V1.1 :** le serveur valide la syntaxe Base64URL de `confirm_hmac` et l'identité déclarée `device_b_id`, mais ne connaît pas `K_PAIR`. Or `GET /pairing/{id}` sans authentification expose `device_b_id` après `join` : un attaquant connaissant `(vault_id, pairing_id)` et devinant/lisant `device_b_id` pourrait courser `POST /confirm` après `approve` (sans toutefois pouvoir déchiffrer `encrypted_vault_bundle`, protégé par `K_PAIR`). | **PROPOSÉE V1.2** — Faire transmettre par Device A lors de `/approve` un engagement `confirm_hmac_sha256` calculé ou restreindre `GET /pairing/{id}` + authentifier `/confirm` par preuve dérivée vérifiable serveur. |
| **SEC-05** | **ÉLEVÉE** | `sync/desktop/crypto.py` & `web/src/sync/crypto.ts` | Absence de bornes supérieures Argon2id (un enveloppe malveillante avec `mem_cost_kib=2097152` ou `iterations=1000` provoque un DoS OOM/CPU) et absence de contrôle strict `len == 32` sur clés X25519 avant appel `ctypes` `libsodium` (lecture hors-limites C). | **CORRIGÉE PHASE 4** — Caps `ARGON2ID_MAX_MEM_KIB = 524288` et `ARGON2ID_MAX_ITERATIONS = 20` + refus `bool`, contrôle strict `32` octets X25519 et mise à zéro `memset` des tampons C sensibles. |
| **SEC-06** | **MOYENNE** | `sync/desktop/engine.py` & `web/src/sync/engine.ts` (`pull*Operations`) | Un serveur malveillant pouvait rejouer/réordonner des `server_seq` décroissants ou injecter des enveloppes d'un autre `vault_id`. | **CORRIGÉE PHASE 4** — Vérification systématique `env.vault_id == expected_vault_id`, format strict `/^[1-9][0-9]*$/`, et stricte croissance `server_seq > last_seen_seq`. |
| **SEC-07** | **MOYENNE** | `sync-server/app/main.py` & `stdlib_server.py` | CORS wildcard `*` par défaut quand `NOTEOR_CORS_ORIGINS` était absent, absence d'en-têtes `X-Content-Type-Options: nosniff` / `Cache-Control: no-store`, absence de plafond HTTP pré-lecture (DoS mémoire sur `Content-Length` gigantesque), crash `500` sur timestamp naïf `expires_at`, et `read_bytes()` complet en RAM dans `complete_media_upload` / `_stream_file`. | **CORRIGÉE PHASE 4** — Parité stricte CORS + garde `NOTEOR_ENV=production`, plafond 16 MiB JSON / 4 MiB PUT média, lecture/hachage/envoi streamés, rejet propre `400` sur timestamp naïf, borne `vsk_recovery_envelope` <= 16 KiB et pagination `pull_operations`. |
| **SEC-08** | **MOYENNE** | `sync-server/` & clients locaux | Pas de rate-limiting HTTP intégré (délégué au reverse proxy), pas de purge automatique TTL des `.bin.part` abandonnés, jetons `device_access_token` conservés en clair dans le stockage local SQLite/IndexedDB (et immuabilité des chaînes JS avant GC). | **RISQUE ACCEPTÉ DOCUMENTÉ** — Configurer rate-limiting côté Caddy/Cloudflare, ajouter tâche de purge `.bin.part` > 24 h en V1.2, conserver verrouillage explicite `lockVaultNow()`. |
| **SEC-09** | **FAIBLE** | Dépendances dev/build JS (`bun audit`) | `sharp@0.34.5` (3 `high`), `glob@11.1.0`, `minimatch`, `postcss-selector-parser` (3 `moderate`) utilisés uniquement au build (`web/scripts/generate-icons.mjs`, `vite-plugin-pwa`, `tailwindcss`). | **MITIGÉ / SURVEILLANCE** — `bun audit --production` confirme **0 vulnérabilité** dans le bundle client (`231` paquets runtime). Mettre à jour `sharp` / `tailwindcss` hors fenêtre d'audit si souhaité. |

---

## 7. Correctifs appliqués & preuves de non-régression

| Fichier modifié | Changement circonscrit appliqué | Non-régression vérifiée |
| --- | --- | --- |
| `sync/desktop/crypto.py` | Caps Argon2id (`<= 524288 KiB`, `<= 20` itérations), `int` strict hors `bool`, contrôle 32 B X25519, `_zero_c_buf` via `ctypes.memset` | 8 tests `test_crypto_vectors.py` + interop TS↔Py PASS |
| `web/src/sync/crypto.ts` | Mêmes caps Argon2id et contrôles longueur `32` B X25519 + `key_epoch >= 1` | 7 tests `crypto.test.ts` + E2E PASS |
| `sync/desktop/engine.py` | Confinement `_safe_attachment_dest(media_id, file_name)` + contrôle `vault_id` & monotonie `server_seq` | `test_desktop_adapter_and_engine.py`, `test_server_and_e2e.py`, `test_security_adversarial.py` PASS |
| `web/src/sync/engine.ts` | Contrôle `env.vault_id` & stricte monotonie `BigInt(server_seq) > lastSeenSeq` | `db_and_engine.test.ts`, `security_adversarial.test.ts`, `sync_e2e.test.ts` PASS |
| `sync-server/app/services/sync_service.py` | `_parse_iso` refuse naïf ; `vsk_recovery_envelope` <= 16 KiB ; `pull_operations` borné ; `complete_media_upload` streamé 1 MiB ; commit verrouillage `confirm_pairing` hors rollback | `test_server_and_e2e.py` + `test_security_adversarial.py` PASS |
| `sync-server/app/main.py` | CORS localhost + garde `NOTEOR_ENV=production` vs `*` ; middleware `413` pré-lecture + en-têtes `nosniff`/`no-store` ; streaming chunk upload 4 MiB ; `RequestValidationError` -> `400` | Compilation Python propre ; à exécuter sous `pytest` en CI |
| `sync-server/app/stdlib_server.py` | Alignement exact CORS (`Vary: Origin`), pré-lecture bornée `16 MiB / 4 MiB`, drainage borné sur `413`, streaming download 64 KiB, en-têtes sécurité | `test_server_and_e2e.py`, `test_security_adversarial.py`, `sync_e2e.test.ts` PASS |
| `sync-server/pyproject.toml` | Ajout `[tool.pytest.ini_options]` (`testpaths = ["tests"]`, `pythonpath = ["."]`) | Validé syntaxiquement |

*Aucun format cryptographique (`VSK`, `KEK`, `HKDF`, `XChaCha20-Poly1305`, `NTRM1`, `RFC 8785 AAD`, `crypto_box_seal`), aucun paramètre Argon2id (`65536 / 3 / 1`), aucun contrat HTTP V1.1 ni modèle de données n'a été altéré.*

---

## 8. Nouveaux tests ajoutés

1. **`sync-server/tests/test_fastapi_uvicorn.py`** *(suite ASGI + Uvicorn réel)* :
   - Couvre `TestClient(app)` (santé, création coffre, CORS dev/production, rejet `400 INVALID_PAYLOAD` Pydantic, rejet `413 CHUNK_TOO_LARGE`, rejet timestamp naïf, verrouillage pairing 5 tentatives) et lancement d'un sous-processus `uvicorn app.main:app` vérifiant la persistance SQLite WAL + blobs après redémarrage, pulls concurrents et sauvegarde à chaud via `scripts/backup.sh`.
2. **`tests/python/test_security_adversarial.py`** *(5 méthodes de tests négatifs Python)* :
   - Flip de bit AEAD et permutation de chaque champ AAD + rejet UUID majuscule / Base64URL non canonique ;
   - DoS Argon2id hors bornes & clés X25519 tronquées ;
   - Réordonnancement, duplication et troncature de chunks `NTRM1` ;
   - Rejet traversal `../../evil` côté Desktop + rejet splice multi-coffre et rejeu `server_seq` ;
   - Tests HTTP live sur `stdlib_server` (en-têtes `413` sans lecture complète pour `50 MiB` JSON et `5 MiB` chunk, CORS par défaut, timestamp naïf `400`, enveloppe VSK > 16 KiB `400`, verrouillage réel au 6e essai pairing `409`, `since_seq=-1` `400`).
3. **`web/src/sync/__tests__/security_adversarial.test.ts`** *(4 tests négatifs TypeScript)* :
   - Altérations AEAD/AAD & encodages non canoniques ;
   - Bornes DoS Argon2id & clés X25519 invalides ;
   - Manipulation de frames `NTRM1` (réordonnancement, duplication, troncature) ;
   - Rejet `PwaSyncEngine` sur enveloppe cross-vault et `server_seq` non monotone.

---

## 9. Conditions bloquantes avant mise en production

Pour lever le statut `NON VALIDÉ` et autoriser la mise en production, exécuter impérativement dans cet ordre :

1. **Valider la suite FastAPI + Uvicorn sur hôte Linux cible ou runner CI :**
   ```bash
   sudo apt-get update && sudo apt-get install -y libsodium23
   python3 -m pip install -e "./sync-server[dev]"
   pytest -v sync-server/tests
   ```
   Attendu : **100 % PASS** (dont démarrage Uvicorn, persistance après redémarrage et `backup.sh`).
2. **Déclencher et valider le passage vert complet de `.github/workflows/ci.yml` sur GitHub Actions** (`push` / `pull_request` vers `main`), y compris `pip-audit` et la matrice Python `3.11` / `3.12`.
3. **Activer les règles de protection de branche `main`** décrites en section 3.2.
4. **Configurer l'environnement serveur cible (`NOTEOR_ENV=production`, `NOTEOR_CORS_ORIGINS` explicite, HTTPS via Cloudflare Tunnel / Tailscale Serve / Caddy + rate-limiting proxy)** et effectuer un test de restauration à blanc selon `docs/sync/NOTEOR_SYNC_OPERATIONS.md`.
5. **Intégrer `sync/desktop/` dans le dépôt Desktop PyQt6 complet** et exécuter un essai réel de bout en bout (Desktop PyQt6 ↔ `sync-server` Uvicorn/Docker ↔ PWA déployée sur Cloudflare Pages).

---

## 10. Risques résiduels acceptés & feuille de route V1.2

- **Risques résiduels acceptés pour V1.1 :**
  - Protection anti-abus HTTP (rate-limiting par IP, quotas disque globaux, expiration TTL automatique des `.bin.part`) confiée à la couche proxy/infrastructure (Cloudflare / Caddy / système hôte) + surveillance disque.
  - Jetons `device_access_token` conservés localement sur les appareils clients pour permettre la synchronisation asynchrone (l'utilisateur doit révoquer immédiatement tout appareil perdu depuis un autre appareil du coffre et déclencher une rotation d'époque).
- **Recommandations prioritaires pour V1.2 :**
  1. **Signature Ed25519 des enveloppes `epoch_keys`** (`SEC-03`) pour éliminer toute substitution de clé d'époque par un serveur compromis.
  2. **Engagement serveur vérifiable sur `confirm_pairing`** (`SEC-04`) pour empêcher toute course à la confirmation si `(vault_id, pairing_id, device_b_id)` est observé.
  3. **Tâche périodique serveur de purge `.bin.part`** expirés (> 24 h) et métriques Prometheus/compteurs sans PII.
  4. **Mise à jour des devDependencies de build JS** (`sharp`, `postcss-selector-parser`, `glob`) dès disponibilité de versions compatibles non vulnérables dans l'arbre `vite-plugin-pwa`/`tailwindcss`.
