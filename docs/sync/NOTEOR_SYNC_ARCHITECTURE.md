# NOTEOR SYNC — ARCHITECTURE TECHNIQUE DE RÉFÉRENCE

**Document :** `docs/sync/NOTEOR_SYNC_ARCHITECTURE.md`  
**Version :** 1.1 (texte original v1.0 préservé + annexe de réconciliation §25)  
**Statut :** Architecture cible — référence pour l'implémentation  
**Date :** 19 novembre 2026  
**Projet :** Noteor  
**Dépôt :** `nouhailler/Noteorsync` (`nouhailler/Noteor`)  
**Documents normatifs associés :** `NOTEOR_SYNC_PROTOCOL.md`, `NOTEOR_SYNC_CRYPTO.md`, `NOTEOR_SYNC_DATA_MODEL.md`, `NOTEOR_SYNC_CONFORMANCE_REPORT.md`

> **Note d'alignement v1.1 :** Ce document conserve intégralement le texte architectural d'origine (v1.0). Les ajustements normatifs arrêtés lors de l'audit de cohérence (`NOTEOR_SYNC_AUDIT_REPORT.md`, C1–C11) et de l'harmonisation v1.1 sont synthétisés dans l'[Annexe §25](#25-annexe-de-réconciliation-v11) et font foi dans `NOTEOR_SYNC_PROTOCOL.md`, `NOTEOR_SYNC_CRYPTO.md` et `NOTEOR_SYNC_DATA_MODEL.md`.

---

# 1. OBJET DU DOCUMENT

Ce document définit l'architecture technique cible de **Noteor Sync**, le futur mécanisme de synchronisation multi-appareils de Noteor.

L'objectif est de permettre à un utilisateur de retrouver ses notes et leurs pièces jointes sur plusieurs appareils, notamment :

- un ordinateur Linux exécutant Noteor Desktop ;
- un téléphone ou une tablette exécutant Noteor PWA ;
- éventuellement d'autres ordinateurs ou appareils compatibles dans les versions futures.

La synchronisation doit fonctionner avec un serveur personnel hébergé à domicile.

Les exigences fondamentales sont :

1. Préserver le fonctionnement local et hors ligne des deux applications.
2. Ne pas réécrire les applications existantes.
3. Introduire une synchronisation différentielle et résiliente.
4. Garantir un chiffrement de bout en bout (E2E).
5. Empêcher le serveur de lire le contenu des notes et des médias.
6. Gérer correctement les modifications concurrentes et les suppressions.
7. Permettre l'ajout et la révocation d'appareils.
8. Garantir la compatibilité entre les clients Python et TypeScript.
9. Prévoir une architecture évolutive, sans imposer de backend cloud commercial.
10. Conserver les possibilités d'export et de sauvegarde indépendamment de la synchronisation.

Ce document constitue la référence architecturale pour les futures interventions de développement.

Toute implémentation devra respecter ses décisions ou documenter explicitement les propositions de modification.

# 2. CONTEXTE TECHNIQUE EXISTANT

## 2.1 Organisation du dépôt

Le dépôt `nouhailler/Noteor` contient deux applications distinctes dans un même projet.

### Noteor Desktop

Technologies identifiées :

- Python ;
- PyQt6 ;
- SQLite ;
- stockage local des médias dans le système de fichiers.

Fichiers examinés lors de l'audit :

- `database.py`
- `config.py`
- `ui/editor.py`

Les données locales sont notamment organisées sous :

`~/.local/share/Noteor/`

Les médias sont stockés dans des répertoires locaux, notamment :

- `media/audio`
- `media/images`
- `media/thumbnails`
- `media/video`

La base SQLite constitue la source de données locale de Desktop.

### Noteor PWA

Technologies identifiées :

- React 18 ;
- TypeScript ;
- Vite ;
- Dexie.js ;
- IndexedDB ;
- Service Worker ;
- déploiement Netlify.

Fichiers examinés lors de l'audit :

- `web/src/db/index.ts`
- `web/src/types/index.ts`
- `web/src/components/NoteEditor.tsx`
- `web/src/services/openrouter.ts`

La PWA stocke ses données localement dans IndexedDB.

Les pièces jointes sont notamment représentées par des objets Blob.

## 2.2 Principe de conservation

Les deux applications doivent conserver leurs architectures locales respectives.

Il est interdit de transformer la synchronisation en dépendance obligatoire pour utiliser Noteor.

Les règles suivantes sont impératives :

- Desktop doit continuer à fonctionner sans serveur.
- La PWA doit continuer à fonctionner sans serveur.
- Une panne du serveur ne doit jamais empêcher la création ou la modification d'une note.
- Une coupure réseau ne doit jamais entraîner une perte de données locales.
- Les bases locales ne doivent pas être remplacées par une base distante.
- Le serveur ne doit pas devenir le seul emplacement contenant les données utilisateur.

La synchronisation est une fonctionnalité complémentaire.

## 2.3 Niveau de validation des informations

Les informations techniques décrites dans ce document proviennent de l'audit des fichiers accessibles et de l'analyse des structures identifiées.

Les éléments suivants doivent néanmoins être vérifiés dans le code complet avant leur implémentation :

- exhaustivité des tables et champs ;
- exhaustivité des méthodes de mutation ;
- gestion complète des relations entre entités ;
- comportement des suppressions en cascade ;
- format exact des Wikilinks ;
- gestion complète des médias ;
- migrations SQLite et Dexie existantes ;
- couverture de tests et intégration continue.

Une hypothèse ne doit jamais être transformée automatiquement en caractéristique du produit.

Le développeur doit examiner les implémentations réelles avant de modifier un fichier.

# 3. VISION ARCHITECTURALE

## 3.1 Architecture Local-First

Noteor Sync repose sur une architecture Local-First.

Chaque appareil possède une copie locale indépendante des données.

La synchronisation transmet uniquement les changements nécessaires entre les appareils.

Architecture logique :

```text
                  NOTEOR ECOSYSTEM

       NOTEOR DESKTOP              NOTEOR PWA
       Python / PyQt6              React / TypeScript
              |                            |
       SQLite local                IndexedDB local
              |                            |
       Sync Engine                 Sync Engine
              |                            |
       Crypto Layer                Crypto Layer
              |                            |
              +-------------+--------------+
                            |
                       HTTPS / VPN
                            |
                    NOTEOR SYNC SERVER
                         FastAPI
                            |
                    SQLite / Journal
                            |
                    Stockage de médias
                    chiffrés sur disque
```

