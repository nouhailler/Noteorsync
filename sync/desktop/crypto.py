"""Noteor Sync v1.1 Python cryptographic module.

Uses PyNaCl (nacl.bindings) when available, or directly binds system libsodium.so.23 via ctypes.
Implements HKDF-SHA-256 (RFC 5869), Argon2id13 (65536 KiB, t=3, p=1),
XChaCha20-Poly1305-IETF with RFC 8785 (JCS) AAD, NTRM1 secretstream,
X25519 crypto_box_seal epoch wrapping, and pairing cryptography.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import hmac
import json
import math
import os
import re
import struct
import uuid as uuid_mod
from typing import Any

CRYPTO_VERSION = 1
PROTOCOL_VERSION = 1
MEDIA_CRYPTO_VERSION = 1
MEDIA_MAGIC = b"NTRM"
MEDIA_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB plaintext
ARGON2ID_MEM_KIB = 65536
ARGON2ID_MEM_BYTES = ARGON2ID_MEM_KIB * 1024  # 67108864
ARGON2ID_ITERATIONS = 3
ARGON2ID_PARALLELISM = 1

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_B64U_NOPAD_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SEQ_RE = re.compile(r"^[1-9][0-9]*$")


class _SodiumBackend:
    """Thin binding over system libsodium (or PyNaCl if installed)."""

    def __init__(self) -> None:
        lib_name = ctypes.util.find_library("sodium") or "libsodium.so.23"
        try:
            lib = ctypes.CDLL(lib_name)
        except OSError:
            lib = ctypes.CDLL("/usr/lib/x86_64-linux-gnu/libsodium.so.23")
        if lib.sodium_init() < 0:
            raise RuntimeError("Failed to initialize libsodium")
        self.lib = lib
        self._configure()

    def _configure(self) -> None:
        L = self.lib
        L.crypto_pwhash_alg_argon2id13.restype = ctypes.c_int
        L.crypto_pwhash.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_char_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        L.crypto_pwhash.restype = ctypes.c_int

        L.crypto_aead_xchacha20poly1305_ietf_encrypt.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        L.crypto_aead_xchacha20poly1305_ietf_encrypt.restype = ctypes.c_int

        L.crypto_aead_xchacha20poly1305_ietf_decrypt.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        L.crypto_aead_xchacha20poly1305_ietf_decrypt.restype = ctypes.c_int

        L.crypto_secretstream_xchacha20poly1305_statebytes.restype = ctypes.c_size_t
        L.crypto_secretstream_xchacha20poly1305_headerbytes.restype = ctypes.c_size_t
        L.crypto_secretstream_xchacha20poly1305_abytes.restype = ctypes.c_size_t
        L.crypto_secretstream_xchacha20poly1305_tag_message.restype = ctypes.c_ubyte
        L.crypto_secretstream_xchacha20poly1305_tag_final.restype = ctypes.c_ubyte

        L.crypto_secretstream_xchacha20poly1305_init_push.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        L.crypto_secretstream_xchacha20poly1305_init_push.restype = ctypes.c_int

        L.crypto_secretstream_xchacha20poly1305_push.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_ubyte,
        ]
        L.crypto_secretstream_xchacha20poly1305_push.restype = ctypes.c_int

        L.crypto_secretstream_xchacha20poly1305_init_pull.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        L.crypto_secretstream_xchacha20poly1305_init_pull.restype = ctypes.c_int

        L.crypto_secretstream_xchacha20poly1305_pull.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
        ]
        L.crypto_secretstream_xchacha20poly1305_pull.restype = ctypes.c_int

        L.crypto_box_publickeybytes.restype = ctypes.c_size_t
        L.crypto_box_secretkeybytes.restype = ctypes.c_size_t
        L.crypto_box_sealbytes.restype = ctypes.c_size_t
        L.crypto_box_keypair.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        L.crypto_box_keypair.restype = ctypes.c_int
        L.crypto_box_seal.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_void_p]
        L.crypto_box_seal.restype = ctypes.c_int
        L.crypto_box_seal_open.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulonglong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        L.crypto_box_seal_open.restype = ctypes.c_int


_SODIUM: _SodiumBackend | None = None


def _get_sodium() -> _SodiumBackend:
    global _SODIUM
    if _SODIUM is None:
        _SODIUM = _SodiumBackend()
    return _SODIUM


def _as_c_buf(data: bytes) -> Any:
    return ctypes.create_string_buffer(data, len(data)) if len(data) > 0 else ctypes.c_void_p(0)


def random_bytes(n: int) -> bytes:
    return os.urandom(n)


def generate_uuid_v4() -> str:
    return str(uuid_mod.uuid4()).lower()


def assert_uuid(value: str) -> None:
    if not isinstance(value, str) or not _UUID_RE.match(value):
        raise ValueError(f"Invalid canonical lowercase UUID: {value!r}")


def uuid_to_bytes(value: str) -> bytes:
    assert_uuid(value)
    return uuid_mod.UUID(value).bytes


def bytes_to_uuid(raw: bytes) -> str:
    if len(raw) != 16:
        raise ValueError(f"Expected 16 UUID bytes, got {len(raw)}")
    return str(uuid_mod.UUID(bytes=raw)).lower()


def to_b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_b64u(text: str) -> bytes:
    if not isinstance(text, str) or not text or not _B64U_NOPAD_RE.match(text):
        raise ValueError("Invalid unpadded Base64URL string")
    pad = "=" * ((4 - len(text) % 4) % 4)
    decoded = base64.urlsafe_b64decode((text + pad).encode("ascii"))
    if to_b64u(decoded) != text:
        raise ValueError("Non-canonical unpadded Base64URL encoding")
    return decoded


def sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    hash_len = 32
    if not salt:
        salt = b"\x00" * hash_len
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = bytearray()
    t = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm.extend(t)
        counter += 1
    return bytes(okm[:length])


# ── RFC 8785 (JCS) JSON Canonicalization ─────────────────────────────────────


def _jcs_encode_string(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _jcs_encode_number(n: int | float) -> str:
    if isinstance(n, bool):
        raise TypeError("bool is not a JSON number")
    if isinstance(n, int):
        # Match IEEE 754 JSON behavior for integers; Python arbitrary-precision ints serialize directly
        return str(n)
    if math.isnan(n) or math.isinf(n):
        raise ValueError("RFC 8785 forbids NaN/Infinity")
    if n == 0.0:
        return "0"

    sign = "-" if n < 0 else ""
    s = repr(abs(float(n)))
    if "e" in s or "E" in s:
        mant, exp_str = s.lower().split("e")
        exp_val = int(exp_str)
        int_part, _, frac_part = mant.partition(".")
        raw_digits = int_part + frac_part
        n_pos = exp_val + len(int_part)
    else:
        int_part, _, frac_part = s.partition(".")
        if int_part != "0":
            raw_digits = int_part + frac_part
            n_pos = len(int_part)
        else:
            stripped = frac_part.lstrip("0")
            leading_zeros = len(frac_part) - len(stripped)
            raw_digits = stripped
            n_pos = -leading_zeros

    digits = raw_digits.rstrip("0")
    k = len(digits)

    # ECMA-262 / RFC 8785 Number::toString(10)
    if k <= n_pos <= 21:
        body = digits + ("0" * (n_pos - k))
    elif 0 < n_pos <= 21:
        body = digits[:n_pos] + "." + digits[n_pos:]
    elif -6 < n_pos <= 0:
        body = "0." + ("0" * (-n_pos)) + digits
    else:
        e = n_pos - 1
        e_sign = "+" if e >= 0 else "-"
        if k == 1:
            body = f"{digits}e{e_sign}{abs(e)}"
        else:
            body = f"{digits[0]}.{digits[1:]}e{e_sign}{abs(e)}"
    return sign + body


def _jcs_serialize(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _jcs_encode_number(value)
    if isinstance(value, str):
        return _jcs_encode_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_jcs_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        # RFC 8785 sorts object keys by UTF-16 code units
        items = sorted(value.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        return "{" + ",".join(f"{_jcs_encode_string(k)}:{_jcs_serialize(v)}" for k, v in items) + "}"
    raise TypeError(f"Unsupported JCS type: {type(value)}")


def canonical_json_bytes(value: Any) -> bytes:
    return _jcs_serialize(value).encode("utf-8")


# ── Argon2id13 ─────────────────────────────────────────────────────────────────


def derive_argon2id_kek(
    password_utf8: str,
    salt16: bytes,
    memory_kib: int = ARGON2ID_MEM_KIB,
    iterations: int = ARGON2ID_ITERATIONS,
    parallelism: int = ARGON2ID_PARALLELISM,
) -> bytes:
    if len(salt16) != 16:
        raise ValueError(f"Argon2id salt must be 16 bytes, got {len(salt16)}")
    if parallelism != 1:
        raise ValueError(f"Unsupported Argon2id parallelism={parallelism}; V1 requires p=1")
    if memory_kib < 8192 or iterations < 1:
        raise ValueError("Unsafe Argon2id parameters")

    s = _get_sodium()
    pw_bytes = password_utf8.encode("utf-8")
    out = ctypes.create_string_buffer(32)
    pw_buf = ctypes.create_string_buffer(pw_bytes, len(pw_bytes))
    salt_buf = ctypes.create_string_buffer(salt16, 16)
    rc = s.lib.crypto_pwhash(
        out,
        32,
        pw_buf,
        len(pw_bytes),
        salt_buf,
        iterations,
        memory_kib * 1024,
        s.lib.crypto_pwhash_alg_argon2id13(),
    )
    if rc != 0:
        raise RuntimeError("crypto_pwhash failed")
    return out.raw


# ── Key Hierarchy ──────────────────────────────────────────────────────────────


def derive_initial_epoch1_root_key(vsk: bytes, vault_id: str) -> bytes:
    if len(vsk) != 32:
        raise ValueError("VSK must be 32 bytes")
    return hkdf_sha256(
        vsk,
        uuid_to_bytes(vault_id),
        b"NOTEOR/SYNC/V1/EPOCH-1-SEED",
        32,
    )


def derive_epoch_keys(epoch_root_key: bytes, vault_id: str, key_epoch: int) -> tuple[bytes, bytes]:
    if len(epoch_root_key) != 32:
        raise ValueError("EpochRootKey must be 32 bytes")
    if not isinstance(key_epoch, int) or key_epoch < 1:
        raise ValueError("key_epoch must be >= 1")
    salt = uuid_to_bytes(vault_id)
    k_op = hkdf_sha256(
        epoch_root_key,
        salt,
        f"NOTEOR/SYNC/V1/OPERATIONS/EPOCH/{key_epoch}".encode("utf-8"),
        32,
    )
    k_media_epoch = hkdf_sha256(
        epoch_root_key,
        salt,
        f"NOTEOR/SYNC/V1/MEDIA/EPOCH/{key_epoch}".encode("utf-8"),
        32,
    )
    return k_op, k_media_epoch


def derive_media_object_key(k_media_epoch: bytes, media_id: str) -> bytes:
    if len(k_media_epoch) != 32:
        raise ValueError("k_media_epoch must be 32 bytes")
    return hkdf_sha256(
        k_media_epoch,
        uuid_to_bytes(media_id),
        b"NOTEOR/SYNC/V1/MEDIA/OBJECT",
        32,
    )


# ── XChaCha20-Poly1305-IETF AEAD ───────────────────────────────────────────────


def aead_encrypt(
    key32: bytes,
    plaintext: bytes,
    aad: bytes,
    nonce_override: bytes | None = None,
) -> tuple[bytes, bytes]:
    if len(key32) != 32:
        raise ValueError("AEAD key must be 32 bytes")
    nonce = nonce_override if nonce_override is not None else random_bytes(24)
    if len(nonce) != 24:
        raise ValueError("AEAD nonce must be 24 bytes")

    s = _get_sodium()
    ct = ctypes.create_string_buffer(len(plaintext) + 16)
    ct_len = ctypes.c_ulonglong(0)
    rc = s.lib.crypto_aead_xchacha20poly1305_ietf_encrypt(
        ct,
        ctypes.byref(ct_len),
        _as_c_buf(plaintext),
        len(plaintext),
        _as_c_buf(aad),
        len(aad),
        None,
        _as_c_buf(nonce),
        _as_c_buf(key32),
    )
    if rc != 0:
        raise RuntimeError("AEAD encryption failed")
    return nonce, ct.raw[: ct_len.value]


def aead_decrypt(key32: bytes, nonce24: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    if len(key32) != 32:
        raise ValueError("AEAD key must be 32 bytes")
    if len(nonce24) != 24:
        raise ValueError("AEAD nonce must be 24 bytes")
    if len(ciphertext) < 16:
        raise ValueError("Ciphertext shorter than 16-byte tag")

    s = _get_sodium()
    pt = ctypes.create_string_buffer(max(1, len(ciphertext) - 16))
    pt_len = ctypes.c_ulonglong(0)
    rc = s.lib.crypto_aead_xchacha20poly1305_ietf_decrypt(
        pt,
        ctypes.byref(pt_len),
        None,
        _as_c_buf(ciphertext),
        len(ciphertext),
        _as_c_buf(aad),
        len(aad),
        _as_c_buf(nonce24),
        _as_c_buf(key32),
    )
    if rc != 0:
        raise ValueError("AEAD authentication failed")
    return pt.raw[: pt_len.value]


def validate_envelope_fields(env: dict[str, Any]) -> None:
    if env.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported protocol_version: {env.get('protocol_version')}")
    if env.get("crypto_version") != CRYPTO_VERSION:
        raise ValueError(f"Unsupported crypto_version: {env.get('crypto_version')}")
    key_epoch = env.get("key_epoch")
    if not isinstance(key_epoch, int) or isinstance(key_epoch, bool) or key_epoch < 1:
        raise ValueError(f"Invalid key_epoch: {key_epoch!r}")
    assert_uuid(env["vault_id"])
    assert_uuid(env["device_id"])
    assert_uuid(env["operation_id"])
    if not isinstance(env.get("device_seq"), str) or not _SEQ_RE.match(env["device_seq"]):
        raise ValueError(f"Invalid device_seq: {env.get('device_seq')!r}")


def build_operation_aad(header: dict[str, Any]) -> bytes:
    return canonical_json_bytes(
        {
            "crypto_version": header["crypto_version"],
            "device_id": header["device_id"],
            "device_seq": header["device_seq"],
            "key_epoch": header["key_epoch"],
            "operation_id": header["operation_id"],
            "protocol_version": header["protocol_version"],
            "vault_id": header["vault_id"],
        }
    )


def encrypt_operation_envelope(
    k_op: bytes,
    meta: dict[str, Any],
    payload: dict[str, Any],
    nonce_override: bytes | None = None,
) -> dict[str, Any]:
    header = {
        "protocol_version": meta.get("protocol_version", PROTOCOL_VERSION),
        "crypto_version": meta.get("crypto_version", CRYPTO_VERSION),
        "key_epoch": meta["key_epoch"],
        "vault_id": meta["vault_id"],
        "device_id": meta["device_id"],
        "operation_id": meta["operation_id"],
        "device_seq": meta["device_seq"],
    }
    validate_envelope_fields(header)
    aad = build_operation_aad(header)
    plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    nonce, ciphertext = aead_encrypt(k_op, plaintext, aad, nonce_override=nonce_override)
    return {
        **header,
        "nonce": to_b64u(nonce),
        "ciphertext": to_b64u(ciphertext),
    }


def decrypt_operation_envelope(k_op: bytes, envelope: dict[str, Any]) -> dict[str, Any]:
    validate_envelope_fields(envelope)
    nonce = from_b64u(envelope["nonce"])
    if len(nonce) != 24:
        raise ValueError(f"Invalid nonce length: {len(nonce)}")
    ciphertext = from_b64u(envelope["ciphertext"])
    aad = build_operation_aad(envelope)
    plaintext = aead_decrypt(k_op, nonce, ciphertext, aad)
    parsed = json.loads(plaintext.decode("utf-8"))
    if parsed.get("payload_version") != 1:
        raise ValueError(f"Unsupported payload_version: {parsed.get('payload_version')}")
    assert_uuid(parsed["entity_id"])
    assert_uuid(parsed["revision_id"])
    for p in parsed.get("parent_revision_ids", []):
        assert_uuid(p)
    return parsed


def encrypt_vsk_envelope(
    vsk: bytes,
    vault_id: str,
    password: str,
    salt_override: bytes | None = None,
    nonce_override: bytes | None = None,
) -> dict[str, Any]:
    if len(vsk) != 32:
        raise ValueError("VSK must be 32 bytes")
    assert_uuid(vault_id)
    salt = salt_override if salt_override is not None else random_bytes(16)
    kek = derive_argon2id_kek(password, salt)
    aad = canonical_json_bytes(
        {
            "envelope_version": 1,
            "kdf": "argon2id13",
            "purpose": "vault-recovery",
            "vault_id": vault_id,
        }
    )
    nonce, ciphertext = aead_encrypt(kek, vsk, aad, nonce_override=nonce_override)
    return {
        "envelope_version": 1,
        "kdf": {
            "algorithm": "argon2id13",
            "salt": to_b64u(salt),
            "memory_kib": ARGON2ID_MEM_KIB,
            "iterations": ARGON2ID_ITERATIONS,
            "parallelism": ARGON2ID_PARALLELISM,
            "output_length": 32,
        },
        "aead": {
            "algorithm": "xchacha20poly1305-ietf",
            "nonce": to_b64u(nonce),
            "ciphertext": to_b64u(ciphertext),
        },
    }


def decrypt_vsk_envelope(envelope: dict[str, Any], vault_id: str, password: str) -> bytes:
    if envelope.get("envelope_version") != 1 or envelope.get("kdf", {}).get("algorithm") != "argon2id13":
        raise ValueError("Unsupported VSK envelope version or KDF")
    assert_uuid(vault_id)
    kdf_info = envelope["kdf"]
    aead_info = envelope["aead"]
    salt = from_b64u(kdf_info["salt"])
    nonce = from_b64u(aead_info["nonce"])
    ciphertext = from_b64u(aead_info["ciphertext"])
    kek = derive_argon2id_kek(
        password,
        salt,
        memory_kib=kdf_info["memory_kib"],
        iterations=kdf_info["iterations"],
        parallelism=kdf_info["parallelism"],
    )
    aad = canonical_json_bytes(
        {
            "envelope_version": 1,
            "kdf": "argon2id13",
            "purpose": "vault-recovery",
            "vault_id": vault_id,
        }
    )
    vsk = aead_decrypt(kek, nonce, ciphertext, aad)
    if len(vsk) != 32:
        raise ValueError("Invalid decrypted VSK length")
    return vsk


# ── NTRM1 Binary Media Stream (crypto_secretstream_xchacha20poly1305) ─────────


def encrypt_media_stream(
    k_media: bytes,
    media_id: str,
    key_epoch: int,
    plaintext: bytes,
    chunk_size: int = MEDIA_CHUNK_SIZE,
) -> bytes:
    if len(k_media) != 32:
        raise ValueError("k_media must be 32 bytes")
    if not isinstance(key_epoch, int) or key_epoch < 1:
        raise ValueError("Invalid key_epoch")
    if chunk_size < 1:
        raise ValueError("Invalid chunk_size")

    s = _get_sodium()
    state = ctypes.create_string_buffer(s.lib.crypto_secretstream_xchacha20poly1305_statebytes())
    hdr_len = s.lib.crypto_secretstream_xchacha20poly1305_headerbytes()
    abytes = s.lib.crypto_secretstream_xchacha20poly1305_abytes()
    stream_hdr = ctypes.create_string_buffer(hdr_len)

    rc = s.lib.crypto_secretstream_xchacha20poly1305_init_push(state, stream_hdr, _as_c_buf(k_media))
    if rc != 0:
        raise RuntimeError("secretstream init_push failed")

    file_header = struct.pack(
        ">4sB16sI24s",
        MEDIA_MAGIC,
        MEDIA_CRYPTO_VERSION,
        uuid_to_bytes(media_id),
        key_epoch,
        stream_hdr.raw,
    )
    assert len(file_header) == 49

    tag_msg = s.lib.crypto_secretstream_xchacha20poly1305_tag_message()
    tag_fin = s.lib.crypto_secretstream_xchacha20poly1305_tag_final()

    parts = [file_header]
    total = len(plaintext)
    offset = 0
    while True:
        end = min(offset + chunk_size, total)
        chunk = plaintext[offset:end]
        is_final = end >= total
        tag = tag_fin if is_final else tag_msg
        out_buf = ctypes.create_string_buffer(len(chunk) + abytes)
        out_len = ctypes.c_ulonglong(0)
        rc = s.lib.crypto_secretstream_xchacha20poly1305_push(
            state,
            out_buf,
            ctypes.byref(out_len),
            _as_c_buf(chunk),
            len(chunk),
            _as_c_buf(file_header),
            len(file_header),
            tag,
        )
        if rc != 0:
            raise RuntimeError("secretstream push failed")
        frame_bytes = out_buf.raw[: out_len.value]
        parts.append(struct.pack(">I", len(frame_bytes)) + frame_bytes)
        offset = end
        if offset >= total:
            break

    return b"".join(parts)


def decrypt_media_stream(
    k_media: bytes,
    expected_media_id: str,
    expected_key_epoch: int,
    encrypted_blob: bytes,
) -> bytes:
    if len(k_media) != 32:
        raise ValueError("k_media must be 32 bytes")
    if len(encrypted_blob) < 49:
        raise ValueError("Encrypted media blob too short for NTRM1 header")

    file_header = encrypted_blob[:49]
    magic, ver, media_bytes, hdr_epoch, stream_hdr = struct.unpack(">4sB16sI24s", file_header)
    if magic != MEDIA_MAGIC:
        raise ValueError("Invalid NTRM magic header")
    if ver != MEDIA_CRYPTO_VERSION:
        raise ValueError(f"Unsupported media_crypto_version: {ver}")
    hdr_media_id = bytes_to_uuid(media_bytes)
    if hdr_media_id != expected_media_id:
        raise ValueError(f"Media ID mismatch: expected {expected_media_id}, got {hdr_media_id}")
    if hdr_epoch != expected_key_epoch:
        raise ValueError(f"Media key_epoch mismatch: expected {expected_key_epoch}, got {hdr_epoch}")

    s = _get_sodium()
    state = ctypes.create_string_buffer(s.lib.crypto_secretstream_xchacha20poly1305_statebytes())
    rc = s.lib.crypto_secretstream_xchacha20poly1305_init_pull(
        state, _as_c_buf(stream_hdr), _as_c_buf(k_media)
    )
    if rc != 0:
        raise ValueError("secretstream init_pull failed")

    tag_fin = s.lib.crypto_secretstream_xchacha20poly1305_tag_final()
    abytes = s.lib.crypto_secretstream_xchacha20poly1305_abytes()
    pos = 49
    seen_final = False
    out_chunks: list[bytes] = []

    while pos < len(encrypted_blob):
        if seen_final:
            raise ValueError("Trailing data detected after secretstream TAG_FINAL")
        if pos + 4 > len(encrypted_blob):
            raise ValueError("Truncated media frame length")
        (frame_len,) = struct.unpack(">I", encrypted_blob[pos : pos + 4])
        pos += 4
        if frame_len < abytes or pos + frame_len > len(encrypted_blob):
            raise ValueError("Truncated or invalid encrypted media frame")
        frame_bytes = encrypted_blob[pos : pos + frame_len]
        pos += frame_len

        pt_buf = ctypes.create_string_buffer(max(1, frame_len - abytes))
        pt_len = ctypes.c_ulonglong(0)
        tag = ctypes.c_ubyte(0)
        rc = s.lib.crypto_secretstream_xchacha20poly1305_pull(
            state,
            pt_buf,
            ctypes.byref(pt_len),
            ctypes.byref(tag),
            _as_c_buf(frame_bytes),
            frame_len,
            _as_c_buf(file_header),
            len(file_header),
        )
        if rc != 0:
            raise ValueError("Media stream authentication failed")
        out_chunks.append(pt_buf.raw[: pt_len.value])
        if tag.value == tag_fin:
            seen_final = True

    if not seen_final:
        raise ValueError("Incomplete media stream: missing TAG_FINAL")
    return b"".join(out_chunks)


# ── Device X25519 Keypair, Epoch Wrapping & Pairing ────────────────────────────


def generate_device_keypair() -> tuple[bytes, bytes]:
    s = _get_sodium()
    pk = ctypes.create_string_buffer(s.lib.crypto_box_publickeybytes())
    sk = ctypes.create_string_buffer(s.lib.crypto_box_secretkeybytes())
    if s.lib.crypto_box_keypair(pk, sk) != 0:
        raise RuntimeError("crypto_box_keypair failed")
    return pk.raw, sk.raw


def seal_epoch_key_for_device(
    device_public_key: bytes,
    vault_id: str,
    key_epoch: int,
    target_device_id: str,
    epoch_root_key: bytes,
) -> str:
    assert_uuid(vault_id)
    assert_uuid(target_device_id)
    if len(epoch_root_key) != 32:
        raise ValueError("EpochRootKey must be 32 bytes")
    payload = canonical_json_bytes(
        {
            "domain": "NOTEOR/SYNC/V1/DEVICE-WRAP",
            "epoch_root_key_b64u": to_b64u(epoch_root_key),
            "key_epoch": key_epoch,
            "target_device_id": target_device_id,
            "vault_id": vault_id,
        }
    )
    s = _get_sodium()
    sealed = ctypes.create_string_buffer(len(payload) + s.lib.crypto_box_sealbytes())
    if s.lib.crypto_box_seal(sealed, _as_c_buf(payload), len(payload), _as_c_buf(device_public_key)) != 0:
        raise RuntimeError("crypto_box_seal failed")
    return to_b64u(sealed.raw)


def unseal_epoch_key_for_device(
    device_public_key: bytes,
    device_private_key: bytes,
    sealed_b64u: str,
    expected_vault_id: str,
    expected_key_epoch: int,
    expected_target_device_id: str,
) -> bytes:
    sealed = from_b64u(sealed_b64u)
    s = _get_sodium()
    sealbytes = s.lib.crypto_box_sealbytes()
    if len(sealed) < sealbytes:
        raise ValueError("Sealed payload too short")
    plain = ctypes.create_string_buffer(max(1, len(sealed) - sealbytes))
    rc = s.lib.crypto_box_seal_open(
        plain,
        _as_c_buf(sealed),
        len(sealed),
        _as_c_buf(device_public_key),
        _as_c_buf(device_private_key),
    )
    if rc != 0:
        raise ValueError("Failed to unseal epoch key")
    obj = json.loads(plain.raw[: len(sealed) - sealbytes].decode("utf-8"))
    if (
        obj.get("domain") != "NOTEOR/SYNC/V1/DEVICE-WRAP"
        or obj.get("vault_id") != expected_vault_id
        or obj.get("key_epoch") != expected_key_epoch
        or obj.get("target_device_id") != expected_target_device_id
    ):
        raise ValueError("Sealed epoch key domain/context mismatch")
    key = from_b64u(obj["epoch_root_key_b64u"])
    if len(key) != 32:
        raise ValueError("Invalid unsealed epoch root key length")
    return key


def compute_secret_proof_hmac(
    pairing_secret: bytes,
    vault_id: str,
    pairing_id: str,
    device_b_id: str,
    session_nonce_b_b64u: str,
) -> str:
    msg = f"{vault_id}:{pairing_id}:{device_b_id}:{session_nonce_b_b64u}".encode("utf-8")
    return to_b64u(hmac_sha256(pairing_secret, msg))


def derive_pairing_key(
    pairing_secret: bytes,
    pairing_nonce: bytes,
    vault_id: str,
    pairing_id: str,
    device_a_id: str,
    device_b_id: str,
    session_nonce_b: str,
) -> bytes:
    info = f"NOTEOR/SYNC/V1/PAIRING/{vault_id}/{pairing_id}/{device_a_id}/{device_b_id}/{session_nonce_b}".encode(
        "utf-8"
    )
    return hkdf_sha256(pairing_secret, pairing_nonce, info, 32)


def encrypt_pairing_bundle(
    k_pair: bytes,
    context: dict[str, str],
    bundle: dict[str, Any],
    nonce_override: bytes | None = None,
) -> dict[str, str]:
    aad = canonical_json_bytes(
        {
            "device_a_id": context["device_a_id"],
            "device_b_id": context["device_b_id"],
            "pairing_id": context["pairing_id"],
            "pairing_version": 1,
            "session_nonce_b": context["session_nonce_b"],
            "vault_id": context["vault_id"],
        }
    )
    plain = json.dumps(bundle, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    nonce, ciphertext = aead_encrypt(k_pair, plain, aad, nonce_override=nonce_override)
    return {"nonce": to_b64u(nonce), "ciphertext": to_b64u(ciphertext)}


def decrypt_pairing_bundle(
    k_pair: bytes,
    context: dict[str, str],
    nonce_b64u: str,
    ciphertext_b64u: str,
) -> dict[str, Any]:
    aad = canonical_json_bytes(
        {
            "device_a_id": context["device_a_id"],
            "device_b_id": context["device_b_id"],
            "pairing_id": context["pairing_id"],
            "pairing_version": 1,
            "session_nonce_b": context["session_nonce_b"],
            "vault_id": context["vault_id"],
        }
    )
    plain = aead_decrypt(k_pair, from_b64u(nonce_b64u), from_b64u(ciphertext_b64u), aad)
    parsed = json.loads(plain.decode("utf-8"))
    if parsed.get("vault_id") != context["vault_id"]:
        raise ValueError("Pairing bundle vault_id mismatch")
    return parsed


def compute_pairing_confirm_hmac(k_pair: bytes, pairing_id: str, device_b_id: str, vault_id: str) -> str:
    msg = f"CONFIRM:{pairing_id}:{device_b_id}:{vault_id}".encode("utf-8")
    return to_b64u(hmac_sha256(k_pair, msg))
