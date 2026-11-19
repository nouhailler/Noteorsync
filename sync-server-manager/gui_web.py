"""Interface graphique locale Web (zéro dépendance) pour Noteor Sync Manager.

Utilisée lorsque `--web` est demandé ou en repli automatique si PyQt6 / serveur
d'affichage graphique n'est pas disponible sur la machine.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from manager_core import (
    ComposeConfig,
    build_cors_origins,
    check_healthz,
    detect_sync_server_dir,
    fetch_container_logs,
    fetch_container_status,
    is_valid_sync_server_dir,
    read_compose_config,
    restart_sync_container,
    run_dependency_check,
    save_settings,
    start_sync_container,
    stop_sync_container,
    update_compose_file,
)


def _find_icon_svg() -> str:
    for p in [
        Path(__file__).resolve().parent.parent / "web" / "public" / "icon.svg",
        Path("/usr/share/icons/hicolor/scalable/apps/noteor-sync-manager.svg"),
        Path("/usr/lib/noteor-sync-manager/icon.svg"),
    ]:
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="14" fill="#4F46E5"/>'
        '<text x="32" y="42" text-anchor="middle" fill="#fff" font-size="30" font-weight="bold">N</text>'
        "</svg>"
    )


HTML_PAGE = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Noteor Sync Manager — Option A Docker Compose</title>
  <link rel="icon" type="image/svg+xml" href="/icon.svg" />
  <style>
    :root {
      --bg: #F9FAFB;
      --card: #FFFFFF;
      --border: #E5E7EB;
      --text: #111827;
      --muted: #6B7280;
      --indigo: #4F46E5;
      --indigo-hover: #4338CA;
      --indigo-soft: #EEF2FF;
      --ok-bg: #ECFDF5; --ok-fg: #065F46; --ok-bd: #A7F3D0;
      --warn-bg: #FFFBEB; --warn-fg: #92400E; --warn-bd: #FDE68A;
      --err-bg: #FEF2F2; --err-fg: #991B1B; --err-bd: #FECACA;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; background: var(--bg); color: var(--text);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 14px;
    }
    header {
      background: var(--card); border-bottom: 1px solid var(--border);
      padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; gap: 16px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand img { width: 40px; height: 40px; }
    .brand h1 { margin: 0; font-size: 18px; font-weight: 800; }
    .brand p { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
    main { max-width: 980px; margin: 20px auto; padding: 0 18px 36px; display: grid; gap: 16px; }
    .card {
      background: var(--card); border: 1px solid var(--border);
      border-radius: 12px; padding: 16px 18px;
    }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .tabs { display: flex; gap: 8px; flex-wrap: wrap; }
    .tab-btn {
      background: var(--indigo-soft); color: var(--indigo-hover); border: 1px solid #E0E7FF;
      border-radius: 8px; padding: 8px 14px; font-weight: 600; cursor: pointer;
    }
    .tab-btn.active { background: var(--indigo); color: #fff; border-color: var(--indigo-hover); }
    .panel { display: none; gap: 14px; }
    .panel.active { display: grid; }
    label { font-weight: 600; color: #374151; font-size: 13px; display: block; margin-bottom: 4px; }
    input, select {
      width: 100%; padding: 8px 10px; border: 1px solid #D1D5DB; border-radius: 8px; font-size: 13px;
    }
    input:focus, select:focus { outline: none; border-color: var(--indigo); }
    button {
      border-radius: 8px; padding: 8px 14px; font-weight: 600; font-size: 13px;
      border: 1px solid #D1D5DB; background: #fff; color: #374151; cursor: pointer;
    }
    button:hover { background: #F3F4F6; }
    button.primary { background: var(--indigo); border-color: var(--indigo-hover); color: #fff; }
    button.primary:hover { background: var(--indigo-hover); }
    button.danger { background: var(--err-bg); border-color: var(--err-bd); color: var(--err-fg); }
    button.success { background: var(--ok-bg); border-color: var(--ok-bd); color: var(--ok-fg); }
    .badge {
      padding: 4px 10px; border-radius: 6px; font-weight: 700; font-size: 12px;
      border: 1px solid var(--border); background: #F3F4F6;
    }
    .badge.ok { background: var(--ok-bg); color: var(--ok-fg); border-color: var(--ok-bd); }
    .badge.warn { background: var(--warn-bg); color: var(--warn-fg); border-color: var(--warn-bd); }
    .badge.err { background: var(--err-bg); color: var(--err-fg); border-color: var(--err-bd); }
    .dep-item {
      display: flex; align-items: flex-start; gap: 12px; padding: 10px 12px;
      border: 1px solid var(--border); border-radius: 10px; background: #fff;
    }
    pre.terminal {
      margin: 0; background: #111827; color: #E5E7EB; border-radius: 10px; padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px; max-height: 360px; overflow: auto; white-space: pre-wrap;
    }
    .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
    .muted { color: var(--muted); font-size: 12px; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <img src="/icon.svg" alt="Logo Noteor" />
      <div>
        <h1>Noteor Sync Manager</h1>
        <p>Déploiement local du serveur relais E2EE — Option A (Docker Compose)</p>
      </div>
    </div>
    <span id="healthBadge" class="badge">Serveur : vérification…</span>
  </header>

  <main>
    <div class="card row">
      <div style="flex: 1; min-width: 240px;">
        <label for="syncDir">Dossier <code>sync-server/</code></label>
        <input id="syncDir" type="text" placeholder="/chemin/vers/Noteor/sync-server" />
      </div>
      <div style="align-self: flex-end;">
        <button onclick="saveSyncDir()">Charger le dossier</button>
      </div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" onclick="switchTab('deps', this)">1. Dépendances</button>
      <button class="tab-btn" onclick="switchTab('config', this)">2. Configuration compose.yaml</button>
      <button class="tab-btn" onclick="switchTab('deploy', this)">3. Conteneur & Santé /healthz</button>
      <button class="tab-btn" onclick="switchTab('logs', this)">4. Logs Docker</button>
    </div>

    <section id="tab-deps" class="panel active">
      <div class="card">
        <div class="row" style="justify-content: space-between; margin-bottom: 12px;">
          <div>
            <strong>Vérification des dépendances</strong>
            <div id="depsSummary" class="muted">En attente d'analyse…</div>
          </div>
          <button class="primary" onclick="runDeps()">Relancer la vérification</button>
        </div>
        <div id="depsList" style="display: grid; gap: 8px;"></div>
      </div>
    </section>

    <section id="tab-config" class="panel">
      <div class="card" style="display: grid; gap: 12px;">
        <strong>Paramétrage de <code>sync-server/compose.yaml</code></strong>
        <div>
          <label for="cfUrl">URL PWA Cloudflare Pages</label>
          <div class="row">
            <input id="cfUrl" type="text" style="flex:1" placeholder="https://noteor-pwa.pages.dev" />
            <button onclick="mergeCf()">Ajouter dans CORS</button>
          </div>
        </div>
        <div>
          <label for="corsInput">NOTEOR_CORS_ORIGINS</label>
          <input id="corsInput" type="text" placeholder="https://noteor-pwa.pages.dev,http://localhost:3000" />
        </div>
        <div class="grid-2">
          <div>
            <label for="dataDirInput">NOTEOR_DATA_DIR</label>
            <input id="dataDirInput" type="text" value="/data" />
          </div>
          <div>
            <label for="portInput">Port local (127.0.0.1)</label>
            <input id="portInput" type="number" min="1" max="65535" value="8787" />
          </div>
        </div>
        <div class="row" style="justify-content: space-between;">
          <button onclick="loadState()">Recharger</button>
          <button class="primary" onclick="saveCompose()">Enregistrer compose.yaml</button>
        </div>
        <div id="configMsg" class="muted"></div>
      </div>
    </section>

    <section id="tab-deploy" class="panel">
      <div class="card" style="display: grid; gap: 12px;">
        <div>
          <strong>Contrôle Docker Compose</strong>
          <div class="muted"><code>cd sync-server &amp;&amp; docker compose up -d --build noteor-sync</code></div>
        </div>
        <div class="row">
          <button class="primary" onclick="composeAction('start')">Démarrer / Construire (up -d --build)</button>
          <button onclick="composeAction('restart')">Redémarrer</button>
          <button class="danger" onclick="composeAction('stop')">Arrêter</button>
          <button onclick="composeAction('ps')">État (ps)</button>
          <button class="success" onclick="checkHealth()">Vérifier /healthz</button>
        </div>
        <div id="healthDetail" class="muted"></div>
        <pre id="deployOut" class="terminal">Prêt.</pre>
      </div>
    </section>

    <section id="tab-logs" class="panel">
      <div class="card" style="display: grid; gap: 12px;">
        <div class="row">
          <div style="min-width: 150px;">
            <label for="logService">Service</label>
            <select id="logService">
              <option value="noteor-sync">noteor-sync</option>
              <option value="caddy">caddy</option>
              <option value="all">all</option>
            </select>
          </div>
          <div style="min-width: 110px;">
            <label for="logTail">Lignes</label>
            <select id="logTail">
              <option value="100">100</option>
              <option value="200" selected>200</option>
              <option value="500">500</option>
            </select>
          </div>
          <div style="flex:1; min-width: 180px;">
            <label for="logFilter">Filtre texte</label>
            <input id="logFilter" type="text" placeholder="error, traceback…" oninput="renderLogs()" />
          </div>
          <div style="align-self: flex-end;">
            <button class="primary" onclick="fetchLogs()">Actualiser les logs</button>
          </div>
        </div>
        <pre id="logsOut" class="terminal">Cliquez sur « Actualiser les logs ».</pre>
      </div>
    </section>
  </main>

  <script>
    let rawLogs = "";
    function switchTab(id, btn) {
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById('tab-' + id).classList.add('active');
      btn.classList.add('active');
    }
    async function api(path, opts) {
      const res = await fetch(path, opts);
      return await res.json();
    }
    async function loadState() {
      const st = await api('/api/state');
      document.getElementById('syncDir').value = st.sync_dir || '';
      document.getElementById('cfUrl').value = st.cloudflare_url || '';
      document.getElementById('corsInput').value = st.noteor_cors_origins || '';
      document.getElementById('dataDirInput').value = st.noteor_data_dir || '/data';
      document.getElementById('portInput').value = st.host_port || 8787;
    }
    async function saveSyncDir() {
      const sync_dir = document.getElementById('syncDir').value;
      const res = await api('/api/sync-dir', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ sync_dir })
      });
      if (!res.ok) alert(res.error || 'Dossier invalide');
      await loadState();
      await runDeps();
    }
    async function runDeps() {
      const port = Number(document.getElementById('portInput').value || 8787);
      const sync_dir = document.getElementById('syncDir').value;
      const res = await api('/api/check-deps?port=' + encodeURIComponent(port) + '&sync_dir=' + encodeURIComponent(sync_dir));
      const box = document.getElementById('depsList');
      const sum = document.getElementById('depsSummary');
      if (res.error) { sum.textContent = res.error; box.innerHTML = ''; return; }
      sum.textContent = res.all_ready ? 'Tous les prérequis sont prêts.' : 'Certains prérequis manquent.';
      box.innerHTML = (res.checks || []).map(c => {
        const cls = !c.ok ? 'err' : (c.warning ? 'warn' : 'ok');
        const txt = !c.ok ? 'MANQUANT' : (c.warning ? 'OCCUPÉ' : 'PRÊT');
        const fix = (!c.ok && c.fix) ? '<div class="muted" style="color:#4338CA;font-family:monospace">Action : ' + c.fix + '</div>' : '';
        return '<div class="dep-item"><span class="badge ' + cls + '">' + txt + '</span><div><strong>' + c.label + '</strong><div class="muted">' + c.detail + '</div>' + fix + '</div></div>';
      }).join('');
    }
    function mergeCf() {
      const cf = document.getElementById('cfUrl').value.trim().replace(/\\/+$/, '');
      const existing = document.getElementById('corsInput').value.split(',').map(s => s.trim().replace(/\\/+$/, '')).filter(Boolean);
      const out = [];
      for (const item of [cf, ...existing]) { if (item && !out.includes(item)) out.push(item); }
      document.getElementById('corsInput').value = out.join(',');
    }
    async function saveCompose() {
      const payload = {
        sync_dir: document.getElementById('syncDir').value,
        noteor_data_dir: document.getElementById('dataDirInput').value,
        noteor_cors_origins: document.getElementById('corsInput').value,
        host_port: Number(document.getElementById('portInput').value || 8787),
      };
      const res = await api('/api/config', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      document.getElementById('configMsg').textContent = res.ok ? '✓ compose.yaml mis à jour.' : ('Erreur : ' + res.error);
    }
    async function composeAction(action) {
      const out = document.getElementById('deployOut');
      out.textContent = 'Exécution de ' + action + '…';
      const res = await api('/api/compose/' + action, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          sync_dir: document.getElementById('syncDir').value,
          noteor_data_dir: document.getElementById('dataDirInput').value,
          noteor_cors_origins: document.getElementById('corsInput').value,
          host_port: Number(document.getElementById('portInput').value || 8787),
        })
      });
      out.textContent = (res.output || res.error || '') + '\\n[code ' + (res.returncode ?? '?') + ']';
      await checkHealth();
    }
    async function checkHealth() {
      const port = Number(document.getElementById('portInput').value || 8787);
      const res = await api('/api/healthz?port=' + encodeURIComponent(port));
      const badge = document.getElementById('healthBadge');
      const detail = document.getElementById('healthDetail');
      if (res.ok) {
        badge.className = 'badge ok';
        badge.textContent = 'Serveur : en ligne (v' + ((res.data && res.data.version) || '?') + ')';
        detail.textContent = 'HTTP ' + res.status_code + ' → ' + res.raw;
      } else {
        badge.className = 'badge err';
        badge.textContent = 'Serveur : hors ligne';
        detail.textContent = res.url + ' → ' + (res.error || ('HTTP ' + res.status_code));
      }
    }
    async function fetchLogs() {
      const sync_dir = document.getElementById('syncDir').value;
      const service = document.getElementById('logService').value;
      const tail = document.getElementById('logTail').value;
      const res = await api('/api/logs?sync_dir=' + encodeURIComponent(sync_dir) + '&service=' + encodeURIComponent(service) + '&tail=' + encodeURIComponent(tail));
      rawLogs = res.output || res.error || '(Aucun log)';
      renderLogs();
    }
    function renderLogs() {
      const needle = document.getElementById('logFilter').value.trim().toLowerCase();
      const el = document.getElementById('logsOut');
      if (!needle) { el.textContent = rawLogs; return; }
      el.textContent = rawLogs.split('\\n').filter(l => l.toLowerCase().includes(needle)).join('\\n');
    }
    loadState().then(() => { runDeps(); checkHealth(); });
  </script>
</body>
</html>
"""