Le serveur constitue un relais de synchronisation et un stockage de données chiffrées.

Il ne constitue pas la base métier principale.

## 3.2 Responsabilités des composants

### Desktop

Responsabilités :

- gestion des notes locales ;
- gestion des catégories, dossiers et tags ;
- gestion des pièces jointes ;
- persistance SQLite ;
- génération des opérations locales ;
- chiffrement des opérations ;
- transmission au serveur ;
- récupération et application des changements distants.

### PWA

Responsabilités :

- gestion des notes locales ;
- gestion des catégories, dossiers et tags ;
- gestion des pièces jointes Blob ;
- persistance IndexedDB ;
- génération des opérations locales ;
- chiffrement des opérations ;
- transmission au serveur ;
- récupération et application des changements distants.

### Serveur Noteor Sync

Responsabilités :

- authentification des appareils ;
- autorisation des opérations ;
- gestion des coffres ;
- stockage des opérations chiffrées ;
- attribution de séquences de synchronisation ;
- gestion des curseurs ;
- stockage des objets médias chiffrés ;
- gestion des appareils autorisés et révoqués ;
- sauvegardes ;
- supervision.

Le serveur ne doit pas :

- déchiffrer les notes ;
- interpréter le contenu des notes ;
- modifier les contenus métier ;
- effectuer des opérations de fusion en clair ;
- posséder les clés de déchiffrement du coffre.

# 4. ORGANISATION CIBLE DU PROJET

Les applications existantes restent dans leur emplacement actuel.

La synchronisation est ajoutée sous forme de modules indépendants.

Organisation indicative :

```text
Noteor/
│
├── application Desktop existante
│   ├── database.py
│   ├── config.py
│   ├── ui/
│   └── ...
│
├── web/
│   ├── src/
│   │   ├── db/
│   │   ├── components/
│   │   ├── services/
│   │   └── ...
│   └── ...
│
├── sync/
│   ├── protocol/
│   │   ├── schemas/
│   │   ├── envelopes/
│   │   └── specification/
│   │
│   ├── crypto/
│   │   ├── vectors/
│   │   └── specification/
│   │
│   ├── desktop/
│   │   ├── adapters/
│   │   ├── engine/
│   │   └── client/
│   │
│   ├── web/
│   │   ├── adapters/
│   │   ├── engine/
│   │   └── client/
│   │
│   └── conflicts/
│
├── sync-server/
│   ├── app/
│   │   ├── api/
│   │   ├── services/
│   │   ├── models/
│   │   ├── storage/
│   │   └── security/
│   │
│   ├── migrations/
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
│
├── tests/
│   ├── protocol/
│   ├── interoperability/
│   └── migration/
│
└── NOTEOR_SYNC_ARCHITECTURE.md
```

Cette organisation est une proposition de destination.

Avant de créer les répertoires, Codex doit vérifier les conventions existantes du dépôt et adapter les emplacements sans déplacer inutilement les fichiers actuels.

Les modules Desktop et PWA doivent rester indépendants des détails internes du serveur.

# 5. MODÈLE DE DONNÉES COMMUN

## 5.1 Principe général

Les bases SQLite et IndexedDB ne doivent pas être synchronisées directement.

Il faut définir un modèle logique indépendant des deux systèmes de stockage.

Chaque entité synchronisable possède un identifiant global UUID.

Les identifiants numériques locaux existants sont conservés pour préserver la compatibilité.

Exemple :

```text
Desktop :
id = 42
sync_id = 7d3a...

PWA :
id = 105
sync_id = 7d3a...
```

Les deux enregistrements représentent le même objet.

Le serveur ne doit jamais utiliser les identifiants numériques locaux comme identifiants globaux.

## 5.2 Identité des objets

Chaque objet synchronisable possède les informations logiques suivantes :

| Propriété | Description |
|---|---|
| `sync_id` | UUID global de l'objet |
| `entity_type` | Type d'objet |
| `revision` | Révision logique |
| `parent_revision` | Révision de base de la modification |
| `device_id` | Appareil à l'origine de l'opération |
| `operation_id` | UUID unique de l'opération |
| `deleted` | État logique de suppression |
| `payload` | Contenu métier chiffré |

Les champs métier sont encapsulés dans une enveloppe chiffrée.

Les champs techniques nécessaires au routage et à la synchronisation restent séparés.

## 5.3 Notes

Modèle logique cible :

```text
Note
├── sync_id
├── title
├── content
├── category_sync_id
├── folder_sync_id
├── created_at
├── updated_at
├── is_deleted
├── deleted_at
├── revision
└── metadata
```

Les champs exacts doivent être rapprochés du modèle réellement utilisé par chaque application.

Les propriétés métier sont chiffrées.

L'identifiant local numérique reste propre à chaque appareil.

## 5.4 Catégories

Modèle logique :

```text
Category
├── sync_id
├── name
├── parent_sync_id
├── color
├── sort_order
├── created_at
├── updated_at
└── revision
```

Les différences constatées entre Desktop et PWA doivent être harmonisées.

Un champ absent d'une application ne doit pas être inventé ou supprimé silencieusement.

La migration doit préserver les valeurs existantes.

## 5.5 Dossiers

Modèle logique :

```text
Folder
├── sync_id
├── name
├── parent_sync_id
├── created_at
├── updated_at
└── revision
```

Les relations hiérarchiques doivent être représentées par les UUID globaux.

Les suppressions de dossiers doivent produire des opérations explicites.

Les effets sur les notes et sous-dossiers doivent être définis au niveau du protocole et non dépendre uniquement des cascades SQL locales.

## 5.6 Tags

Modèle logique :

```text
Tag
├── sync_id
├── name
├── color
├── created_at
├── updated_at
└── revision
```

Les relations note-tag sont également synchronisables.

Modèle :

```text
NoteTag
├── sync_id
├── note_sync_id
├── tag_sync_id
└── revision
```

L'unicité des tags doit respecter la règle fonctionnelle existante concernant la casse.

Les règles de renommage, fusion éventuelle et suppression devront être explicitées dans le protocole.

## 5.7 Pièces jointes

Les médias sont une partie importante de Noteor Sync.

Les deux applications utilisent actuellement des mécanismes de stockage différents :

- Desktop : chemins de fichiers locaux ;
- PWA : objets Blob dans IndexedDB.

Il est interdit de synchroniser directement ces structures.

Modèle logique cible :

```text
Attachment
├── sync_id
├── note_sync_id
├── filename
├── media_type
├── mime_type
├── size
├── duration
├── created_at
├── encrypted_object_id
├── encrypted_hash
├── encryption_version
└── metadata
```

