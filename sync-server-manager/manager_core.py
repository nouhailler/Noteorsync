"""Core deployment logic for Noteor Sync Manager (Docker Compose Option A).

Zero third-party Python dependencies (pure stdlib) so it can be unit-tested
and embedded seamlessly in PyQt6 GUI, CLI, or fallback local Web UI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

CONFIG_PATH = Path.home() / ".config" / "noteor" / "sync-manager.json"
DEFAULT_CLOUDFLARE_URL = "https://noteor-pwa.pages.dev"
DEFAULT_LOCAL_ORIGIN = "http://localhost:3000"
DEFAULT_DATA_DIR = "/data"
DEFAULT_PORT = 8787


@dataclass
class ComposeConfig:
    noteor_data_dir: str = DEFAULT_DATA_DIR
    noteor_cors_origins: str = f"{DEFAULT_CLOUDFLARE_URL},{DEFAULT_LOCAL_ORIGIN}"
    host_port: int = DEFAULT_PORT

    @property
    def cloudflare_url_guess(self) -> str:
        for item in self.noteor_cors_origins.split(","):
            s = item.strip()
            if s.startswith("https://") and "localhost" not in s:
                return s
        return DEFAULT_CLOUDFLARE_URL


def detect_sync_server_dir(start_dir: str | Path | None = None) -> Path | None:
    """Locate `sync-server/` starting from saved config, CWD, parents, or script dir."""
    candidates: list[Path] = []
    saved = load_saved_settings()
    if saved.get("sync_server_dir"):
        candidates.append(Path(str(saved["sync_server_dir"])).expanduser())

    for base in [
        Path(start_dir) if start_dir else Path.cwd(),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent.parent,
    ]:
        for root in [base, *base.parents]:
            candidates.append(root / "sync-server")
            if root.name == "sync-server":
                candidates.append(root)

    seen: set[str] = set()
    for c in candidates:
        resolved = str(c.resolve()) if c.exists() else str(c)
        if resolved in seen:
            continue
        seen.add(resolved)
        if is_valid_sync_server_dir(c):
            return c.resolve()
    return None


def is_valid_sync_server_dir(path: str | Path) -> bool:
    p = Path(path)
    return (
        (p / "compose.yaml").is_file()
        and (p / "Dockerfile").is_file()
        and (p / "app" / "main.py").is_file()
    )


def load_saved_settings(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not config_path.is_file():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict[str, Any], config_path: Path = CONFIG_PATH) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    current = load_saved_settings(config_path)
    current.update(data)
    config_path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")


def _strip_quotes(val: str) -> str:
    s = val.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1]
    return s


def read_compose_config(compose_path: str | Path) -> ComposeConfig:
    """Parse NOTEOR_DATA_DIR, NOTEOR_CORS_ORIGINS and host port from compose.yaml."""
    p = Path(compose_path)
    if not p.is_file():
        return ComposeConfig()
    text = p.read_text(encoding="utf-8")
    cfg = ComposeConfig()

    m_data = re.search(r"^\s*NOTEOR_DATA_DIR:\s*(.+)$", text, flags=re.MULTILINE)
    if m_data:
        cfg.noteor_data_dir = _strip_quotes(m_data.group(1))

    m_cors = re.search(r"^\s*NOTEOR_CORS_ORIGINS:\s*(.+)$", text, flags=re.MULTILINE)
    if m_cors:
        cfg.noteor_cors_origins = _strip_quotes(m_cors.group(1))

    m_port = re.search(r'^\s*-\s*"?127\.0\.0\.1:(\d+):8787"?\s*$', text, flags=re.MULTILINE)
    if m_port:
        try:
            cfg.host_port = int(m_port.group(1))
        except ValueError:
            pass

    return cfg


def build_cors_origins(cloudflare_url: str, extra_origins: str = "") -> str:
    """Merge Cloudflare PWA URL and additional origins cleanly without duplicates."""
    items: list[str] = []
    for raw in [cloudflare_url, *extra_origins.split(",")]:
        cleaned = raw.strip().rstrip("/")
        if cleaned and cleaned not in items:
            items.append(cleaned)
    if not items:
        return f"{DEFAULT_CLOUDFLARE_URL},{DEFAULT_LOCAL_ORIGIN}"
    return ",".join(items)


def update_compose_file(compose_path: str | Path, cfg: ComposeConfig) -> Path:
    """Update NOTEOR_DATA_DIR, NOTEOR_CORS_ORIGINS, and host port in compose.yaml cleanly."""
    p = Path(compose_path)
    if not p.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {p}")
    if not cfg.noteor_data_dir.strip():
        raise ValueError("NOTEOR_DATA_DIR ne peut pas être vide")
    if not cfg.noteor_cors_origins.strip():
        raise ValueError("NOTEOR_CORS_ORIGINS ne peut pas être vide")
    if not (1 <= int(cfg.host_port) <= 65535):
        raise ValueError("Port HTTP invalide (1–65535)")

    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines: list[str] = []
    found_data = False
    found_cors = False
    found_port = False

    safe_data = cfg.noteor_data_dir.strip().replace('"', '\\"')
    safe_cors = cfg.noteor_cors_origins.strip().replace('"', '\\"')
    newline = "\n"

    for line in lines:
        nl = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        body = line[:-len(nl)] if nl else line

        m_data = re.match(r"^(\s*)NOTEOR_DATA_DIR\s*:.*$", body)
        if m_data:
            new_lines.append(f"{m_data.group(1)}NOTEOR_DATA_DIR: {safe_data}{nl or newline}")
            found_data = True
            continue

        m_cors = re.match(r"^(\s*)NOTEOR_CORS_ORIGINS\s*:.*$", body)
        if m_cors:
            new_lines.append(f'{m_cors.group(1)}NOTEOR_CORS_ORIGINS: "{safe_cors}"{nl or newline}')
            found_cors = True
            continue

        m_port = re.match(r'^(\s*-\s*)"?127\.0\.0\.1:\d+:8787"?\s*$', body)
        if m_port:
            new_lines.append(f'{m_port.group(1)}"127.0.0.1:{int(cfg.host_port)}:8787"{nl or newline}')
            found_port = True
            continue

        new_lines.append(line)

    if not (found_data and found_cors and found_port):
        raise ValueError(
            "Format inattendu de compose.yaml (champs NOTEOR_DATA_DIR / NOTEOR_CORS_ORIGINS / port non trouvés)"
        )

    p.write_text("".join(new_lines), encoding="utf-8")
    return p


def _find_check_deps_script() -> Path:
    for candidate in [
        Path(__file__).resolve().parent / "scripts" / "check_deps.sh",
        Path("/usr/lib/noteor-sync-manager/scripts/check_deps.sh"),
    ]:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Script check_deps.sh introuvable")


def run_dependency_check(sync_dir: str | Path, port: int = DEFAULT_PORT) -> dict[str, Any]:
    """Execute check_deps.sh --json and return parsed structured checks."""
    script = _find_check_deps_script()
    proc = subprocess.run(
        ["bash", str(script), str(sync_dir), str(port), "--json"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(proc.stderr.strip() or f"check_deps.sh exit {proc.returncode}")
    return json.loads(proc.stdout)


def resolve_compose_argv() -> list[str]:
    """Return ['docker', 'compose'] or ['docker-compose']."""
    if shutil.which("docker"):
        res = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        if res.returncode == 0:
            return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return ["docker", "compose"]


def run_compose_command(
    sync_dir: str | Path,
    args: list[str],
    on_output: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Run a docker compose command inside sync_dir, optionally streaming lines."""
    cmd = resolve_compose_argv() + args
    proc = subprocess.Popen(
        cmd,
        cwd=str(sync_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        chunks.append(line)
        if on_output:
            on_output(line.rstrip("\r\n"))
    rc = proc.wait()
    return rc, "".join(chunks)


def start_sync_container(
    sync_dir: str | Path,
    on_output: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Equivalent to: cd sync-server && docker compose up -d --build noteor-sync."""
    return run_compose_command(sync_dir, ["up", "-d", "--build", "noteor-sync"], on_output=on_output)


def stop_sync_container(
    sync_dir: str | Path,
    on_output: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    return run_compose_command(sync_dir, ["stop", "noteor-sync"], on_output=on_output)


def restart_sync_container(
    sync_dir: str | Path,
    on_output: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    return run_compose_command(sync_dir, ["restart", "noteor-sync"], on_output=on_output)


def fetch_container_status(sync_dir: str | Path) -> tuple[int, str]:
    return run_compose_command(sync_dir, ["ps"])


def fetch_container_logs(
    sync_dir: str | Path,
    service: str = "noteor-sync",
    tail: int = 200,
) -> tuple[int, str]:
    args = ["logs", "--no-color", f"--tail={int(tail)}"]
    if service and service != "all":
        args.append(service)
    return run_compose_command(sync_dir, args)


def check_healthz(port: int = DEFAULT_PORT, host: str = "127.0.0.1", timeout: float = 3.0) -> dict[str, Any]:
    """Verify server health cleanly via HTTP GET http://127.0.0.1:<port>/healthz (equivalent to curl -fsS)."""
    url = f"http://{host}:{int(port)}/healthz"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed: dict[str, Any] = {}
            try:
                parsed = json.loads(raw)
            except Exception:
                pass
            return {
                "ok": resp.status == 200 and parsed.get("status") == "ok",
                "status_code": resp.status,
                "url": url,
                "raw": raw,
                "data": parsed,
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status_code": exc.code,
            "url": url,
            "raw": "",
            "data": {},
            "error": f"HTTP {exc.code}: {exc.reason}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 0,
            "url": url,
            "raw": "",
            "data": {},
            "error": str(exc),
        }
