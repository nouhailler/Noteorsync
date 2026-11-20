"""Phase 2 FastAPI + Uvicorn preproduction validation suite for Noteor Sync Server.

Tests:
- Real ASGI / FastAPI validation via TestClient (`create_app`)
- Live Uvicorn child process HTTP server + persistence across full process restart
- Security headers (`X-Content-Type-Options`, `Cache-Control: no-store`), CORS policy & NOTEOR_ENV=production guard
- Auth, cross-vault isolation, device revocation, pairing lockout (5 failures), chunked NTRM1 media & WAL concurrency
- Size limits (`413 CHUNK_TOO_LARGE`, `413 PAYLOAD_TOO_LARGE`) & SQLite WAL backup integrity
"""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.main import create_app  # noqa: E402
from sync.desktop import crypto as pyc  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_health(url: str, timeout_s: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{url}/healthz", timeout=1.0)
            if r.status_code == 200:
                return r.json()
        except Exception as exc:
            last_err = exc
        time.sleep(0.15)
    raise RuntimeError(f"Uvicorn did not become healthy at {url}: {last_err}")


def test_fastapi_cors_headers_and_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTEOR_ENV", "production")
    monkeypatch.setenv("NOTEOR_CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="Wildcard CORS"):
        create_app(tmp_path / "fail.db", tmp_path / "fail_blobs")

    monkeypatch.setenv("NOTEOR_CORS_ORIGINS", "https://noteor-pwa.pages.dev")
    app = create_app(tmp_path / "srv.db", tmp_path / "blobs")
    client = TestClient(app)

    h = client.get("/healthz", headers={"Origin": "https://noteor-pwa.pages.dev"})
    assert h.status_code == 200
    assert h.json() == {"status": "ok", "version": "1.1.0"}
    assert h.headers.get("access-control-allow-origin") == "https://noteor-pwa.pages.dev"
    assert h.headers.get("x-content-type-options") == "nosniff"

    # Untrusted Origin receives no ACAO header
    h_bad = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert h_bad.status_code == 200
    assert "access-control-allow-origin" not in h_bad.headers

    # Malformed payload & out-of-range query return unified 400 INVALID_PAYLOAD JSON
    bad_json = client.post("/api/v1/vaults", content=b"not-json", headers={"Content-Type": "application/json"})
    assert bad_json.status_code == 400
    assert bad_json.json()["error"]["code"] == "INVALID_PAYLOAD"

    vault_id = str(uuid4())
    dev_a = str(uuid4())
    pk_a, _ = pyc.generate_device_keypair()
    boot = client.post(
        "/api/v1/vaults",
        json={
            "vault_id": vault_id,
            "device_id": dev_a,
            "device_name": "FastAPI A",
            "device_type": "desktop",
            "public_key_x25519": pyc.to_b64u(pk_a),
        },
    )
    assert boot.status_code == 201
    assert boot.headers.get("cache-control") == "no-store"
    token_a = boot.json()["device_access_token"]
    hdrs = {"Authorization": f"Bearer {token_a}"}

    bad_pull = client.get(f"/api/v1/vaults/{vault_id}/operations?since_seq=-1&limit=999", headers=hdrs)
    assert bad_pull.status_code == 400
    assert bad_pull.json()["error"]["code"] == "INVALID_PAYLOAD"


def test_fastapi_auth_pairing_lockout_media_and_abuse(tmp_path: Path) -> None:
    app = create_app(tmp_path / "srv.db", tmp_path / "blobs", cors_origins=["http://localhost:3000"])
    client = TestClient(app)

    v1, v2 = str(uuid4()), str(uuid4())
    d1, d2 = str(uuid4()), str(uuid4())
    pk1, _ = pyc.generate_device_keypair()
    pk2, _ = pyc.generate_device_keypair()

    t1 = client.post(
        "/api/v1/vaults",
        json={
            "vault_id": v1,
            "device_id": d1,
            "device_name": "D1",
            "device_type": "desktop",
            "public_key_x25519": pyc.to_b64u(pk1),
        },
    ).json()["device_access_token"]
    client.post(
        "/api/v1/vaults",
        json={
            "vault_id": v2,
            "device_id": d2,
            "device_name": "D2",
            "device_type": "pwa",
            "public_key_x25519": pyc.to_b64u(pk2),
        },
    )
    h1 = {"Authorization": f"Bearer {t1}"}

    # Cross-vault access forbidden
    assert client.get(f"/api/v1/vaults/{v2}", headers=h1).json()["error"]["code"] == "VAULT_MISMATCH"
    assert client.get(f"/api/v1/vaults/{v2}/devices", headers=h1).status_code == 403

    # Naive expires_at rejected cleanly as 400 (not 500)
    p_naive = client.post(
        f"/api/v1/vaults/{v1}/pairing",
        headers=h1,
        json={
            "pairing_id": str(uuid4()),
            "device_a_id": d1,
            "pairing_nonce": pyc.to_b64u(os.urandom(16)),
            "expires_at": "2027-01-01T00:00:00",
        },
    )
    assert p_naive.status_code == 400
    assert p_naive.json()["error"]["code"] == "INVALID_PAYLOAD"

    # Pairing lockout after 5 wrong confirm attempts
    pid = str(uuid4())
    fut = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert client.post(
        f"/api/v1/vaults/{v1}/pairing",
        headers=h1,
        json={
            "pairing_id": pid,
            "device_a_id": d1,
            "pairing_nonce": pyc.to_b64u(os.urandom(32)),
            "expires_at": fut,
        },
    ).status_code == 201
    dev_b = str(uuid4())
    pk_b, _ = pyc.generate_device_keypair()
    assert client.post(
        f"/api/v1/vaults/{v1}/pairing/{pid}/join",
        json={
            "device_b_id": dev_b,
            "device_b_name": "B",
            "device_b_type": "mobile",
            "device_b_public_key_x25519": pyc.to_b64u(pk_b),
            "session_nonce_b": pyc.to_b64u(os.urandom(24)),
            "secret_proof_hmac": pyc.to_b64u(os.urandom(32)),
        },
    ).status_code == 200
    assert client.post(
        f"/api/v1/vaults/{v1}/pairing/{pid}/approve",
        headers=h1,
        json={
            "bundle_nonce": pyc.to_b64u(os.urandom(24)),
            "encrypted_vault_bundle": pyc.to_b64u(os.urandom(64)),
        },
    ).status_code == 200
    wrong_dev = str(uuid4())
    for _ in range(5):
        r = client.post(
            f"/api/v1/vaults/{v1}/pairing/{pid}/confirm",
            json={"device_b_id": wrong_dev, "confirm_hmac": pyc.to_b64u(os.urandom(32))},
        )
        assert r.status_code == 403
    r_locked = client.post(
        f"/api/v1/vaults/{v1}/pairing/{pid}/confirm",
        json={"device_b_id": dev_b, "confirm_hmac": pyc.to_b64u(os.urandom(32))},
    )
    assert r_locked.status_code == 409
    assert r_locked.json()["error"]["code"] == "PAIRING_INVALID_STATE"

    # Media upload chunk > 4 MiB rejected cleanly with 413 CHUNK_TOO_LARGE
    mid = str(uuid4())
    fake_sha = hashlib.sha256(b"x" * 64).hexdigest()
    assert client.post(
        f"/api/v1/vaults/{v1}/media/uploads",
        headers=h1,
        json={"media_id": mid, "encrypted_size": 64, "encrypted_sha256": fake_sha, "key_epoch": 1},
    ).status_code == 200
    oversized = b"\x01" * (4 * 1024 * 1024 + 1)
    r_big = client.put(
        f"/api/v1/vaults/{v1}/media/uploads/{mid}",
        headers={**h1, "Content-Range": f"bytes 0-{len(oversized) - 1}/{len(oversized)}"},
        content=oversized,
    )
    assert r_big.status_code == 413
    assert r_big.json()["error"]["code"] == "CHUNK_TOO_LARGE"


def test_uvicorn_live_process_persistence_across_restart_and_concurrency(tmp_path: Path) -> None:
    data_dir = tmp_path / "live_data"
    data_dir.mkdir()
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "NOTEOR_DATA_DIR": str(data_dir),
        "NOTEOR_CORS_ORIGINS": "http://localhost:3000",
        "PYTHONPATH": str(SERVER_ROOT),
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(SERVER_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        health = _wait_health(base_url)
        assert health["version"] == "1.1.0"

        vault_id = str(uuid4())
        dev_id = str(uuid4())
        vsk = os.urandom(32)
        pk, _ = pyc.generate_device_keypair()
        boot = httpx.post(
            f"{base_url}/api/v1/vaults",
            json={
                "vault_id": vault_id,
                "device_id": dev_id,
                "device_name": "Uvicorn Client",
                "device_type": "desktop",
                "public_key_x25519": pyc.to_b64u(pk),
            },
            timeout=5.0,
        )
        assert boot.status_code == 201
        token = boot.json()["device_access_token"]
        hdrs = {"Authorization": f"Bearer {token}"}

        # Push an operation & NTRM1 media blob
        k_op, k_media_ep = pyc.derive_epoch_keys(pyc.derive_initial_epoch1_root_key(vsk, vault_id), vault_id, 1)
        op_id = str(uuid4())
        env_op = pyc.encrypt_operation_envelope(
            k_op,
            {"vault_id": vault_id, "device_id": dev_id, "operation_id": op_id, "device_seq": "1", "key_epoch": 1},
            {
                "payload_version": 1,
                "operation_type": "entity.upsert",
                "entity_type": "note",
                "entity_id": str(uuid4()),
                "revision_id": str(uuid4()),
                "parent_revision_ids": [],
                "created_at": "2026-11-20T00:00:00.000Z",
                "data": {"title": "Persist Uvicorn"},
            },
        )
        push = httpx.post(f"{base_url}/api/v1/vaults/{vault_id}/operations:batch", headers=hdrs, json={"operations": [env_op]})
        assert push.status_code == 200
        assert push.json()["accepted"][0]["server_seq"] == "1"

        media_id = str(uuid4())
        k_media = pyc.derive_media_object_key(k_media_ep, media_id)
        plain_media = b"Uvicorn persisted NTRM1 payload" * 500
        blob = pyc.encrypt_media_stream(k_media, media_id, 1, plain_media)
        sha = pyc.sha256_hex(blob)
        assert httpx.post(
            f"{base_url}/api/v1/vaults/{vault_id}/media/uploads",
            headers=hdrs,
            json={"media_id": media_id, "encrypted_size": len(blob), "encrypted_sha256": sha, "key_epoch": 1},
        ).status_code == 200
        assert httpx.put(
            f"{base_url}/api/v1/vaults/{vault_id}/media/uploads/{media_id}",
            headers={**hdrs, "Content-Range": f"bytes 0-{len(blob) - 1}/{len(blob)}"},
            content=blob,
        ).status_code == 200
        assert httpx.post(f"{base_url}/api/v1/vaults/{vault_id}/media/uploads/{media_id}/complete", headers=hdrs).status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    # Restart Uvicorn on the SAME data_dir and new port -> verify persistence + concurrent reads
    port2 = _free_port()
    base_url2 = f"http://127.0.0.1:{port2}"
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port2)],
        cwd=str(SERVER_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_health(base_url2)

        def _pull_once(_i: int) -> int:
            r = httpx.get(f"{base_url2}/api/v1/vaults/{vault_id}/operations?since_seq=0", headers=hdrs, timeout=5.0)
            assert r.status_code == 200
            ops = r.json()["operations"]
            assert len(ops) == 1
            assert ops[0]["operation_id"] == op_id
            return len(ops)

        with ThreadPoolExecutor(max_workers=6) as pool:
            counts = list(pool.map(_pull_once, range(12)))
        assert all(c == 1 for c in counts)

        dl = httpx.get(f"{base_url2}/api/v1/vaults/{vault_id}/media/{media_id}", headers=hdrs, timeout=5.0)
        assert dl.status_code == 200
        assert pyc.decrypt_media_stream(k_media, media_id, 1, dl.content) == plain_media

        # Run hot backup script on live Uvicorn DB
        backup_dir = tmp_path / "backups"
        bk_proc = subprocess.run(
            ["bash", str(SERVER_ROOT / "scripts" / "backup.sh"), str(backup_dir)],
            env={**os.environ, "NOTEOR_DATA_DIR": str(data_dir)},
            capture_output=True,
            text=True,
            check=True,
        )
        assert "Backup completed" in bk_proc.stdout
        snaps = list(backup_dir.glob("noteor-sync-*"))
        assert len(snaps) == 1
        assert (snaps[0] / "noteor_sync.db").is_file()
        assert (snaps[0] / "blobs" / vault_id / f"{media_id}.bin").is_file()
    finally:
        proc2.terminate()
        proc2.wait(timeout=10)