Les transcriptions, miniatures et métadonnées privées doivent être chiffrées.

Les médias sont synchronisés comme des objets binaires indépendants.

Le stockage local de chaque application conserve son propre format :

- Desktop reconstruit un fichier local ;
- PWA reconstruit un Blob.

L'adaptateur local assure cette conversion.

## 5.8 Relations

Toutes les relations entre objets synchronisables doivent utiliser les UUID globaux.

Cela concerne notamment :

- notes et catégories ;
- notes et dossiers ;
- dossiers parents et enfants ;
- notes et tags ;
- pièces jointes et notes.

Les identifiants numériques locaux ne doivent jamais être transmis comme références inter-appareils.

# 6. MIGRATION DES IDENTIFIANTS ET WIKILINKS

## 6.1 Problème existant

Les identifiants numériques générés indépendamment par SQLite et IndexedDB peuvent entrer en collision.

Les Wikilinks identifiés dans la PWA utilisent notamment une référence numérique.

Exemple :

```text
[[Titre de la note|123]]
```

Cette représentation ne garantit pas une identité stable entre appareils.

## 6.2 Solution retenue

Introduire des UUID globaux pour les notes.

Le format cible des références internes doit utiliser l'identifiant global.

Exemple indicatif :

```text
[[Titre de la note|@7d3a...]]
```

La syntaxe définitive devra être validée avec les parseurs existants.

## 6.3 Migration progressive

La migration doit :

1. Sauvegarder la base locale.
2. Ajouter un UUID à chaque note existante.
3. Construire une correspondance entre ancien identifiant et UUID.
4. Parcourir les références internes.
5. Réécrire les références vers le format global.
6. Vérifier les références orphelines.
7. Valider les résultats.
8. Conserver temporairement la lecture de l'ancien format numérique.

Les anciens formats doivent rester lisibles pendant la période de transition.

La migration doit être transactionnelle, idempotente et réversible dans la mesure du possible.

Aucune donnée ne doit être supprimée pendant cette migration.

# 7. GESTION DES DATES ET DES RÉVISIONS

## 7.1 Problème existant

Desktop et PWA utilisent des conventions de dates différentes.

Les dates locales ne peuvent pas servir seules de mécanisme de détection des changements.

## 7.2 Décision

Les dates servent à l'affichage et à la compréhension historique.

Elles ne déterminent pas la priorité d'une modification.

Les dates échangées dans le protocole doivent être normalisées en UTC au format ISO 8601.

Exemple :

```text
2026-11-19T10:30:00.000Z
```

## 7.3 Révisions

Chaque objet possède une révision logique.

Chaque modification indique la révision dont elle est issue.

Exemple :

```text
Objet A
Revision 1

PC :
Revision 1 -> Revision 2

Téléphone :
Revision 1 -> Revision 2
```

Le serveur ne doit pas choisir arbitrairement l'une des deux modifications.

Il doit conserver les deux branches et permettre aux clients de gérer le conflit.

Les compteurs de révision ne doivent pas être considérés comme une horloge universelle.

Le journal serveur fournit l'ordre de réception et un curseur de synchronisation.

# 8. SUPPRESSIONS ET CORBEILLE

## 8.1 Principe

Les suppressions doivent être synchronisables même lorsque les appareils sont hors ligne.

Une suppression physique immédiate est incompatible avec la synchronisation différée.

## 8.2 Tombstones

Chaque suppression produit un marqueur persistant.

Exemple :

```text
DeleteOperation
├── operation_id
├── entity_sync_id
├── entity_type
├── parent_revision
├── device_id
└── created_at
```

Le serveur transmet ce marqueur à tous les appareils autorisés.

Les clients appliquent ensuite la suppression localement.

## 8.3 Deux niveaux de suppression

- Corbeille : suppression réversible.
- Suppression définitive demandée : opération de suppression synchronisée.
- Purge physique : après propagation et expiration de la période de rétention.

Les appareils hors ligne ne doivent pas pouvoir réintroduire une donnée supprimée.

Une politique spécifique doit être prévue pour les appareils perdus, révoqués ou restés hors ligne très longtemps.

# 9. ARCHITECTURE DE CHIFFREMENT E2E

## 9.1 Exigence fondamentale

Le serveur ne doit jamais avoir accès aux clés permettant de déchiffrer le contenu métier.

Le chiffrement doit être effectué sur les appareils avant toute transmission.

Le déchiffrement est effectué uniquement sur les appareils autorisés.

Le chiffrement doit couvrir :

- titres ;
- contenus ;
- catégories ;
- dossiers ;
- tags ;
- relations privées ;
- pièces jointes ;
- transcriptions ;
- miniatures ;
- métadonnées privées.

Les métadonnées techniques nécessaires au fonctionnement du serveur peuvent rester visibles.

## 9.2 Technologies candidates

| Fonction | Technologie |
|---|---|
| Chiffrement authentifié | XChaCha20-Poly1305 |
| Dérivation de clé | Argon2id |
| Bibliothèque cryptographique | libsodium |
| Desktop | PyNaCl ou liaison compatible |
| PWA | libsodium.js / libsodium-wrappers |
| Empreintes | SHA-256 |
| Identifiants | UUID |
| Échange de clés | Mécanisme authentifié fondé sur des clés publiques |

Les versions exactes des bibliothèques devront être choisies après validation de leur compatibilité et de leur maintenance.

Les deux clients doivent produire des résultats cryptographiques interopérables.

Il est interdit d'implémenter soi-même les primitives cryptographiques.

## 9.3 Gestion du coffre

Chaque coffre possède une clé maîtresse aléatoire.

Cette clé protège les clés de données.

Schéma logique :

```text
Vault Master Key
       |
       +---- Data Encryption Keys
       |           |
       |           +---- Notes
       |           +---- Categories
       |           +---- Folders
       |           +---- Tags
       |           +---- Metadata
       |
       +---- Media Encryption Keys
                   |
                   +---- Images
                   +---- Audio
                   +---- Video
                   +---- Thumbnails
```

La clé maîtresse ne doit jamais être envoyée au serveur en clair.

Elle doit être protégée localement et disposer d'un mécanisme de récupération.

Une phrase secrète ou un secret de récupération peut servir à protéger une copie de la clé maîtresse.

Argon2id doit être utilisé pour dériver une clé de protection à partir d'un secret utilisateur.

La phrase de récupération ne doit pas être considérée comme une simple donnée de configuration.

