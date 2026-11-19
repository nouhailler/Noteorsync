# NOTEOR_SYNC_PROTOCOL.md

**Spécification du protocole de synchronisation Noteor**  
Version : 1.0 — 19 novembre 2026  
Statut : contrat technique de référence — proposition normative à valider par les deux implémentations avant développement  
Périmètre : Noteor Desktop (Python / PyQt6 / SQLite), Noteor PWA (React / TypeScript / Dexie / IndexedDB), Noteor Sync Server (FastAPI / SQLite)

---

## 1. Objet et portée

Ce document définit le protocole d'échange entre les différentes installations de Noteor.

Son objectif principal est de garantir que :

- Noteor Desktop et Noteor PWA produisent des opérations compatibles.
- Les deux clients peuvent interpréter les opérations produites par l'autre.
- Le serveur peut stocker, ordonner et redistribuer les opérations sans accéder au contenu en clair.
- Une synchronisation interrompue peut reprendre sans perte ni duplication logique.
- Les modifications concurrentes ne sont jamais écrasées silencieusement.
- Les pièces jointes peuvent être transférées indépendamment des opérations qui les référencent.
- Le fonctionnement local de Noteor reste possible sans serveur.

Ce document est normatif pour les échanges réseau. Les termes **DOIT**, **NE DOIT PAS**, **DEVRAIT**, **NE DEVRAIT PAS** et **PEUT** expriment respectivement une obligation, une interdiction, une recommandation, une recommandation négative et une possibilité.

### 1.1 Documents associés

| Document | Responsabilité |
|---|---|
| `NOTEOR_SYNC_ARCHITECTURE.md` | Architecture générale, responsabilités et décisions structurantes |
| `NOTEOR_SYNC_PROTOCOL.md` | Contrat d'échange exact entre les clients et le serveur |
| `NOTEOR_SYNC_DATA_MODEL.md` | Modèle métier commun détaillé, si nécessaire |
| `NOTEOR_SYNC_CRYPTO.md` | Spécification cryptographique détaillée et vecteurs de test |
| `NOTEOR_SYNC_TEST_VECTORS.md` | Jeux de données communs Python / TypeScript |

En cas de divergence entre les implémentations et ce document, le protocole documenté prévaut. Toute modification du contrat nécessite une nouvelle version documentée.

### 1.2 Ce que ce document ne définit pas intégralement

Il ne remplace pas :
- l'audit exhaustif des fonctions de mutation des deux applications ;
- la migration effective des bases existantes ;
- la conception détaillée de l'interface utilisateur ;
- la spécification complète du jumelage et de la récupération du coffre ;
- la politique détaillée de rotation des clés ;
- le schéma métier complet de chaque type d'entité Noteor.

Ces éléments doivent respecter le présent contrat lorsqu'ils interagissent avec la synchronisation.

---

## 2. Principes fondamentaux

Les deux implémentations DOIVENT respecter les principes suivants :

1. **Local-First** : la base locale est utilisable sans connexion au serveur.
2. **Serveur non fiable pour la confidentialité** : le serveur ne reçoit que des données chiffrées et des métadonnées techniques minimales.
3. **Journal d'opérations** : toute mutation synchronisable produit une opération durable.
4. **Ordre serveur** : le serveur attribue une séquence globale aux opérations acceptées.
5. **Identité globale** : les entités synchronisées sont identifiées par UUID, indépendamment des identifiants locaux.
6. **Idempotence** : une opération déjà acceptée ne doit pas être appliquée une seconde fois.
7. **Reprise** : les échanges peuvent être interrompus puis repris.
8. **Conservation des conflits** : deux modifications concurrentes ne doivent jamais entraîner une perte silencieuse.
9. **Chiffrement de bout en bout** : seul un client autorisé peut déchiffrer les contenus.
10. **Interopérabilité** : les mêmes données et les mêmes vecteurs de test doivent produire les mêmes résultats en Python et TypeScript.

---

## 3. Terminologie et identifiants

### 3.1 Identifiants

| Identifiant | Signification | Format |
|---|---|---|
| `vault_id` | Identifiant du coffre synchronisé | UUID v4 |
| `device_id` | Identifiant d'une installation Noteor autorisée | UUID v4 |
| `operation_id` | Identifiant global d'une opération | UUID v4 |
| `entity_id` | Identifiant global d'une entité métier | UUID v4 |
| `revision_id` | Identifiant d'une révision logique d'entité | UUID v4 |
| `media_id` | Identifiant global d'un média | UUID v4 |
| `upload_id` | Identifiant d'une session d'envoi de média | UUID v4 |

Les UUID DOIVENT être représentés en texte minuscule, au format canonique avec tirets.

Exemple :

`550e8400-e29b-41d4-a716-446655440000`

Les identifiants numériques historiques des bases Desktop et PWA peuvent être conservés comme identifiants locaux. Ils ne doivent jamais être utilisés comme identifiants réseau d'une entité synchronisée.

### 3.2 Séquences

Deux séquences distinctes existent :

- `device_seq` : numéro croissant attribué localement par un appareil à ses opérations.
- `server_seq` : numéro croissant attribué par le serveur à chaque opération acceptée dans un coffre.

Les séquences sont des entiers positifs représentés dans le JSON par des chaînes décimales afin d'éviter les limites de précision des nombres JavaScript.

Exemple :

`"device_seq": "184"`

`"server_seq": "9021"`

Une séquence ne doit jamais être réutilisée.

### 3.3 Horodatages

Tous les horodatages transmis dans les contenus métier utilisent le format RFC 3339 en UTC.

Exemple :

`2026-11-19T12:41:00.000Z`

Les dates de modification locales ne sont pas utilisées pour déterminer l'ordre des opérations réseau.

**Interdiction :** aucune implémentation ne doit considérer `updated_at` comme un curseur de synchronisation ou comme une preuve suffisante de l'ordre des modifications.

---

## 4. Modèle général de synchronisation

La synchronisation repose sur un journal append-only.

Chaque client :
1. effectue une modification dans sa base locale ;
2. crée une opération de synchronisation ;
3. chiffre son contenu ;
4. enregistre l'opération dans son journal local ;
5. transmet les opérations en attente au serveur ;
6. récupère les opérations distantes depuis son dernier curseur ;
7. déchiffre et applique les opérations dans sa base locale ;
8. met à jour son curseur après validation locale.

Le serveur :
1. authentifie l'appareil ;
2. vérifie la structure et les limites des enveloppes ;
3. vérifie l'idempotence et les séquences de l'appareil ;
4. enregistre les opérations ;
5. attribue un `server_seq` ;
6. rend les opérations disponibles aux autres appareils autorisés.

