# Noteor Sync — Vecteurs de test cryptographiques croisés Python / TypeScript

Ce répertoire contient les vecteurs déterministes partagés entre :
- `sync/desktop/crypto.py` (Python + libsodium / PyNaCl)
- `web/src/sync/crypto.ts` (TypeScript + libsodium-wrappers-sumo + `@noble/hashes` + `canonicalize`)

## Suites couvertes
- `argon2id/` : dérivation Argon2id13 (`65 536 KiB`, `t=3`, `p=1`, UTF-8 brut sans normalisation Unicode).
- `hkdf/` : RFC 5869 Annexe A.1 + dérivations Noteor (`EpochRootKey`, `K_OP`, `K_MEDIA_EPOCH`, `K_MEDIA`).
- `xchacha20poly1305/` : enveloppes d'opérations v1.1 avec `key_epoch` et AAD RFC 8785 (JCS), plus enveloppe `VSK`.
- `secretstream/` : format binaire média `NTRM1` multi-fragments (`crypto_secretstream_xchacha20poly1305`).
- `pairing/` : dérivation `K_PAIR` liée au contexte, paquet de jumelage AEAD, confirmation HMAC et scellement X25519 (`crypto_box_seal`) d'une clé d'époque `key_epoch = 2` excluant un appareil révoqué.
- `malformed/` : cas de rejet obligatoires (padding `=`, AAD altérée, mauvaise époque, tag corrompu, flux média tronqué ou données après `TAG_FINAL`).