## 9.4 Enveloppe cryptographique

Chaque objet transmis doit posséder une enveloppe versionnée.

Structure indicative :

```json
{
  "format_version": 1,
  "crypto_version": 1,
  "vault_id": "opaque-vault-id",
  "entity_sync_id": "uuid",
  "entity_type": "note",
  "key_id": "key-reference",
  "nonce": "base64",
  "ciphertext": "base64",
  "authentication_tag": "included-in-ciphertext"
}
```

Cette structure est indicative.

Le format définitif doit être décrit dans `NOTEOR_SYNC_PROTOCOL.md`.

L'enveloppe doit permettre une évolution des formats sans rendre les anciennes données illisibles.

Les nonces doivent être uniques pour chaque clé de chiffrement.

Le système doit être conçu pour éviter toute réutilisation de nonce.

## 9.5 Séparation des métadonnées

Le serveur peut avoir besoin de connaître :

- l'identifiant opaque du coffre ;
- l'identifiant de l'appareil ;
- l'identifiant de l'opération ;
- le type technique de l'objet ;
- la taille de l'objet chiffré ;
- la séquence serveur ;
- l'état du transfert.

Il ne doit pas recevoir :

- le titre en clair ;
- le contenu en clair ;
- le nom privé d'une catégorie ;
- les transcriptions ;
- les clés privées ;
- la clé maîtresse ;
- la phrase de récupération.

L'architecture doit minimiser les fuites de métadonnées.

## 9.6 Ajout d'un appareil

L'ajout d'un appareil doit être une opération explicite.

Parcours cible :

1. Un appareil déjà autorisé demande un jumelage.
2. Un jeton temporaire est créé.
3. Le nouvel appareil génère ses propres clés.
4. Les appareils établissent un échange authentifié.
5. Une copie protégée du secret du coffre est transmise au nouvel appareil.
6. Le nouvel appareil prouve qu'il possède les éléments nécessaires.
7. Le serveur enregistre l'appareil autorisé.

Le jeton de jumelage doit être à usage unique, limité dans le temps et lié à une session précise.

La clé maîtresse ne doit jamais être incluse en clair dans le QR code.

Le protocole exact de jumelage doit être documenté séparément.

## 9.7 Révocation

Chaque appareil possède une identité cryptographique propre.

Un appareil peut être révoqué indépendamment des autres.

Après révocation :

- il ne peut plus transmettre d'opérations ;
- il ne peut plus récupérer les opérations ;
- ses jetons d'accès deviennent invalides ;
- les nouvelles synchronisations sont refusées.

La révocation d'un appareil ne doit pas entraîner automatiquement la destruction du coffre.

La procédure de rotation des clés après compromission doit être définie.

## 9.8 Stockage local des secrets

Desktop :

- privilégier le trousseau de clés du système ;
- ne pas enregistrer les secrets en clair dans les fichiers de configuration ;
- prévoir une stratégie de verrouillage.

PWA :

- ne pas utiliser `localStorage` pour stocker la clé maîtresse en clair ;
- privilégier un stockage protégé adapté au navigateur ;
- examiner les possibilités de Web Crypto et CryptoKey non extractibles ;
- protéger les données persistées dans IndexedDB ;
- prévoir un verrouillage du coffre.

Le choix définitif doit être validé par des tests sur les navigateurs ciblés.

Le chiffrement E2E ne protège pas contre un appareil compromis ou un code applicatif malveillant exécuté sur un appareil autorisé.

# 10. PROTOCOLE DE SYNCHRONISATION

## 10.1 Principes

La synchronisation repose sur un journal d'opérations.

Elle ne repose pas sur une comparaison des dates `updated_at`.

Les opérations doivent être :

- uniques ;
- idempotentes ;
- ordonnées par le serveur ;
- vérifiables ;
- persistées localement avant transmission.

## 10.2 Journal local

Chaque client doit disposer d'un journal local de synchronisation.

Une opération contient au minimum :

```text
SyncOperation
├── operation_id
├── vault_id
├── device_id
├── entity_type
├── entity_sync_id
├── parent_revision
├── operation_type
├── encrypted_payload
├── created_at
└── status
```

Les statuts locaux peuvent être :

- pending ;
- in_flight ;
- acknowledged ;
- conflict ;
- failed.

Les valeurs exactes et leurs transitions seront définies dans le protocole.

## 10.3 Atomicité locale

Une mutation métier et son inscription dans le journal doivent être atomiques.

Desktop :

- utiliser une transaction SQLite ;
- enregistrer la modification métier ;
- enregistrer l'opération de synchronisation dans la même transaction.

PWA :

- utiliser une transaction Dexie ;
- inclure les tables métier et le journal local dans la transaction ;
- garantir que les deux opérations réussissent ou échouent ensemble.

Cette règle évite de perdre une modification entre son enregistrement local et sa détection par le moteur de synchronisation.

## 10.4 Cycle de synchronisation

```text
1. Charger les opérations locales en attente.

2. Préparer les enveloppes chiffrées.

3. Envoyer les opérations au serveur.

4. Le serveur vérifie l'appareil et les droits.

5. Le serveur détecte les doublons.

6. Le serveur attribue les séquences.

7. Le client récupère les opérations distantes.

8. Le client vérifie et déchiffre les enveloppes.

9. Le client applique les modifications localement.

10. Le client confirme le curseur reçu.
```

Le traitement doit être reprenable après interruption.

Une opération déjà acceptée par le serveur ne doit pas être appliquée deux fois.

## 10.5 Curseurs

Chaque appareil dispose de son propre curseur de synchronisation.

Le serveur attribue une séquence monotone aux opérations acceptées.

Le curseur indique jusqu'où un appareil a récupéré et confirmé le journal.

La gestion des curseurs doit tenir compte des opérations reçues, appliquées et acquittées.

Une opération reçue par le serveur ne doit pas être considérée comme appliquée sur tous les appareils.

## 10.6 Conflits

Les conflits sont détectés lorsque deux modifications partent d'une même révision de base.

Exemple :

```text
Revision 1

PC :
Revision 1 -> Revision 2

PWA :
Revision 1 -> Revision 2
```

Le serveur conserve les deux branches.

Les clients doivent recevoir les informations nécessaires à leur résolution.

Règles :

- objets différents : synchronisation automatique ;
- modifications concurrentes du même objet : conflit ;
- conservation des deux versions ;
- aucune écrasement silencieux ;
- possibilité de comparer et de choisir ;
- création d'une nouvelle révision après résolution.