Le serveur ne déchiffre jamais le contenu métier.

### 4.1 Deux journaux distincts

**Journal local**

Il conserve les opérations produites par l'appareil, leur état d'envoi et les informations nécessaires à leur reprise.

**Journal serveur**

Il conserve les enveloppes chiffrées acceptées et leur séquence globale.

Le journal serveur n'est pas une copie de la base métier. Il n'est pas chargé de reconstruire les notes.

### 4.2 Pas de synchronisation directe des bases

Il est formellement interdit de synchroniser :
- le fichier SQLite de Noteor Desktop ;
- les fichiers WAL ou SHM ;
- la base IndexedDB de la PWA ;
- les fichiers internes de Dexie.

Les clients échangent exclusivement des opérations conformes au présent protocole et des objets médias chiffrés.

---

## 5. Enveloppe réseau d'une opération

### 5.1 Structure JSON normative

Une opération transmise au serveur utilise exactement la structure suivante :

```json
{
  "protocol_version": 1,
  "crypto_version": 1,
  "vault_id": "550e8400-e29b-41d4-a716-446655440000",
  "device_id": "550e8400-e29b-41d4-a716-446655440001",
  "operation_id": "550e8400-e29b-41d4-a716-446655440002",
  "device_seq": "184",
  "nonce": "BASE64URL_NONCE",
  "ciphertext": "BASE64URL_CIPHERTEXT"
}
```

Tous les champs sont obligatoires.

Aucun champ métier en clair ne doit être ajouté à cette enveloppe.

Le serveur ajoute `server_seq` lors de l'acceptation. Ce champ est renvoyé lors de la récupération des opérations ; il ne fait pas partie du contenu chiffré initial.

### 5.2 Encodage

- JSON UTF-8.
- Noms de propriétés en `snake_case`.
- UUID en minuscules.
- Séquences en chaînes décimales sans signe ni zéro initial inutile.
- Données binaires en Base64 URL-safe sans remplissage (`=`).
- Aucun champ supplémentaire non défini par la version du protocole.

Les deux clients DOIVENT rejeter les encodages invalides.

### 5.3 Taille maximale

Pour la V1 :

- Taille maximale d'une enveloppe JSON : 2 Mio.
- Taille maximale du contenu déchiffré d'une opération : 1,5 Mio.
- Nombre maximal d'opérations par lot : 100.
- Taille maximale d'une réponse de récupération : 100 opérations.

Une opération dépassant ces limites doit être refusée avec une erreur explicite. Les médias ne doivent pas être intégrés directement dans le contenu d'une opération.

---

## 6. Chiffrement des opérations

### 6.1 Algorithme retenu pour la V1

Algorithme candidat normatif à confirmer par le prototype d'interopérabilité :

**XChaCha20-Poly1305-IETF via libsodium.**

Paramètres attendus :
- clé : 32 octets ;
- nonce : 24 octets ;
- tag d'authentification : 16 octets ;
- sortie : ciphertext contenant le tag d'authentification.

La bibliothèque doit être utilisée via une API maintenue et reconnue :
- Python : binding compatible libsodium, par exemple PyNaCl ;
- TypeScript : binding compatible libsodium, par exemple libsodium.js.

Les deux implémentations doivent appeler des fonctions cryptographiques équivalentes et respecter exactement les mêmes paramètres.

**Important :** le nom de l'algorithme ne suffit pas à garantir l'interopérabilité. Le prototype Python/TypeScript et les vecteurs de test communs sont un prérequis à la validation de cette section.

### 6.2 Clé de chiffrement

Chaque coffre possède un secret cryptographique indépendant de l'identité d'un appareil.

La clé utilisée pour chiffrer les opérations doit être dérivée ou obtenue à partir du matériel cryptographique du coffre selon `NOTEOR_SYNC_CRYPTO.md`.

Les règles suivantes sont impératives :
- le serveur ne reçoit jamais la clé de chiffrement ;
- le jeton d'accès serveur n'est jamais une clé de chiffrement ;
- un appareil révoqué ne doit plus pouvoir télécharger de nouvelles opérations ;
- une révocation ne rend pas automatiquement illisibles les données déjà déchiffrées ou copiées par cet appareil.

### 6.3 Nonce

Chaque chiffrement doit utiliser un nonce de 24 octets généré par un générateur cryptographiquement sûr.

Un nonce ne doit jamais être réutilisé avec la même clé.

Le nonce est transmis dans le champ `nonce`, encodé en Base64 URL-safe sans remplissage.

### 6.4 Données authentifiées associées (AAD)

Le chiffrement doit authentifier les métadonnées de routage pour empêcher leur modification.

L'AAD est constituée d'un objet JSON canonique RFC 8785 contenant exactement :