class ManagerState:
    def __init__(self, initial_sync_dir: str | None = None) -> None:
        self.sync_dir: Path | None = (
            Path(initial_sync_dir).resolve() if initial_sync_dir else detect_sync_server_dir()
        )
        self.icon_svg: str = _find_icon_svg()


def make_handler(state: ManagerState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)

            if parsed.path == "/":
                body = HTML_PAGE.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/icon.svg":
                body = state.icon_svg.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/api/state":
                cfg = (
                    read_compose_config(state.sync_dir / "compose.yaml")
                    if state.sync_dir and (state.sync_dir / "compose.yaml").is_file()
                    else ComposeConfig()
                )
                self._send_json(
                    {
                        "sync_dir": str(state.sync_dir) if state.sync_dir else "",
                        "cloudflare_url": cfg.cloudflare_url_guess,
                        "noteor_data_dir": cfg.noteor_data_dir,
                        "noteor_cors_origins": cfg.noteor_cors_origins,
                        "host_port": cfg.host_port,
                    }
                )
                return

            if parsed.path == "/api/check-deps":
                sync_dir = (qs.get("sync_dir") or [str(state.sync_dir or "./sync-server")])[0]
                port = int((qs.get("port") or ["8787"])[0])
                try:
                    self._send_json(run_dependency_check(sync_dir, port))
                except Exception as exc:
                    self._send_json({"all_ready": False, "error": str(exc), "checks": []}, status=500)
                return

            if parsed.path == "/api/healthz":
                port = int((qs.get("port") or ["8787"])[0])
                self._send_json(check_healthz(port=port))
                return

            if parsed.path == "/api/logs":
                sync_dir = Path((qs.get("sync_dir") or [str(state.sync_dir or "")])[0]).expanduser()
                if not is_valid_sync_server_dir(sync_dir):
                    self._send_json({"ok": False, "error": "Dossier sync-server/ invalide"}, status=400)
                    return
                service = (qs.get("service") or ["noteor-sync"])[0]
                tail = int((qs.get("tail") or ["200"])[0])
                try:
                    rc, out = fetch_container_logs(sync_dir, service=service, tail=tail)
                    self._send_json({"ok": rc == 0, "returncode": rc, "output": out})
                except Exception as exc:
                    self._send_json({"ok": False, "returncode": 1, "error": str(exc)}, status=500)
                return

            self._send_json({"error": "Not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            try:
                body = self._read_json_body()
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=400)
                return

            if parsed.path == "/api/sync-dir":
                p = Path(str(body.get("sync_dir", ""))).expanduser().resolve()
                if not is_valid_sync_server_dir(p):
                    self._send_json({"ok": False, "error": "Dossier sync-server/ invalide"}, status=400)
                    return
                state.sync_dir = p
                save_settings({"sync_server_dir": str(p)})
                self._send_json({"ok": True, "sync_dir": str(p)})
                return

            if parsed.path == "/api/config":
                p = Path(str(body.get("sync_dir") or state.sync_dir or "")).expanduser().resolve()
                if not is_valid_sync_server_dir(p):
                    self._send_json({"ok": False, "error": "Dossier sync-server/ invalide"}, status=400)
                    return
                cfg = ComposeConfig(
                    noteor_data_dir=str(body.get("noteor_data_dir", "/data")),
                    noteor_cors_origins=str(
                        body.get("noteor_cors_origins") or build_cors_origins("https://noteor-pwa.pages.dev")
                    ),
                    host_port=int(body.get("host_port", 8787)),
                )
                try:
                    update_compose_file(p / "compose.yaml", cfg)
                    state.sync_dir = p
                    save_settings({"sync_server_dir": str(p)})
                    self._send_json({"ok": True})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                return

            if parsed.path.startswith("/api/compose/"):
                action = parsed.path.rsplit("/", 1)[-1]
                p = Path(str(body.get("sync_dir") or state.sync_dir or "")).expanduser().resolve()
                if not is_valid_sync_server_dir(p):
                    self._send_json({"ok": False, "error": "Dossier sync-server/ invalide"}, status=400)
                    return
                try:
                    if action == "start":
                        if body.get("noteor_cors_origins"):
                            cfg = ComposeConfig(
                                noteor_data_dir=str(body.get("noteor_data_dir", "/data")),
                                noteor_cors_origins=str(body["noteor_cors_origins"]),
                                host_port=int(body.get("host_port", 8787)),
                            )
                            update_compose_file(p / "compose.yaml", cfg)
                        rc, out = start_sync_container(p)
                    elif action == "stop":
                        rc, out = stop_sync_container(p)
                    elif action == "restart":
                        rc, out = restart_sync_container(p)
                    elif action == "ps":
                        rc, out = fetch_container_status(p)
                    else:
                        self._send_json({"ok": False, "error": f"Action inconnue : {action}"}, status=404)
                        return
                    self._send_json({"ok": rc == 0, "returncode": rc, "output": out})
                except Exception as exc:
                    self._send_json({"ok": False, "returncode": 1, "error": str(exc)}, status=500)
                return

            self._send_json({"error": "Not found"}, status=404)

    return Handler


def create_web_server(
    sync_dir: str | None = None, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    state = ManagerState(initial_sync_dir=sync_dir)
    return ThreadingHTTPServer((host, port), make_handler(state))


def launch_web_gui(sync_dir: str | None = None, port: int = 0, open_browser: bool = True) -> int:
    server = create_web_server(sync_dir=sync_dir, port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Noteor Sync Manager (Web UI) disponible sur : {url}", flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
