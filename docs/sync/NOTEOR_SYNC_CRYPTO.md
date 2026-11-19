# NOTEOR_SYNC_CRYPTO.md (v1.1 — Spécification cryptographique révisée)

**Version :** 1.1 — 19 novembre 2026  
**Statut :** Spécification interopérable validée par vecteurs croisés Python / TypeScript  

---

## 1. Algorithmes normatifs V1.1

| Usage | Algorithme | Détail |
|---|---|---|
| Protection mot de passe (`KEK`) | Argon2id v1.3 (`alg = 2`) | `65 536 KiB` (64 MiB), `t = 3`, `p = 1`, sel `16 octets`, sortie `32 octets` |
| Dérivation de clés | HKDF-SHA-256 (RFC 5869) | Sortie `32 octets`, UUID encodés en `16 octets` big-endian (RFC 4122) |
| Enveloppes AEAD & Opérations | `XChaCha20-Poly1305-IETF` | Clé `32 octets`, Nonce CSPRNG `24 octets`, Tag `16 octets` (`CIPHERTEXT \|\| TAG`) |
| Chiffrement des médias en flux | `crypto_secretstream_xchacha20poly1305` | Header `24 octets`, tag final `TAG_FINAL`, chunk clair max `4 MiB` |
| Enveloppe de clés d'époque par appareil | `crypto_box_seal` (X25519 + XSalsa20-Poly1305 / Blake2b) | Chaque appareil possède une paire de clés X25519 générée localement |
| Empreintes | SHA-256 | Sur blob média chiffré complet + hash des jetons d'accès serveur |
| Canonisation AAD | RFC 8785 (JCS) | Sérialisation JSON canonique stricte en UTF-8 |

---

## 2. Hiérarchie des clés corrigée (Correctif C3 — Révocation effective)

Dans la v1.0, dériver `K_OP(epoch)` de manière déterministe depuis `VSK` permettait à un appareil révoqué connaissant le `VSK` de calculer lui-même les clés d'époques suivantes. La v1.1 corrige cette faille ainsi :

1. **`VSK` (Vault Secret Key, 32 octets aléatoires)** :
   - Protégé par le mot de passe utilisateur (`KEK` Argon2id → enveloppe `VSK`) et par le secret de récupération 256 bits.
   - Sert à initialiser/envelopper `EpochRootKey(1)` à la création du coffre (`HKDF-SHA256(VSK, UUID_BYTES(vault_id), "NOTEOR/SYNC/V1/EPOCH-1-SEED", 32)` ou aléatoire 32 octets) et à signer/envelopper les sauvegardes de récupération.
2. **`EpochRootKey(epoch)` (32 octets aléatoires pour chaque rotation `epoch >= 2`)** :
   - Lors d'une révocation d'appareil ou rotation de clé, l'appareil initiateur génère une nouvelle `EpochRootKey(new_epoch)` **aléatoire par CSPRNG** (indépendante de `VSK` et des époques précédentes).
   - Elle est scellée individuellement pour chaque appareil autorisé non révoqué à l'aide de sa clé publique X25519 (`crypto_box_seal`) avec contexte `NOTEOR/SYNC/V1/DEVICE-WRAP` authentifié à l'intérieur du paquet scellé (`{ "domain": "NOTEOR/SYNC/V1/DEVICE-WRAP", "vault_id", "key_epoch", "target_device_id", "epoch_root_key_b64u" }`).
   - L'appareil révoqué **ne peut ni dériver ni déchiffrer `EpochRootKey(new_epoch)`**.
3. **Clés dérivées depuis `EpochRootKey(epoch)` via HKDF-SHA-256 :**
   - `K_OP(epoch) = HKDF-SHA256(IKM = EpochRootKey(epoch), salt = UUID_BYTES(vault_id), info = UTF8("NOTEOR/SYNC/V1/OPERATIONS/EPOCH/" + epoch), L = 32)`
   - `K_MEDIA_EPOCH(epoch) = HKDF-SHA256(IKM = EpochRootKey(epoch), salt = UUID_BYTES(vault_id), info = UTF8("NOTEOR/SYNC/V1/MEDIA/EPOCH/" + epoch), L = 32)`
   - `K_MEDIA(media_id) = HKDF-SHA256(IKM = K_MEDIA_EPOCH(epoch), salt = UUID_BYTES(media_id), info = UTF8("NOTEOR/SYNC/V1/MEDIA/OBJECT"), L = 32)`