```json
{
  "crypto_version": 1,
  "device_id": "550e8400-e29b-41d4-a716-446655440001",
  "device_seq": "184",
  "operation_id": "550e8400-e29b-41d4-a716-446655440002",
  "protocol_version": 1,
  "vault_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

Les octets UTF-8 de cette représentation canonique sont utilisés comme AAD.

Les propriétés doivent être sérialisées selon RFC 8785. Il est interdit d'utiliser directement une sérialisation JSON native non normalisée comme AAD.

Le serveur ne vérifie pas l'AAD cryptographique : il ne possède pas la clé. Chaque client la reconstruit à partir des champs reçus et vérifie le tag lors du déchiffrement.

### 6.5 Format du ciphertext

Le champ `ciphertext` contient directement la sortie de l'algorithme AEAD :

`ciphertext || authentication_tag`

Le tag n'est pas transmis dans un champ séparé.

Une authentification invalide entraîne le rejet de l'opération côté client. Le client ne doit pas appliquer partiellement son contenu.

---

## 7. Contenu chiffré d'une opération

### 7.1 Structure commune

Le contenu déchiffré est un objet JSON UTF-8.

Structure obligatoire :

```json
{
  "payload_version": 1,
  "operation_type": "entity.upsert",
  "entity_type": "note",
  "entity_id": "550e8400-e29b-41d4-a716-446655440010",
  "revision_id": "550e8400-e29b-41d4-a716-446655440011",
  "parent_revision_ids": [
    "550e8400-e29b-41d4-a716-446655440012"
  ],
  "created_at": "2026-11-19T12:41:00.000Z",
  "data": {}
}
```

Les propriétés métier sont chiffrées. Le serveur ne les voit pas.

### 7.2 Types d'opérations

La V1 définit les types suivants :

| `operation_type` | Fonction |
|---|---|
| `entity.upsert` | Création ou modification d'une entité |
| `entity.delete` | Suppression logique d'une entité |
| `entity.restore` | Restauration d'une entité supprimée |
| `media.register` | Déclaration d'un média chiffré |
| `media.delete` | Suppression logique d'un média |

Une opération inconnue doit être conservée dans le journal local comme opération non interprétable et signalée à l'utilisateur ou au mécanisme de diagnostic. Elle ne doit jamais être interprétée comme une suppression ou une modification par défaut.

Un client qui ne comprend pas un type d'opération ne doit pas avancer son curseur au-delà de cette opération si cela rend impossible une application cohérente de l'état local. Il peut conserver l'enveloppe et signaler une incompatibilité de version.

### 7.3 Types d'entités

Les types initiaux sont :

- `note`
- `folder`
- `category`
- `tag`
- `relation`
- `attachment`

La liste est extensible, mais tout nouveau type doit être documenté et testé dans les deux clients avant utilisation.

### 7.4 Contenu de `data`

`data` contient un snapshot métier complet de l'entité à la révision indiquée.

Il ne contient pas seulement les champs modifiés.

Cette règle facilite :
- la reconstruction de l'état ;
- l'application idempotente ;
- la détection des conflits ;
- la migration de schéma ;
- l'interopérabilité entre clients.

Les champs exacts de chaque type d'entité seront définis dans le modèle de données commun. Les implémentations ne doivent pas inventer des champs réseau divergents.

Les propriétés propres à l'interface utilisateur ou au système d'exploitation ne doivent pas être synchronisées, sauf décision explicite du modèle commun.

### 7.5 Suppression logique

Exemple de suppression :

```json
{
  "payload_version": 1,
  "operation_type": "entity.delete",
  "entity_type": "note",
  "entity_id": "550e8400-e29b-41d4-a716-446655440010",
  "revision_id": "550e8400-e29b-41d4-a716-446655440013",
  "parent_revision_ids": [
    "550e8400-e29b-41d4-a716-446655440011"
  ],
  "created_at": "2026-11-19T12:45:00.000Z",
  "data": {
    "deleted_at": "2026-11-19T12:45:00.000Z"
  }
}
```

La suppression est un événement synchronisé. Elle ne doit pas provoquer immédiatement la suppression physique de l'entité.

### 7.6 Restauration

La restauration produit une nouvelle révision, avec une nouvelle `revision_id` et une référence à la révision parente.

Elle ne réutilise jamais l'identifiant d'une ancienne opération.

### 7.7 Relations entre entités

Les relations sont des entités synchronisables ou des données explicitement incluses dans les snapshots selon le modèle métier commun.

Les deux implémentations doivent adopter la même stratégie. Il est interdit que Desktop synchronise une relation comme une entité autonome tandis que la PWA l'encode uniquement dans le snapshot d'une note sans règle de correspondance documentée.

Les références à d'autres entités utilisent exclusivement leurs UUID globaux.

---

## 8. Révisions et résolution des conflits

### 8.1 Révisions logiques

Chaque mutation synchronisable produit :
- un nouveau `revision_id` ;
- une liste `parent_revision_ids` ;
- un snapshot complet de l'entité.

Une création possède une liste de parents vide.

Une modification simple possède normalement un parent.

Une fusion peut posséder plusieurs parents.

### 8.2 Détection des modifications concurrentes

Deux révisions sont concurrentes lorsqu'elles descendent d'une même révision et qu'aucune n'est l'ancêtre de l'autre.

Exemple :

```text
              R1
             /  \
           R2    R3