Une fusion automatique ne pourra être introduite que pour des cas explicitement définis et testés.

Le serveur ne doit jamais fusionner les contenus chiffrés.

## 10.7 Opérations en masse

Les opérations concernant les dossiers, catégories, tags et relations doivent être représentées de façon explicite.

Une opération en cascade ne doit pas être interprétée différemment par les deux applications.

Le protocole doit déterminer si une action produit :

- une opération unique portant sur un groupe d'objets ;
- plusieurs opérations individuelles liées ;
- une opération de suppression avec des effets dérivés.

La solution retenue doit préserver l'intégrité des relations.

# 11. SYNCHRONISATION DES MÉDIAS

## 11.1 Principe

Les médias sont synchronisés indépendamment des métadonnées.

Ils sont chiffrés localement avant transfert.

Le serveur ne stocke que les objets chiffrés.

## 11.2 Parcours

```text
Fichier local
     |
Chiffrement local
     |
Calcul de l'empreinte
     |
Découpage éventuel en blocs
     |
Upload reprenable
     |
Stockage serveur
     |
Téléchargement sur autre appareil
     |
Vérification d'intégrité
     |
Déchiffrement local
     |
Reconstruction du média
```

## 11.3 Exigences

- transferts reprenables ;
- vérification d'intégrité ;
- déduplication possible ;
- gestion des médias volumineux ;
- gestion des transferts interrompus ;
- nettoyage des transferts abandonnés ;
- rattachement fiable aux notes ;
- compatibilité avec les formats locaux existants.

Les médias ne doivent pas être intégralement chargés en mémoire lorsque leur taille ne le justifie pas.

## 11.4 Transcriptions et services IA

La PWA utilise actuellement un service OpenRouter pour la transcription audio.

Cette opération est indépendante de Noteor Sync.

La synchronisation doit chiffrer les enregistrements et les transcriptions.

Lorsqu'une transcription IA est demandée :

1. le média est déchiffré localement ;
2. l'utilisateur déclenche explicitement l'opération ;
3. les données nécessaires sont transmises au fournisseur externe ;
4. le résultat est stocké localement ;
5. le résultat est ensuite synchronisé sous forme chiffrée.

Les clés API et paramètres de fournisseurs IA restent des paramètres locaux.

# 12. SERVEUR NOTEOR SYNC

## 12.1 Technologies

Technologies recommandées :

| Composant | Choix |
|---|---|
| Langage | Python |
| Framework API | FastAPI |
| Serveur HTTP | Uvicorn |
| Base | SQLite |
| Mode SQLite | WAL |
| Stockage médias | Système de fichiers |
| Déploiement | Docker ou Podman Compose |
| Transport | HTTPS |
| Réseau privé | WireGuard / Tailscale |

La première version doit éviter les dépendances inutiles.

PostgreSQL, Redis, Kubernetes et un stockage objet externe ne sont pas nécessaires pour le périmètre initial.

## 12.2 API REST indicative

Préfixe :

`/api/v1/`

Endpoints envisagés :

| Endpoint | Fonction |
|---|---|
| `POST /devices/pair` | Jumelage |
| `GET /devices` | Liste des appareils |
| `DELETE /devices/{id}` | Révocation |
| `POST /sync/push` | Transmission des opérations |
| `GET /sync/pull` | Récupération des opérations |
| `GET /sync/status` | État de synchronisation |
| `POST /devices/ack` | Confirmation de réception |
| `POST /media/uploads` | Création d'un transfert |
| `PUT /media/uploads/{id}/chunks/{n}` | Envoi d'un bloc |
| `GET /media/{id}` | Téléchargement d'un média chiffré |

Les méthodes et contrats définitifs seront spécifiés dans `NOTEOR_SYNC_PROTOCOL.md`.

## 12.3 Base serveur

Tables indicatives :

| Table | Responsabilité |
|---|---|
| `users` | Identités et accès locaux |
| `vaults` | Coffres |
| `devices` | Appareils autorisés |
| `sync_operations` | Journal chiffré |
| `sync_cursors` | Progression des appareils |
| `media_objects` | Références des médias |
| `media_uploads` | Transferts en cours |
| `pairing_sessions` | Jumelages temporaires |
| `revoked_devices` | Révocations |

La base serveur ne contient pas les données métier en clair.

La structure réelle sera définie après validation des besoins du protocole.

## 12.4 Stockage

Organisation indicative :

```text
noteor-sync/
├── config/
├── data/
│   ├── sync.db
│   ├── objects/
│   ├── temporary/
│   └── backups/
├── logs/
└── compose.yaml
```

Les données doivent être stockées dans des volumes persistants.

Le processus serveur doit fonctionner avec un utilisateur non privilégié.

# 13. HÉBERGEMENT À DOMICILE

## 13.1 Principe

Le serveur est installé sur une machine Linux à domicile.

Il doit être indépendant des postes clients.

Il doit pouvoir fonctionner en continu et redémarrer automatiquement après une panne ou un redémarrage.

## 13.2 Accès réseau

Solution recommandée :

- WireGuard ;
- Tailscale dans un premier temps ;
- Headscale éventuellement dans une évolution ultérieure.

L'API doit être accessible uniquement sur le réseau privé VPN dans la configuration de référence.

Il ne faut pas exposer directement l'API sur Internet.

## 13.3 Déploiement

Le déploiement doit être reproductible.

Il comprend :

- Dockerfile ;
- compose.yaml ;
- configuration externalisée ;
- volumes persistants ;
- politique de redémarrage ;
- healthcheck ;
- documentation d'installation ;
- documentation de mise à jour ;
- procédure de sauvegarde ;
- procédure de restauration.

## 13.4 Sauvegardes

La sauvegarde doit inclure :

- base SQLite ;
- journal de synchronisation ;
- objets médias chiffrés ;
- configuration non secrète ;
- informations nécessaires à la restauration.

Les secrets de chiffrement ne doivent pas être copiés dans les sauvegardes serveur.

Les sauvegardes doivent être cohérentes, chiffrées au repos si elles sont exportées vers un autre emplacement, et testées régulièrement.

Une sauvegarde non restaurable ne constitue pas une stratégie de sauvegarde valide.

# 14. ADAPTATIONS NÉCESSAIRES DANS NOTEOR DESKTOP

Les adaptations doivent être ajoutées sans casser les fonctionnalités existantes.

## 14.1 Adaptateur SQLite

Créer un adaptateur qui convertit les objets existants en objets du protocole commun.

Il doit gérer :