---

## 3. Format binaire normatif des médias `NTRM1` (Correctif C4 & C5)

Le blob binaire chiffré d'un média possède exactement l'en-tête fixe de **49 octets** suivi des messages `crypto_secretstream_xchacha20poly1305` :

```text
Offset  Taille  Champ
0       4       Magic = b"NTRM"
4       1       media_crypto_version = 1 (uint8)
5       16      media_id (UUID v4, 16 octets big-endian)
21      4       key_epoch (uint32 big-endian)
25      24      secretstream_header (24 octets issus de init_push)
49      ...     Frames séquentielles: [uint32_be encrypted_frame_len][encrypted_frame_bytes]
```
- Chaque frame chiffre au plus `chunk_size = 4 194 304` octets (4 MiB) de texte clair.
- L'AAD passée au tout premier chunk (ou à chaque appel `push`/`pull`) est exactement les **49 octets d'en-tête `header[0..49]`** (liant cryptographiquement `Magic`, `media_crypto_version`, `media_id`, `key_epoch` et `secretstream_header`).
- La dernière frame DOIT porter `crypto_secretstream_xchacha20poly1305_TAG_FINAL` (`tag = 3`), et le déchiffreur DOIT vérifier l'absence de données résiduelles après la frame finale.
- Côté réseau (`PUT`), le client découpe simplement ce flux binaire complet en tranches HTTP de ≤ `4 194 304` octets.

---

## 4. Protocole cryptographique de jumelage (Correctif C11)

1. L'appareil A génère `pairing_id` (UUID v4), `pairing_secret` (32 octets aléatoires), `pairing_nonce` (32 octets aléatoires), `expires_at` (`now + 120s`).
2. Le QR code contient (JSON sans secret serveur) : `{ "pairing_version": 1, "vault_id", "pairing_id", "pairing_secret": BASE64URL_NOPAD, "pairing_nonce": BASE64URL_NOPAD, "expires_at", "server_url"? }`.
3. A enregistre auprès du serveur `SHA256(pairing_secret)` + `device_a_id` + `device_a_pub_key`.
4. B scanne le QR code, génère sa paire X25519 (`device_b_pub_key`, `device_b_priv_key`), son `device_b_id` (UUID v4), `session_nonce_b` (24 octets aléatoires) et calcule `secret_proof_hmac = HMAC-SHA256(key=pairing_secret, msg=UTF8(vault_id + ":" + pairing_id + ":" + device_b_id + ":" + session_nonce_b_b64u))`.
5. Dérivation liée au contexte complet :
   ```text
   K_PAIR = HKDF-SHA256(
     IKM  = pairing_secret,
     salt = pairing_nonce,
     info = UTF8("NOTEOR/SYNC/V1/PAIRING/" + vault_id + "/" + pairing_id + "/" + device_a_id + "/" + device_b_id + "/" + session_nonce_b_b64u),
     L    = 32
   )
   ```
6. A vérifie la demande, affiche les informations de B à l'utilisateur pour approbation explicite, puis chiffre le paquet de jumelage (`{ vault_id, vsk_b64u, current_key_epoch, epoch_keys }`) avec `XChaCha20-Poly1305-IETF(K_PAIR, nonce_24, plaintext, AAD = RFC8785({ pairing_version: 1, vault_id, pairing_id, device_a_id, device_b_id, session_nonce_b }))`.
7. B déchiffre, vérifie `vault_id`, stocke le matériel localement et envoie `confirmation_hmac = HMAC-SHA256(key=K_PAIR, msg=UTF8("CONFIRM:" + pairing_id + ":" + device_b_id + ":" + vault_id))` au serveur pour activer son compte appareil et recevoir son `DEVICE_ACCESS_TOKEN`.

---

## 5. Vecteur HKDF-SHA-256 corrigé — RFC 5869 Annexe A.1 (Correctif C7)

```json
{
  "ikm_hex": "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
  "salt_hex": "000102030405060708090a0b0c",
  "info_hex": "f0f1f2f3f4f5f6f7f8f9",
  "length": 42,
  "expected_prk_hex": "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5",
  "expected_okm_hex": "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
}
```
Les vecteurs croisés complets (Argon2id, HKDF, XChaCha20-Poly1305 + AAD JCS, `NTRM1` secretstream, pairing/epoch-wrap et cas invalides) sont stockés et exécutés automatiquement dans `tests/crypto-vectors/`.