```

R2 et R3 sont concurrentes.

Le protocole ne donne pas la priorité à la dernière date, au dernier appareil ou à la dernière opération reçue.

### 8.3 Règle de conservation

Une implémentation ne doit jamais remplacer silencieusement une branche concurrente par une autre.

Le client doit :
1. conserver les deux révisions ;
2. déterminer si une fusion automatique est sûre ;
3. produire une nouvelle révision de fusion si elle est sûre ;
4. sinon conserver les deux versions et signaler le conflit.

Une fusion automatique ne doit être réalisée que sur des champs dont la sémantique est explicitement définie. Le contenu textuel d'une note ne doit pas être fusionné par concaténation arbitraire.

### 8.4 Révision de fusion

Une fusion produit une nouvelle opération `entity.upsert` avec :
- un nouvel `operation_id` ;
- un nouvel `revision_id` ;
- les deux révisions concurrentes dans `parent_revision_ids` ;
- le snapshot résultant.

### 8.5 Conflits non résolus

Un conflit non résolu doit rester visible localement.

Il ne doit pas empêcher la synchronisation des autres entités indépendantes.

Les détails de présentation du conflit relèvent de l'interface utilisateur, mais les deux clients doivent conserver les mêmes informations de filiation.

---

## 9. Journal local et transactionnalité

### 9.1 Règle fondamentale

La mutation métier et la création de l'opération locale doivent être atomiques.

Il est interdit d'enregistrer une modification métier synchronisable sans enregistrer dans la même transaction l'opération correspondante.

### 9.2 Desktop

Dans SQLite, la transaction doit inclure :
- la modification de l'entité ;
- la création de la révision ;
- l'incrément de `device_seq` ;
- la création de l'entrée du journal local.

### 9.3 PWA

Dans Dexie/IndexedDB, la transaction doit inclure :
- la modification de l'entité ;
- la création de la révision ;
- l'incrément de `device_seq` ;
- l'insertion de l'opération dans le journal local.

Les tables concernées doivent être incluses dans une même transaction Dexie.

### 9.4 États locaux d'une opération

Le journal local peut utiliser les états suivants :

| État | Signification |
|---|---|
| `pending` | Opération créée, pas encore confirmée par le serveur |
| `sending` | Envoi en cours |
| `acknowledged` | Acceptée par le serveur |
| `blocked` | Envoi impossible sans intervention |
| `invalid` | Opération invalide détectée localement |

Une opération `acknowledged` ne doit plus être renvoyée, sauf mécanisme explicite de réconciliation.

### 9.5 Persistance

Le journal doit survivre :
- à la fermeture de l'application ;
- au redémarrage du système ;
- à une coupure réseau ;
- à une interruption de l'envoi ;
- à un redémarrage du serveur.

---

## 10. Séquences et idempotence

### 10.1 Attribution de `device_seq`

Chaque appareil maintient un compteur strictement croissant.

Chaque nouvelle opération reçoit la prochaine valeur.

Le compteur et l'opération doivent être persistés dans la même transaction que la mutation métier.

### 10.2 Unicité

Le couple suivant est unique :

`(vault_id, device_id, device_seq)`

L'identifiant `operation_id` est également unique dans le coffre.

### 10.3 Rejeu d'une opération

Si le serveur reçoit une opération déjà acceptée avec le même `operation_id` et le même contenu exact, il répond avec le résultat initial : l'opération est reconnue comme déjà acceptée.

Si le même `operation_id` est présenté avec un contenu différent, le serveur répond `409 OPERATION_ID_REUSE`.

Si le même couple appareil/séquence est présenté avec un contenu différent, le serveur répond `409 DEVICE_SEQUENCE_REUSE`.

### 10.4 Trou dans les séquences

Pour un appareil donné, le serveur attend les opérations dans l'ordre croissant.

Si la dernière séquence acceptée est 184 :
- 185 peut être acceptée ;
- 186 peut être acceptée après 185 ;
- 187 ne peut pas être acceptée avant 186.

Une opération manquante entraîne une réponse `409 DEVICE_SEQUENCE_GAP` indiquant la prochaine séquence attendue.

Les clients doivent conserver les opérations en attente jusqu'à confirmation.

### 10.5 Réinstallation ou perte de base locale

Un appareil ne doit jamais reprendre un ancien `device_id` avec un compteur réinitialisé.

Une réinstallation ou une perte irrécupérable du journal local nécessite un nouvel appareil logique et un nouveau jumelage, sauf procédure de récupération explicitement définie.

---

## 11. API HTTP

### 11.1 Principes

- HTTPS obligatoire.
- Préfixe API : `/api/v1`.
- Authentification par jeton d'appareil.
- Format JSON pour les opérations.
- UTF-8.
- Réponses d'erreur normalisées.
- Aucun endpoint ne retourne de contenu métier déchiffré.

Les exemples suivants utilisent un `vault_id` UUID.

### 11.2 Authentification

Les requêtes authentifiées utilisent :

```http
Authorization: Bearer DEVICE_ACCESS_TOKEN
```

Le jeton :
- est propre à un appareil ;
- est révocable ;
- n'est pas dérivé de la clé de chiffrement du coffre ;
- ne doit pas être stocké dans le journal des opérations ;
- ne doit pas être envoyé dans les URL.

La gestion complète de la création, du renouvellement et de la révocation des jetons est définie dans le protocole de jumelage et d'identité.

### 11.3 Vérification de connexion

`GET /api/v1/health`

Réponse :

```json
{
  "status": "ok",
  "api_version": 1
}
```

Cet endpoint ne nécessite pas de clé de coffre et ne révèle aucune donnée métier.

### 11.4 Informations du coffre

`GET /api/v1/vaults/{vault_id}`

Réponse indicative :

```json
{
  "vault_id": "550e8400-e29b-41d4-a716-446655440000",
  "protocol_version": 1,
  "status": "active"
}
```

Le serveur ne retourne aucune clé ni donnée métier.

### 11.5 Envoi d'opérations

`POST /api/v1/vaults/{vault_id}/operations:batch`

Corps :

```json
{
  "operations": [
    {
      "protocol_version": 1,
      "crypto_version": 1,
      "vault_id": "550e8400-e29b-41d4-a716-446655440000",
      "device_id": "550e8400-e29b-41d4-a716-446655440001",
      "operation_id": "550e8400-e29b-41d4-a716-446655440002",
      "device_seq": "184",
      "nonce": "BASE64URL_NONCE",
      "ciphertext": "BASE64URL_CIPHERTEXT"
    }
  ]
}
```

Réponse :

```json
{
  "accepted": [
    {
      "operation_id": "550e8400-e29b-41d4-a716-446655440002",
      "device_seq": "184",
      "server_seq": "9021",
      "status": "accepted"
    }
  ],
  "next_expected_device_seq": "185"
}
```

Règles :
- maximum 100 opérations par lot ;
- opérations d'un même appareil ;
- séquences contiguës ;
- validation de chaque enveloppe avant insertion ;
- transaction serveur garantissant qu'un lot est accepté entièrement ou refusé entièrement ;
- aucune opération ne peut être modifiée après acceptation.

Un client ne doit pas supposer qu'un timeout signifie que l'opération a été refusée. Il doit réessayer avec les mêmes identifiants et le même contenu.

### 11.6 Récupération des opérations

`GET /api/v1/vaults/{vault_id}/operations?after={cursor}&limit=100`

Paramètres :
- `after` : curseur serveur exclusif ;
- `limit` : entier de 1 à 100.

Le curseur initial est `"0"`.

Réponse :

```json
{
  "operations": [
    {
      "server_seq": "9021",
      "envelope": {
        "protocol_version": 1,
        "crypto_version": 1,
        "vault_id": "550e8400-e29b-41d4-a716-446655440000",
        "device_id": "550e8400-e29b-41d4-a716-446655440001",
        "operation_id": "550e8400-e29b-41d4-a716-446655440002",
        "device_seq": "184",
        "nonce": "BASE64URL_NONCE",
        "ciphertext": "BASE64URL_CIPHERTEXT"
      }
    }
  ],
  "next_cursor": "9021",
  "has_more": false
}
```

Règles :
- ordre strictement croissant de `server_seq` ;
- `after` est exclusif ;
- les opérations ne sont pas filtrées selon leur appareil d'origine ;
- le client peut recevoir ses propres opérations ;
- le client doit gérer les opérations déjà connues ;
- le curseur retourné ne doit être persisté qu'après application durable des opérations récupérées.

Le serveur peut retourner une page vide si aucune nouvelle opération n'existe. Dans ce cas, le curseur ne doit pas régresser.

### 11.7 Confirmation de progression

Le curseur local est une responsabilité du client.

La V1 ne nécessite pas d'endpoint de confirmation du curseur : le serveur conserve le journal et le client mémorise son propre `last_server_cursor`.

Une future politique de rétention devra être conçue séparément. Aucun client ne doit supposer que les anciennes opérations sont supprimables dès qu'il les a téléchargées.

---

## 12. Application des opérations reçues

Le client doit appliquer une page d'opérations de manière durable.

Pour chaque opération :
1. vérifier la version du protocole ;
2. vérifier les champs obligatoires ;
3. reconstruire l'AAD ;
4. vérifier et déchiffrer le ciphertext ;
5. valider le JSON déchiffré ;
6. vérifier les UUID et les types ;
7. vérifier les versions de payload ;
8. enregistrer l'opération reçue ;
9. appliquer ou mettre en attente son effet métier ;
10. mettre à jour le curseur dans la même transaction locale que l'enregistrement durable de la page ou du segment traité.

Une erreur de déchiffrement ne doit jamais conduire à l'application d'un contenu partiel.

### 12.1 Opération déjà appliquée

Si `operation_id` est déjà connu localement, l'opération ne doit pas être appliquée une seconde fois.

Le client peut néanmoins avancer son curseur si l'opération a déjà été durablement enregistrée et traitée.

### 12.2 Opération inconnue

Si l'opération est cryptographiquement valide mais utilise un type non supporté :
- conserver l'enveloppe ;
- signaler l'incompatibilité ;
- ne pas interpréter l'opération ;
- ne pas perdre l'information ;
- ne pas avancer le curseur au-delà si cela empêche une application cohérente.

### 12.3 Références manquantes

Une opération peut référencer une entité qui n'est pas encore présente localement.

Le client doit conserver l'opération et résoudre la dépendance lorsque l'entité devient disponible.

Il ne doit pas créer une entité artificielle ou supprimer une référence pour contourner le problème.

---

## 13. Curseurs et synchronisation différentielle

Chaque appareil conserve un curseur serveur propre au coffre.

Exemple :

```json
{
  "vault_id": "550e8400-e29b-41d4-a716-446655440000",
  "last_server_cursor": "9021"
}
```

Le curseur représente la dernière séquence serveur dont le traitement local est durablement confirmé.

Il ne représente :
- ni une date ;
- ni le dernier `updated_at` ;
- ni la dernière opération locale ;
- ni la dernière séquence de l'appareil.

### 13.1 Ordre d'un cycle

Un cycle normal suit cet ordre :

1. envoyer les opérations locales en attente ;
2. traiter les confirmations serveur ;
3. récupérer les opérations distantes après le curseur local ;
4. déchiffrer et appliquer les opérations ;
5. persister les résultats et le nouveau curseur ;
6. recommencer tant que `has_more` vaut `true`.

Un client peut récupérer les opérations avant l'envoi local si une raison technique le justifie, mais il doit conserver les mêmes règles de cohérence et d'idempotence.

### 13.2 Synchronisation interrompue

Si l'application s'arrête pendant un cycle :
- les opérations locales non confirmées restent en attente ;
- les opérations distantes non appliquées restent récupérables ;
- le curseur ne doit pas avancer au-delà des opérations durablement traitées.

### 13.3 Synchronisation complète

Une synchronisation complète ne consiste pas à remplacer la base locale par une copie serveur.

Elle consiste à rejouer ou réconcilier les opérations nécessaires, puis à reconstruire les états locaux à partir des révisions et des branches connues.

Une reconstruction complète pourra être ajoutée ultérieurement comme procédure de réparation.

---

## 14. Synchronisation des médias

### 14.1 Principe

Les médias sont transférés séparément des opérations.

Les clients ne doivent jamais intégrer directement un fichier image, audio ou vidéo en clair dans une enveloppe d'opération.

Le contenu média est chiffré côté client avant transfert.

### 14.2 Métadonnées métier du média

Les métadonnées nécessaires à l'application sont incluses dans le contenu chiffré d'une opération `media.register`.

Structure indicative :

```json
{
  "payload_version": 1,
  "operation_type": "media.register",
  "entity_type": "attachment",
  "entity_id": "550e8400-e29b-41d4-a716-446655440020",
  "revision_id": "550e8400-e29b-41d4-a716-446655440021",
  "parent_revision_ids": [],
  "created_at": "2026-11-19T12:50:00.000Z",
  "data": {
    "media_id": "550e8400-e29b-41d4-a716-446655440022",
    "media_crypto_version": 1,
    "encrypted_size": 245760,
    "encrypted_sha256": "HEX_SHA256",
    "media_nonce": "BASE64URL_NONCE",
    "original_size": 245700,
    "mime_type": "image/jpeg",
    "file_name": "photo.jpg"
  }
}
```

Ces métadonnées sont chiffrées dans l'opération. Le serveur ne doit pas recevoir le nom du fichier, le MIME type ou la taille originale dans les métadonnées HTTP du média.

Le format exact de chiffrement des médias doit être fixé dans `NOTEOR_SYNC_CRYPTO.md` avant implémentation.

### 14.3 Identité et intégrité

Chaque média possède un `media_id` UUID.

L'empreinte SHA-256 porte sur le blob chiffré complet, pas sur le fichier en clair.

Le client vérifie :
- la taille chiffrée ;
- l'empreinte du blob chiffré ;
- l'authenticité du déchiffrement ;
- la cohérence des métadonnées après déchiffrement.

### 14.4 Envoi reprenable

Le transfert utilise des sessions d'envoi.

Création :

`POST /api/v1/vaults/{vault_id}/media`

Corps :

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440022",
  "encrypted_size": 245760,
  "encrypted_sha256": "HEX_SHA256"
}
```

