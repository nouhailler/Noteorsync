# NOTEOR SYNC — MODÈLE DE DONNÉES COMMUN (v1.1)

**Document :** `NOTEOR_SYNC_DATA_MODEL.md`  
**Version :** 1.1 — 19 novembre 2026  
**Statut :** Contrat normatif commun Desktop / PWA  

---

## 1. Principes généraux

1. **Identifiants globaux :** toute entité synchronisable possède un `entity_id` (`sync_id` en base locale) de type **UUID v4 minuscule canonique** (`xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`). Les clés primaires numériques locales (`id`) restent strictement locales et ne traversent jamais le réseau.
2. **Horodatages :** tous les champs `created_at`, `updated_at`, `deleted_at` du protocole utilisent **UTC RFC 3339 / ISO 8601 avec millisecondes et suffixe `Z`** (`2026-11-19T12:41:00.000Z`).
3. **Encodage binaire :** toute donnée binaire en JSON utilise **Base64 URL-safe sans remplissage `=`** (`[A-Za-z0-9_-]`).
4. **Révisions :** chaque entité maintient un graphe orienté acyclique (DAG) de révisions identifiées par `revision_id` (UUID v4) et `parent_revision_ids` (liste d'UUID v4).

---

## 2. Schémas des entités (`data` dans le payload chiffré)

### 2.1 `note` (`entity_type = "note"`)
```json
{
  "title": "Ma note",
  "content": "Contenu Markdown avec [[Autre note|@550e8400-e29b-41d4-a716-446655440099]]",
  "category_id": "550e8400-e29b-41d4-a716-446655440020",
  "folder_id": "550e8400-e29b-41d4-a716-446655440030",
  "is_deleted": false,
  "deleted_at": null,
  "created_at": "2026-11-19T12:00:00.000Z",
  "updated_at": "2026-11-19T12:41:00.000Z",
  "metadata": {}
}
```
- `category_id` et `folder_id` sont soit `null`, soit l'UUID global de la catégorie / du dossier.
- **Corbeille :** la mise à la corbeille est représentée par `entity.trash` (ou `entity.upsert` avec `is_deleted: true, deleted_at: "..."`), et la sortie de corbeille par `entity.restore` (`is_deleted: false, deleted_at: null`).
- **Suppression définitive :** produite par `entity.delete` (`{ "deleted_at": "..." }`), créant un tombstone permanent non restaurable automatiquement.

### 2.2 `category` (`entity_type = "category"`)
```json
{
  "name": "Travail",
  "parent_id": null,
  "color": "#4F46E5",
  "sort_order": 0,
  "created_at": "2026-11-19T12:00:00.000Z",
  "updated_at": "2026-11-19T12:00:00.000Z"
}
```

### 2.3 `folder` (`entity_type = "folder"`)
```json
{
  "name": "Projets",
  "parent_id": null,
  "created_at": "2026-11-19T12:00:00.000Z",
  "updated_at": "2026-11-19T12:00:00.000Z"
}
```

### 2.4 `tag` (`entity_type = "tag"`)
```json
{
  "name": "urgent",
  "color": "#6366F1",
  "created_at": "2026-11-19T12:00:00.000Z",
  "updated_at": "2026-11-19T12:00:00.000Z"
}
```
- **Normalisation casse/Unicode :** `name` est normalisé en NFC + `trim()` + lowercase/casefold Unicode (`tagName.normalize('NFC').trim().toLowerCase()` en TS, `unicodedata.normalize('NFC', name).strip().casefold()` en Python).
- Si deux appareils créent hors ligne deux tags de même nom normalisé avec des `entity_id` distincts, le client conserve l'entité canonique déterminée par le plus petit UUID lexicographique (`min(uuidA, uuidB)`) et remappe localement les relations `note_tag` vers cet UUID canonique.

### 2.5 `relation` (`entity_type = "relation"`, liaison Note ↔ Tag)
Les relations note-tag sont des **entités autonomes** afin d'éviter des conflits inutiles lors de l'ajout simultané d'un tag et de l'édition du texte d'une note :
```json
{
  "relation_type": "note_tag",
  "note_id": "550e8400-e29b-41d4-a716-446655440010",
  "tag_id": "550e8400-e29b-41d4-a716-446655440040",
  "created_at": "2026-11-19T12:00:00.000Z"
}
```
- Le retrait d'un tag produit `entity.delete` sur l'`entity_id` de la relation.

### 2.6 `attachment` (`entity_type = "attachment"`)
- Enregistrement par opération `media.register` :
```json
{
  "note_id": "550e8400-e29b-41d4-a716-446655440010",
  "media_id": "550e8400-e29b-41d4-a716-446655440022",
  "media_type": "image",
  "media_crypto_version": 1,
  "key_epoch": 1,
  "encrypted_size": 245760,
  "encrypted_sha256": "lowercase_hex_64_chars",
  "original_size": 245700,
  "mime_type": "image/jpeg",
  "file_name": "photo.jpg",
  "duration": null,
  "thumbnail_data_url": "data:image/jpeg;base64,...",
  "created_at": "2026-11-19T12:50:00.000Z"
}
```
- **Suppression :** `media.delete` avec `entity_id` de la pièce jointe et `media_id`.
- **Remarque :** `media_nonce` est supprimé car `crypto_secretstream_xchacha20poly1305` transporte son en-tête de 24 octets dans le flux binaire chiffré `NTRM`.
- Les miniatures légères peuvent être transportées dans le payload chiffré (`thumbnail_data_url`, plafonné à 128 Ko) ou régénérées localement à partir du média déchiffré.

---

## 3. Format canonique des Wikilinks

- **Format canonique V1 :** `[[Titre affiché|@<uuid-v4>]]`  
  Exemple : `[[Architecture Sync|@550e8400-e29b-41d4-a716-446655440010]]`
- **Formats rétrocompatibles lus par les parseurs :**
  1. `[[Titre|@<uuid-v4>]]` → résolution directe par `sync_id`.
  2. `[[Titre|<digits>]]` → format historique local, migré vers `@<uuid>` lors de la migration locale v2.
  3. `[[Titre]]` → résolution insensible à la casse par `title`.
- Lorsqu'une note est renommée (`updateWikilinksOnRename`), chaque note référençante dont le contenu change produit une opération `entity.upsert` distincte dans la même transaction locale.

---

## 4. Règles de cascades et dépendances manquantes

1. **Suppression d'une catégorie ou d'un dossier :** le client qui supprime met à jour localement `category_id = null` / `folder_id = null` (et `parent_id = null` sur les enfants directs) et génère les opérations `entity.upsert` correspondantes dans la même transaction, puis `entity.delete` sur la catégorie/le dossier.
2. **Dépendances manquantes à la réception :** si une opération `attachment`, `relation` ou `note` référence un `note_id` / `tag_id` / `category_id` / `folder_id` non encore présent localement, l'opération est placée en file `sync_pending_ops` et réévaluée dès qu'une entité manquante arrive (sans bloquer l'avancement du curseur pour les autres entités indépendantes, mais en conservant l'opération durablement).

---

## 5. Règles de résolution des conflits (DAG de révisions)

Deux révisions `R_local` et `R_remote` sont **concurrentes** si aucune n'est ancêtre de l'autre dans `parent_revision_ids`.

| Type d'entité | Cas concurrent | Stratégie V1 |
|---|---|---|
| `note` | Contenu ou métadonnées différentes (`title`, `content`, `category_id`, `folder_id`) | **Pas d'écrasement silencieux.** Création d'une entrée dans `sync_conflicts` conservant le snapshot local et le snapshot distant. L'utilisateur choisit *Conserver local*, *Conserver distant*, *Conserver les deux (dupliquer)* ou *Fusion manuelle* (produisant un nouvel `entity.upsert` avec `parent_revision_ids = [R_local, R_remote]`). |
| `note` | Suppression définitive (`entity.delete`) vs modification | Conservation en conflit explicite avant toute purge : la branche éditée reste récupérable dans `sync_conflicts`. |
| `note` | Corbeille (`is_deleted=true`) vs édition | Conflit explicite présenté à l'utilisateur (éviter de perdre une édition hors ligne). |
| `note` | Snapshots strictement identiques | Auto-convergence silencieuse vers une révision de fusion `[R_local, R_remote]`. |
| `relation` (`note_tag`) | Ajout/retrait concurrent sur une même relation | Dernière opération serveur gagnante sur le lien ou recréation explicite ; pas de perte de la note. |
| `category` / `folder` | Renommage concurrent | Conflit enregistré sur l'entité ; état courant conservé sans perte de notes rattachées. |
| `tag` | Création concurrente du même nom | Fusion déterministe sur le plus petit UUID (cf. §2.4). |