- notes ;
- catégories ;
- dossiers ;
- tags ;
- relations ;
- pièces jointes ;
- suppressions ;
- restaurations.

Il doit également convertir les UUID globaux vers les identifiants numériques locaux lorsqu'une opération distante est appliquée.

## 14.2 Journal local

Ajouter un journal transactionnel.

Une modification locale et son opération de synchronisation doivent être enregistrées dans la même transaction SQLite.

Le moteur ne doit pas dépendre uniquement des horodatages.

## 14.3 Moteur de synchronisation

Responsabilités :

- lecture du journal ;
- chiffrement ;
- appels API ;
- récupération des opérations ;
- déchiffrement ;
- application locale ;
- gestion des conflits ;
- reprise après interruption.

Le moteur doit être exécuté hors du thread graphique principal.

Les opérations réseau ne doivent jamais bloquer l'interface PyQt6.

## 14.4 Interface

Ajouter une interface de synchronisation permettant :

- activation et désactivation ;
- configuration du serveur ;
- association du coffre ;
- gestion des appareils ;
- synchronisation manuelle ;
- état de la dernière synchronisation ;
- progression des médias ;
- gestion des conflits ;
- verrouillage du coffre.

Le fonctionnement local doit rester identique lorsque la synchronisation est désactivée.

# 15. ADAPTATIONS NÉCESSAIRES DANS NOTEOR PWA

## 15.1 Adaptateur Dexie

Créer un adaptateur entre les tables IndexedDB existantes et le modèle commun.

Il doit gérer les identifiants globaux et les relations.

Les modifications du schéma Dexie doivent respecter les mécanismes de migration déjà présents.

## 15.2 Journal local

Ajouter les opérations de synchronisation dans IndexedDB.

La modification métier et son inscription au journal doivent être réalisées dans une transaction Dexie.

Les opérations doivent être conservées jusqu'à leur confirmation.

## 15.3 Moteur de synchronisation

Responsabilités :

- chargement des opérations locales ;
- chiffrement ;
- transmission API ;
- récupération des changements ;
- déchiffrement ;
- application dans Dexie ;
- gestion des conflits ;
- reprise des transferts médias.

Le moteur doit être indépendant des composants React.

Il ne doit pas bloquer l'interface.

## 15.4 Fonctionnement mobile

La PWA doit continuer à fonctionner hors ligne.

La synchronisation doit pouvoir être déclenchée :

- au lancement ;
- au retour au premier plan ;
- après une modification lorsque cela est possible ;
- à la demande de l'utilisateur.

La synchronisation en arrière-plan ne doit pas être considérée comme garantie par les navigateurs mobiles.

Une synchronisation manuelle doit toujours être disponible.

## 15.5 Interface

Ajouter :

- état de connexion ;
- dernière synchronisation ;
- progression ;
- appareils autorisés ;
- conflits ;
- gestion du coffre ;
- gestion des clés de récupération ;
- configuration du serveur.

Le design doit rester cohérent avec l'interface existante.

# 16. SÉCURITÉ ET MENACES

L'architecture doit prendre en compte les menaces suivantes :

| Menace | Protection attendue |
|---|---|
| Interception réseau | HTTPS / VPN |
| Lecture des données serveur | E2E |
| Compromission serveur | Clés absentes du serveur |
| Appareil révoqué | Révocation des accès |
| Répétition d'une opération | Idempotence |
| Modification d'un objet chiffré | Authentification cryptographique |
| Perte de connexion | Journal local persistant |
| Perte de serveur | Sauvegarde et restauration |
| Perte d'appareil | Révocation et récupération |
| Mauvaise clé | Échec de déchiffrement |
| Réutilisation de nonce | Génération et contrôle adaptés |
| Script malveillant PWA | CSP, contrôle des dépendances, protection du déploiement |

La sécurité doit être validée par des tests et non uniquement par des déclarations architecturales.

# 17. STRATÉGIE DE TESTS

## 17.1 Tests du protocole

- validation des schémas ;
- compatibilité des versions ;
- validation des UUID ;
- idempotence ;
- ordre des opérations ;
- validation des curseurs ;
- gestion des doublons.

## 17.2 Tests cryptographiques

- chiffrement Python vers déchiffrement TypeScript ;
- chiffrement TypeScript vers déchiffrement Python ;
- mauvaise clé ;
- nonce incorrect ;
- contenu altéré ;
- enveloppe tronquée ;
- mauvaise version cryptographique ;
- clé révoquée ;
- récupération du coffre.

Les vecteurs de tests doivent être communs aux deux implémentations.

## 17.3 Tests de synchronisation

- PC vers téléphone ;
- téléphone vers PC ;
- modifications successives ;
- modifications simultanées ;
- suppressions hors ligne ;
- restauration ;
- interruption réseau ;
- redémarrage serveur ;
- répétition d'opération ;
- plusieurs appareils.

## 17.4 Tests des médias

- image ;
- audio ;
- vidéo ;
- miniature ;
- transcription ;
- fichier volumineux ;
- interruption d'upload ;
- reprise ;
- corruption ;
- doublon ;
- suppression.

## 17.5 Tests de non-régression

Les tests existants doivent continuer à passer.

La synchronisation ne doit pas modifier le comportement des fonctions locales.

Les tests doivent couvrir les migrations de bases existantes.

## 17.6 Tests de sécurité serveur

- appareil non autorisé ;
- jeton expiré ;
- révocation ;
- accès à un autre coffre ;
- tentative de répétition ;
- upload non autorisé ;
- dépassement de taille ;
- données malformées ;
- tentative d'accès aux fichiers internes.

# 18. STRATÉGIE D'ÉVOLUTION

## Version 1 — Synchronisation personnelle

Périmètre :

- un coffre ;
- un utilisateur ;
- un PC ;
- un téléphone ;
- synchronisation bidirectionnelle ;
- notes ;
- catégories ;
- dossiers ;
- tags ;
- médias ;
- chiffrement E2E ;
- conflits ;
- suppressions synchronisées.

C'est le périmètre prioritaire.

## Version 1.1 — Plusieurs appareils

- plusieurs téléphones ;
- plusieurs ordinateurs ;
- gestion améliorée des appareils ;
- récupération et révocation ;
- supervision.

## Version 1.2 — Plusieurs coffres

- plusieurs espaces indépendants ;
- clés distinctes ;
- paramètres indépendants ;
- sauvegardes par coffre.

## Version 2 — Partage sécurisé