Réponse :

```json
{
  "upload_id": "550e8400-e29b-41d4-a716-446655440023",
  "chunk_size": 4194304,
  "received_bytes": 0,
  "status": "uploading"
}
```

Le client transmet ensuite les fragments :

`PUT /api/v1/vaults/{vault_id}/media/{media_id}/uploads/{upload_id}`

En-têtes :

```http
Content-Type: application/octet-stream
Content-Range: bytes 0-245759/245760
```

La taille maximale d'un fragment est de 4 Mio. Le dernier fragment peut être plus petit.

Le serveur doit :
- vérifier les offsets ;
- refuser les fragments incohérents ;
- permettre la reprise à partir du nombre d'octets déjà reçus ;
- ne pas publier le média avant validation complète ;
- vérifier la taille et le SHA-256 final.

Finalisation :

`POST /api/v1/vaults/{vault_id}/media/{media_id}/uploads/{upload_id}/complete`

Réponse :

```json
{
  "media_id": "550e8400-e29b-41d4-a716-446655440022",
  "status": "available",
  "encrypted_size": 245760,
  "encrypted_sha256": "HEX_SHA256"
}
```

### 14.5 Récupération d'un média

`GET /api/v1/vaults/{vault_id}/media/{media_id}`

Le serveur renvoie le blob chiffré uniquement.

Le client vérifie son intégrité avant déchiffrement et stockage local.

### 14.6 Médias manquants

Une opération `media.register` peut être téléchargée avant la fin de l'envoi du média.

Le client doit conserver l'état « média en attente » et réessayer sa récupération.

Une note ne doit pas être invalidée simplement parce que son média n'est pas encore disponible.

### 14.7 Suppression des médias

La suppression d'un média suit un mécanisme logique synchronisé.

La suppression physique serveur ne doit être réalisée qu'après une politique de rétention et de sécurité définie. Le serveur ne peut pas déduire seul qu'un média n'est plus référencé par aucune note, puisque ces références sont chiffrées.

---

## 15. Gestion des erreurs

### 15.1 Format commun

Toutes les erreurs API utilisent la structure :

```json
{
  "error": {
    "code": "DEVICE_SEQUENCE_GAP",
    "message": "An operation sequence is missing.",
    "details": {
      "expected_device_seq": "185"
    }
  }
}
```

`message` est destiné au diagnostic. Le code `error.code` est la référence stable utilisée par les clients.

Les clients ne doivent pas dépendre du texte de `message`.

### 15.2 Codes d'erreur

| HTTP | Code | Signification |
|---:|---|---|
| 400 | `INVALID_REQUEST` | Requête mal formée |
| 400 | `INVALID_ENVELOPE` | Enveloppe invalide |
| 400 | `INVALID_CURSOR` | Curseur invalide |
| 401 | `AUTHENTICATION_REQUIRED` | Jeton absent ou invalide |
| 403 | `DEVICE_REVOKED` | Appareil révoqué |
| 403 | `VAULT_ACCESS_DENIED` | Accès au coffre refusé |
| 404 | `VAULT_NOT_FOUND` | Coffre inexistant |
| 404 | `MEDIA_NOT_FOUND` | Média absent |
| 409 | `OPERATION_ID_REUSE` | Réutilisation d'un identifiant d'opération avec un contenu différent |
| 409 | `DEVICE_SEQUENCE_REUSE` | Réutilisation d'une séquence avec un contenu différent |
| 409 | `DEVICE_SEQUENCE_GAP` | Séquence manquante |
| 409 | `MEDIA_UPLOAD_CONFLICT` | Session ou fragment incompatible |
| 413 | `PAYLOAD_TOO_LARGE` | Taille dépassée |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | Type de contenu HTTP non supporté |
| 422 | `UNSUPPORTED_PROTOCOL_VERSION` | Version de protocole non supportée |
| 429 | `RATE_LIMITED` | Limitation de débit |
| 500 | `INTERNAL_ERROR` | Erreur serveur |
| 503 | `SERVICE_UNAVAILABLE` | Service temporairement indisponible |

### 15.3 Comportement client

- `401` : suspendre la synchronisation et demander une réauthentification.
- `403 DEVICE_REVOKED` : désactiver la synchronisation de cet appareil.
- `409 DEVICE_SEQUENCE_GAP` : réorganiser l'envoi à partir de la séquence attendue, sans supprimer les opérations locales.
- `409 OPERATION_ID_REUSE` : bloquer l'opération et déclencher un diagnostic.
- `413` : ne pas réessayer sans correction.
- `429`, `500`, `503` : réessayer avec temporisation exponentielle et jitter.
- erreur réseau ou timeout : réessayer avec les mêmes identifiants et le même ciphertext.

Un client ne doit jamais supprimer une opération locale simplement parce que l'envoi a échoué.

---

## 16. Versions et compatibilité

### 16.1 Versions distinctes

Le protocole distingue :
- `protocol_version` : format des échanges et règles réseau ;
- `crypto_version` : format cryptographique des enveloppes ;
- `payload_version` : structure du contenu déchiffré ;
- `media_crypto_version` : format cryptographique des médias.

Ces versions ne doivent pas être confondues.

### 16.2 Règles de compatibilité

Un serveur V1 :
- accepte uniquement `protocol_version = 1` ;
- accepte uniquement les versions cryptographiques explicitement prises en charge ;
- conserve les enveloppes sans en interpréter le contenu ;
- refuse les versions réseau inconnues.

Un client :
- valide les versions avant application ;
- conserve les données inconnues lorsqu'il le peut ;
- ne transforme jamais une version inconnue en opération vide ;
- ne supprime jamais une opération inconnue.

### 16.3 Évolution

Toute modification incompatible exige une nouvelle version majeure du protocole.

Une évolution additive peut être réalisée dans une nouvelle version de payload si les règles de compatibilité sont explicitement définies.

Aucun champ facultatif ne doit être ajouté à l'enveloppe V1 sans décision de version documentée : l'enveloppe réseau est volontairement stricte.

---

## 17. Sécurité et confidentialité

### 17.1 Données visibles par le serveur

Le serveur peut connaître :
- les identifiants opaques du coffre et des appareils ;
- les identifiants d'opérations ;
- les séquences ;
- les tailles des enveloppes ;
- les dates de réception ;
- les tailles et empreintes des médias chiffrés ;
- les informations techniques nécessaires à l'authentification et au transport.

Il ne doit pas connaître :
- le titre des notes ;
- le contenu des notes ;
- les tags ;
- les noms de dossiers ;
- les catégories ;
- les noms de fichiers ;
- les contenus des médias ;
- les clés cryptographiques.

Les identifiants et les séquences constituent déjà des métadonnées. Il ne faut pas ajouter de métadonnées métier en clair sans justification.

### 17.2 Transport

HTTPS est obligatoire, même lorsque le serveur est accessible uniquement via WireGuard ou Tailscale.

Le VPN réduit l'exposition réseau, mais ne remplace pas le chiffrement TLS.

### 17.3 Secrets locaux

Les clés et secrets doivent être protégés par les mécanismes locaux adaptés à chaque plateforme.

Ils ne doivent pas être :
- inscrits dans les logs ;
- inclus dans les exports de diagnostic ;
- enregistrés dans le dépôt Git ;
- placés dans les URL ;
- transmis au serveur de synchronisation.

### 17.4 Limites de l'E2E

Le chiffrement E2E protège les données contre la lecture par le serveur.

Il ne protège pas contre :
- un appareil compromis ;
- un malware local ;
- une extension de navigateur malveillante ;
- du code PWA compromis livré au navigateur ;
- une personne ayant accès à une session locale déverrouillée.

La PWA doit donc être servie depuis une source de confiance et ses dépendances doivent être maîtrisées.

### 17.5 Logs

Les logs serveur et clients ne doivent jamais contenir :
- de contenu de note ;
- de contenu de média ;
- de clé ;
- de nonce associé à une clé dans un contexte qui compromettrait la sécurité ;
- de jeton d'accès ;
- de ciphertext complet, sauf outil de diagnostic explicitement sécurisé et désactivé par défaut.

Les logs peuvent contenir des identifiants techniques et des codes d'erreur, avec une politique de rétention limitée.

---

## 18. Adaptations obligatoires des clients

### 18.1 Noteor Desktop — Python / SQLite

L'implémentation Desktop doit fournir :
- un identifiant UUID stable par entité synchronisée ;
- un identifiant d'appareil persistant ;
- un compteur `device_seq` persistant ;
- un journal local transactionnel ;
- une gestion des révisions et des parents ;
- un moteur de chiffrement compatible avec les vecteurs communs ;
- un client HTTP ;
- un curseur serveur persistant ;
- une gestion des conflits ;
- un transfert de médias chiffrés.