- partage de notes ;
- partage de coffres ;
- gestion des destinataires ;
- droits de lecture et d'écriture ;
- distribution sécurisée des clés.

Cette évolution nécessite une nouvelle étude cryptographique.

## Version 3 — Collaboration

- modifications collaboratives ;
- historique avancé ;
- résolution évoluée des conflits ;
- éventuelles stratégies de fusion.

La collaboration ne fait pas partie de la première version.

# 19. DÉCISIONS RETENUES ET POINTS À TRANCHER

## 19.1 Décisions retenues

| Sujet | Décision |
|---|---|
| Architecture | Local-First |
| Serveur | Indépendant |
| Hébergement | Domicile |
| Accès | VPN privé |
| Backend | FastAPI |
| Base serveur | SQLite WAL |
| Desktop | SQLite existant |
| PWA | Dexie / IndexedDB existant |
| Protocole | REST versionné |
| Identité | UUID global |
| Identifiants locaux | Conservés |
| Synchronisation | Journal d'opérations |
| Conflits | Conservation des versions |
| Suppressions | Tombstones |
| Médias | Objets chiffrés indépendants |
| Chiffrement | E2E |
| Fonctionnement hors ligne | Obligatoire |
| Réécriture des applications | Interdite sans justification |

## 19.2 Points techniques encore ouverts

Les points suivants doivent être résolus avant de figer le protocole.

1. Format exact des enveloppes cryptographiques.
2. Mécanisme exact d'échange et de récupération des clés.
3. Format définitif des Wikilinks UUID.
4. Gestion des modifications de plusieurs objets dans une même opération.
5. Stratégie de fusion des modifications de relations.
6. Taille maximale et découpage des médias.
7. Politique de rétention des tombstones.
8. Gestion des appareils restés longtemps hors ligne.
9. Stratégie de rotation des clés.
10. Stockage protégé des clés dans les navigateurs ciblés.
11. Politique de sauvegarde et de récupération du coffre.
12. Gestion des migrations et compatibilité entre versions des clients.

Ces décisions doivent être documentées dans `NOTEOR_SYNC_PROTOCOL.md` et ne doivent pas être improvisées au cours de l'implémentation.

# 20. PHASAGE DE L'IMPLÉMENTATION

L'implémentation est divisée en phases indépendantes.

## Phase A — Audit final

- Inventaire exhaustif des tables.
- Inventaire des migrations.
- Inventaire des méthodes de mutation.
- Inventaire des médias.
- Vérification des Wikilinks.
- Analyse des tests et de la CI.

Livrable : rapport d'audit technique final.

## Phase B — Identifiants et migrations

- Ajout des UUID globaux.
- Conservation des identifiants numériques.
- Migration des Wikilinks.
- Normalisation des dates.
- Tests de migration.
- Retour arrière.

Livrable : deux clients compatibles avec les identifiants globaux.

## Phase C — Protocole et cryptographie

- Schémas communs.
- Enveloppes chiffrées.
- Gestion du coffre.
- Récupération.
- Jumelage.
- Vecteurs de tests interopérables.

Livrable : prototype cryptographique Python / TypeScript.

## Phase D — Moteurs locaux

- Journaux transactionnels.
- Adaptateurs SQLite et Dexie.
- Opérations idempotentes.
- Révisions.
- Conflits.
- Tombstones.

Livrable : moteurs testables sans serveur réel.

## Phase E — Serveur

- API.
- Appareils.
- Autorisations.
- Journal.
- Curseurs.
- Médias.
- Sauvegardes.
- Déploiement.

Livrable : Noteor Sync Server installable.

## Phase F — Intégration

- Intégration PWA.
- Intégration Desktop.
- Interface.
- Synchronisation réelle.
- Tests multi-appareils.
- Documentation.

Livrable : version stable.

# 21. CRITÈRES D'ACCEPTATION GLOBAUX

Noteor Sync ne pourra être considéré comme opérationnel que lorsque les conditions suivantes seront toutes vérifiées.

### Fonctionnement local

- Desktop fonctionne sans serveur.
- PWA fonctionne sans serveur.
- Les fonctionnalités locales existantes sont préservées.
- Les données locales ne sont jamais supprimées par une erreur de synchronisation.

### Synchronisation

- Une note créée sur Desktop apparaît sur PWA.
- Une note créée sur PWA apparaît sur Desktop.
- Les modifications sont propagées.
- Les suppressions sont propagées.
- Les appareils hors ligne récupèrent les changements ultérieurement.
- Les doublons d'opérations sont correctement gérés.
- Les conflits ne provoquent aucune perte silencieuse.

### Médias

- Les images sont synchronisées.
- Les audios sont synchronisés.
- Les vidéos sont synchronisées.
- Les miniatures sont reconstruites ou récupérées correctement.
- Les transferts interrompus peuvent reprendre.
- Les médias ne sont jamais transmis en clair au serveur.

### Sécurité

- Le serveur ne peut pas lire les notes.
- Le serveur ne peut pas déchiffrer les médias.
- Les clés privées restent sur les appareils.
- Un appareil peut être révoqué.
- Un appareil non autorisé ne peut pas synchroniser.
- La récupération du coffre fonctionne.
- Les tests cryptographiques croisés passent.

### Résilience

- Une panne du serveur ne provoque pas de perte locale.
- Une coupure réseau ne provoque pas de perte locale.
- Un redémarrage du serveur permet de reprendre la synchronisation.
- Une sauvegarde peut être restaurée.
- Les migrations n'entraînent aucune perte de données.

### Non-régression

- Les tests existants passent.
- Le build Desktop fonctionne.
- Le build PWA fonctionne.
- L'import/export existant continue de fonctionner.
- Les Wikilinks continuent de fonctionner.
- Les fonctions de recherche et d'édition restent opérationnelles.
- Les appels aux services IA continuent de fonctionner selon leur configuration existante.

# 22. RÈGLES IMPÉRATIVES POUR LES FUTURES INTERVENTIONS CODEX

Ce document doit être utilisé comme référence lors de chaque phase de développement.

Les règles suivantes sont obligatoires.