Les modifications doivent s'intégrer au fonctionnement actuel de Noteor.

Il ne faut pas réécrire le modèle métier ou l'interface PyQt6 pour introduire la synchronisation.

### 18.2 Noteor PWA — TypeScript / Dexie

L'implémentation PWA doit fournir les mêmes fonctions, avec :
- stockage UUID ;
- journal transactionnel Dexie ;
- compteur local persistant ;
- chiffrement compatible ;
- client HTTP ;
- curseur serveur ;
- résolution des conflits ;
- transfert des médias chiffrés ;
- reprise après fermeture du navigateur ou perte de réseau.

Le Service Worker et le mode offline-first doivent continuer à fonctionner lorsque le serveur est indisponible.

### 18.3 Identité des entités

Les deux clients doivent utiliser le même UUID pour une même entité après synchronisation.

Les identifiants numériques locaux peuvent rester différents.

Chaque base locale doit conserver une correspondance entre UUID global et identifiant local lorsque cela est nécessaire.

### 18.4 Wikilinks

Les références inter-notes doivent progressivement s'appuyer sur les UUID globaux.

Le format historique fondé sur les identifiants numériques doit rester lisible pendant la migration.

La conversion ne doit pas casser les liens existants ni les références intégrées au contenu.

Le format définitif des Wikilinks est à documenter dans le modèle de données commun.

---

## 19. Serveur Noteor Sync

### 19.1 Responsabilités

Le serveur FastAPI est un relais de synchronisation chiffré.

Il est responsable de :
- l'authentification des appareils ;
- l'autorisation d'accès aux coffres ;
- la validation structurelle des enveloppes ;
- l'idempotence ;
- l'attribution des séquences serveur ;
- le stockage du journal chiffré ;
- la distribution des opérations ;
- le stockage des blobs médias chiffrés ;
- la gestion des transferts reprenables ;
- la révocation des appareils.

Il n'est pas responsable de :
- déchiffrer les notes ;
- résoudre les conflits métier ;
- fusionner les contenus ;
- interpréter les tags ou les dossiers ;
- reconstruire les bases locales.

### 19.2 Stockage

Pour la V1 :
- SQLite en mode WAL pour les métadonnées et le journal ;
- fichiers pour les blobs médias chiffrés ;
- stockage des fichiers hors du répertoire public ;
- sauvegardes serveur protégées ;
- aucune dépendance obligatoire à PostgreSQL, Redis ou Kubernetes.

### 19.3 Tables serveur indicatives

Les tables suivantes décrivent des responsabilités, pas une obligation de reprendre exactement ces noms :

- coffres ;
- appareils autorisés ;
- jetons ou empreintes de jetons ;
- opérations ;
- séquences par appareil ;
- sessions d'upload ;
- médias ;
- séquence globale par coffre.

Les opérations doivent être append-only.

### 19.4 Cohérence transactionnelle

L'insertion d'une opération, la réservation de son `server_seq` et la mise à jour de la dernière séquence acceptée de l'appareil doivent être atomiques.

Une panne serveur ne doit pas produire une séquence annoncée comme acceptée alors que l'opération n'est pas durablement enregistrée.

---

## 20. Tests d'interopérabilité obligatoires

Les tests communs sont une condition de validation du protocole.

### 20.1 Tests cryptographiques

Les deux clients doivent réussir les mêmes tests :
- Python chiffre, TypeScript déchiffre ;
- TypeScript chiffre, Python déchiffre ;
- AAD identique ;
- nonce valide ;
- ciphertext modifié ;
- tag invalide ;
- mauvaise clé ;
- mauvais UUID de coffre ;
- mauvaise séquence ;
- Base64 invalide ;
- JSON invalide.

Les vecteurs doivent inclure :
- les entrées exactes ;
- la clé de test ;
- le nonce ;
- l'AAD canonique ;
- le ciphertext attendu ;
- le plaintext attendu.

Les clés et nonces des vecteurs sont exclusivement des valeurs de test publiques, jamais réutilisées en production.

### 20.2 Tests d'opérations

Les deux clients doivent pouvoir :
- créer une note ;
- modifier une note ;
- supprimer une note ;
- restaurer une note ;
- créer un dossier ;
- créer un tag ;
- modifier une relation ;
- synchroniser une pièce jointe ;
- recevoir une opération déjà connue ;
- traiter deux révisions concurrentes ;
- fusionner une révision ;
- conserver un conflit non résolu.

### 20.3 Tests réseau

Les scénarios suivants sont obligatoires :
- serveur indisponible ;
- timeout après réception serveur ;
- retry d'un lot ;
- coupure pendant un upload ;
- reprise d'un upload ;
- doublon exact ;
- réutilisation d'identifiant avec contenu différent ;
- trou dans les séquences ;
- récupération paginée ;
- redémarrage client ;
- redémarrage serveur ;
- appareil révoqué.

### 20.4 Tests de non-régression

Les fonctionnalités locales existantes doivent rester utilisables :
- création et édition de notes ;
- recherche ;
- tags et dossiers ;
- catégories ;
- pièces jointes ;
- Wikilinks ;
- import/export ;
- fonctionnement hors ligne.

La synchronisation ne doit pas devenir une dépendance pour utiliser Noteor.

### 20.5 Tests de bout en bout

Un scénario E2E complet doit être automatisé :

1. créer une note dans Desktop ;
2. synchroniser vers le serveur ;
3. récupérer dans la PWA ;
4. vérifier le contenu après déchiffrement ;
5. modifier la note dans la PWA ;
6. synchroniser ;
7. récupérer dans Desktop ;
8. vérifier l'identité UUID ;
9. vérifier les médias ;
10. couper le réseau et vérifier le fonctionnement local ;
11. rétablir le réseau et vérifier la reprise ;
12. créer deux modifications concurrentes et vérifier la conservation des deux branches.

Le scénario doit être exécuté en CI.

---

## 21. Critères d'acceptation du protocole

Le protocole V1 n'est considéré comme implémentable que lorsque les conditions suivantes sont remplies :

- [ ] Les structures JSON réseau sont figées.
- [ ] Les règles d'encodage sont identiques en Python et TypeScript.
- [ ] Les UUID et les séquences sont définis.
- [ ] Les règles d'idempotence sont testables.
- [ ] L'ordre des opérations est défini.
- [ ] Le format AAD est identique.
- [ ] Le format AEAD est validé par des vecteurs croisés.
- [ ] Le contenu métier est chiffré.
- [ ] Les types d'opérations sont définis.
- [ ] Le modèle des révisions et des conflits est compris des deux clients.
- [ ] Le format des réponses de l'API est identique.
- [ ] Les erreurs et leurs codes sont définis.
- [ ] Le curseur est indépendant des dates.
- [ ] Le transfert des médias est reprenable.
- [ ] La révocation d'appareil est prise en compte.
- [ ] Les tests de compatibilité Python/TypeScript passent.
- [ ] Le serveur ne peut pas déchiffrer les données.
- [ ] Le mode local fonctionne sans serveur.

---

## 22. Décisions restant à valider avant développement

Les points suivants ne doivent pas être considérés comme implicitement résolus.

### 22.1 Cryptographie

- Valider XChaCha20-Poly1305 avec libsodium dans les deux environnements.
- Valider la disponibilité et la maintenance des bindings Python et navigateur.
- Définir précisément la dérivation des clés, les paramètres Argon2id et les enveloppes de clés dans `NOTEOR_SYNC_CRYPTO.md`.
- Définir le chiffrement des médias et leur gestion des nonces.
- Définir la procédure de récupération du coffre.
- Définir le jumelage et la révocation avec rotation éventuelle des secrets.

### 22.2 Modèle métier

- Finaliser les champs communs des notes.
- Finaliser les champs des dossiers, catégories et tags.
- Décider si les relations sont des entités indépendantes ou des éléments des snapshots.
- Définir les règles d'intégrité référentielle.
- Définir le comportement des suppressions en cascade.
- Définir les règles exactes des Wikilinks.

### 22.3 Conflits

- Définir les champs pouvant être fusionnés automatiquement.
- Définir les règles de fusion des collections.
- Définir la présentation et les actions de résolution des conflits.
- Définir la conservation des révisions historiques.

### 22.4 Serveur et exploitation

- Définir la politique de rétention du journal.
- Définir la sauvegarde et la restauration du serveur.
- Définir les limites de stockage et de débit.
- Définir la gestion du renouvellement des jetons.
- Définir le comportement si un appareil reste longtemps hors ligne.

### 22.5 Compatibilité

- Définir la politique de migration des anciennes bases.
- Définir le comportement des anciennes versions de Desktop et PWA.
- Définir les conditions de montée de version du protocole.

Aucune de ces décisions ne doit être remplacée par une hypothèse silencieuse dans le code.

---

## 23. Phasage d'implémentation recommandé

### Phase A — Contrat et prototypes

- Valider les structures réseau.
- Créer les modèles TypeScript et Python.
- Valider les UUID et les séquences.
- Réaliser le prototype cryptographique croisé.
- Générer les premiers vecteurs de test.
- Valider les sérialisations canoniques.

**Critère de sortie :** Python et TypeScript produisent et déchiffrent les mêmes vecteurs.

### Phase B — Journal local

- Inventorier toutes les mutations synchronisables.
- Introduire les UUID.
- Introduire les révisions.
- Introduire le journal local.
- Garantir l'atomicité des mutations.
- Ajouter les migrations de base.

**Critère de sortie :** chaque mutation synchronisable génère une opération durable dans les deux clients.

### Phase C — Serveur

- Implémenter FastAPI.
- Implémenter l'authentification.
- Implémenter le journal append-only.
- Implémenter les séquences.
- Implémenter l'idempotence.
- Implémenter les endpoints d'envoi et de récupération.

**Critère de sortie :** les opérations chiffrées sont acceptées et redistribuées sans déchiffrement serveur.

### Phase D — Synchronisation des clients

- Implémenter l'envoi différentiel.
- Implémenter la récupération paginée.
- Implémenter les curseurs.
- Implémenter la reprise après interruption.
- Implémenter la détection des opérations déjà connues.

**Critère de sortie :** Desktop et PWA échangent les mêmes opérations sans perte ni duplication logique.

### Phase E — Conflits et médias

- Implémenter le graphe de révisions.
- Implémenter la conservation des branches concurrentes.
- Implémenter les transferts médias chiffrés.
- Implémenter la reprise des uploads.
- Implémenter la vérification d'intégrité.

**Critère de sortie :** les conflits et les médias sont correctement gérés dans les deux directions.

### Phase F — Durcissement

- Tests de sécurité.
- Tests de panne.
- Tests de charge raisonnables.
- Tests E2E complets.
- CI Python et TypeScript.
- Documentation d'installation serveur.
- Documentation de récupération et révocation.

**Critère de sortie :** la synchronisation est exploitable sans compromettre le fonctionnement local.

---

## 24. Règles impératives pour Codex

Tout agent de développement travaillant sur Noteor doit respecter les règles suivantes :

1. Lire `NOTEOR_SYNC_ARCHITECTURE.md` et `NOTEOR_SYNC_PROTOCOL.md` avant toute modification.
2. Auditer le code réel avant de créer ou modifier une fonction.
3. Ne pas supposer qu'un nom de méthode ou un champ proposé dans la documentation existe déjà.
4. Ne pas inventer un protocole parallèle.
5. Ne pas modifier le format réseau sans mettre à jour le document et les tests.
6. Ne pas implémenter séparément des formats Python et TypeScript.
7. Ne pas utiliser les dates de modification comme mécanisme de synchronisation.
8. Ne pas synchroniser les fichiers de base SQLite ou IndexedDB.
9. Ne pas supprimer physiquement une donnée avant propagation de sa suppression.
10. Ne jamais écraser silencieusement une révision concurrente.
11. Ne jamais transmettre de clé de chiffrement au serveur.
12. Ne jamais stocker de données métier en clair sur le serveur.
13. Ne pas supprimer les fonctionnalités locales existantes.
14. Ne pas réécrire inutilement les composants qui fonctionnent.
15. Ajouter des tests pour chaque règle du protocole implémentée.
16. Exécuter les tests des deux environnements lorsque le contrat commun est modifié.
17. Documenter les décisions non résolues au lieu de les trancher arbitrairement.
18. Fournir un rapport final indiquant les fichiers modifiés, les tests exécutés, leurs résultats et les limites restantes.

---

## 25. Principe final

Le protocole Noteor n'est pas une simple API permettant de copier des données entre deux appareils.

C'est un contrat d'interopérabilité entre deux applications locales indépendantes, utilisant des bases différentes, des environnements différents et des langages différents.

La règle centrale est donc :

**Une opération produite par Noteor Desktop doit pouvoir être comprise, vérifiée et appliquée par Noteor PWA — et inversement — sans que l'un des deux clients ait besoin de connaître les détails internes de l'autre.**

Le serveur ne doit être qu'un transporteur fiable du point de vue de l'intégrité et de l'ordre, mais non détenteur du secret des données.

Tant que les vecteurs cryptographiques, les schémas métier communs et les tests croisés ne sont pas validés, la synchronisation V1 ne doit pas être considérée comme prête pour une mise en production.