1. Examiner le code existant avant toute modification.
2. Ne jamais supposer qu'une fonction n'existe pas sans avoir recherché son équivalent.
3. Ne pas réécrire une fonctionnalité existante pour ajouter la synchronisation.
4. Ne pas supprimer une fonctionnalité existante.
5. Ne pas remplacer SQLite ou IndexedDB.
6. Ne pas synchroniser directement des fichiers SQLite.
7. Ne pas utiliser `updated_at` comme seul mécanisme de synchronisation.
8. Ne pas exposer de données privées en clair au serveur.
9. Ne jamais stocker les clés de chiffrement en clair côté serveur.
10. Ne pas effectuer de suppression physique avant propagation.
11. Ne pas fusionner automatiquement deux modifications concurrentes sans règle validée.
12. Ne pas introduire de dépendance cloud obligatoire.
13. Ne pas introduire de services payants obligatoires.
14. Préserver les conventions de code et l'organisation existantes.
15. Écrire les tests avant de considérer une fonctionnalité terminée.
16. Vérifier les builds et les tests après chaque phase.
17. Documenter les migrations et leur procédure de retour arrière.
18. Ne jamais prétendre qu'un test a été exécuté s'il ne l'a pas été.
19. Produire un rapport final indiquant les fichiers modifiés, les tests réellement exécutés et les limites restantes.
20. Toute modification de cette architecture doit être justifiée et documentée.

# 23. DOCUMENTS TECHNIQUES COMPLÉMENTAIRES

Ce document constitue la référence architecturale principale.

Les documents suivants devront le compléter :

| Document | Objet |
|---|---|
| `NOTEOR_SYNC_ARCHITECTURE.md` | Architecture globale |
| `NOTEOR_SYNC_PROTOCOL.md` | Modèle de données et protocole |
| `NOTEOR_SYNC_SECURITY.md` | Cryptographie, clés et menaces |
| `NOTEOR_SYNC_ROADMAP.md` | Phases et dépendances |
| `NOTEOR_SYNC_TEST_PLAN.md` | Scénarios et critères de validation |
| `NOTEOR_SYNC_DEPLOYMENT.md` | Installation à domicile |

Les documents complémentaires ne doivent pas contredire l'architecture principale.

En cas de modification d'une décision fondamentale, ce document doit être mis à jour.

---

# 24. CONCLUSION

Noteor Sync doit permettre à Noteor de devenir une application multi-appareils sans perdre son identité Local-First.

Le principe central est simple :

**Les données appartiennent aux appareils de l'utilisateur. Le serveur ne fait que synchroniser des données chiffrées.**

L'ordinateur et le téléphone restent autonomes.

Le serveur personnel facilite leur synchronisation, mais ne devient jamais une dépendance indispensable à l'utilisation de Noteor.

La première version doit privilégier la fiabilité, la sécurité, la simplicité d'installation et la préservation de toutes les fonctionnalités existantes.

La priorité n'est pas de multiplier les fonctionnalités, mais de garantir qu'une note créée sur un appareil puisse être retrouvée sur un autre, sans perte, sans conflit silencieux et sans exposer son contenu au serveur.

# 25. ANNEXE DE RÉCONCILIATION v1.1

Les sections 1 à 24 ci-dessus constituent la référence fondatrice v1.0. Afin d'éliminer les divergences avec l'implémentation effective et les spécifications détaillées (`NOTEOR_SYNC_PROTOCOL.md`, `NOTEOR_SYNC_CRYPTO.md`, `NOTEOR_SYNC_DATA_MODEL.md`), les correspondances suivantes s'appliquent en **v1.1** :

| Sujet | Formulation indicative v1.0 | Contrat normatif retenu en v1.1 |
|---|---|---|
| **Arborescence réelle (`§4`)** | `sync/web/`, `NOTEOR_SYNC_*.md` à la racine | Documentation normalisée sous `docs/sync/` (`NOTEOR_SYNC_ARCHITECTURE.md`, `NOTEOR_SYNC_PROTOCOL.md`, `NOTEOR_SYNC_CRYPTO.md`, `NOTEOR_SYNC_DATA_MODEL.md`) ; implémentation Web dans `web/src/sync/` ; module Desktop dans `sync/desktop/` (`crypto.py`, `sqlite_sync_adapter.py`, `engine.py`) ; serveur dans `sync-server/` ; outil graphique `.deb` dans `sync-server-manager/` ; vecteurs et tests dans `tests/`. |
| **Identifiants & Révisions (`§5.2`, `§7.3`)** | `revision` entier (`1 -> 2`), `parent_revision` | Graphe orienté acyclique (DAG) identifié par UUID v4 (`revision_id`, `parent_revision_ids: string[]`). |
| **Médias & Pièces jointes (`§5.7`)** | `encrypted_object_id`, `encrypted_hash` | `media_id` (UUID v4), `encrypted_size`, `encrypted_sha256`, `media_crypto_version: 1`, `key_epoch`, format binaire `NTRM1` (`crypto_secretstream_xchacha20poly1305`). |
| **Enveloppe & Révocation (`§9.4`, `§9.7`)** | `key_id`, dérivation directe depuis `VSK` | Champ obligatoire `key_epoch >= 1` authentifié dans l'AAD RFC 8785. Pour toute rotation `epoch >= 2`, `EpochRootKey(epoch)` est générée aléatoirement par CSPRNG et scellée par appareil actif via X25519 `crypto_box_seal`. |
| **Endpoints HTTP (`§12.2`)** | `/devices/pair`, `/sync/push`, `/sync/pull`, `/devices/ack`, `/media/uploads/{id}/chunks/{n}` | Routes unifiées sous `/api/v1/vaults/{vault_id}/...` (`operations:batch`, `operations?since_seq=...`, `pairing/*`, `devices`, `devices/{id}/revoke`, `keys/epochs`, `media/uploads/*`, `media/{id}`), sans route `/devices/ack` (cf. `NOTEOR_SYNC_PROTOCOL.md §4`). |
| **Hébergement PWA (`§2.1`, `§15`)** | Déploiement Netlify uniquement | Support dual **Cloudflare Pages** (`web/wrangler.toml`, `web/public/_headers`) et **Netlify** (`web/netlify.toml`) avec CSP autorisant `'wasm-unsafe-eval'` pour Argon2id/libsodium WASM. |
| **Documents complémentaires (`§23`)** | Noms prévisionnels (`NOTEOR_SYNC_SECURITY.md`, etc.) | Quatre documents normatifs unifiés dans `docs/sync/` : `NOTEOR_SYNC_ARCHITECTURE.md`, `NOTEOR_SYNC_PROTOCOL.md`, `NOTEOR_SYNC_CRYPTO.md`, `NOTEOR_SYNC_DATA_MODEL.md`, complétés par `NOTEOR_SYNC_CONFORMANCE_REPORT.md` et `docs/DEPLOYMENT.md`. |

**Fin du document — NOTEOR_SYNC_ARCHITECTURE.md — Version 1.1**
