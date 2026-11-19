#!/usr/bin/env python3
"""Point d'entrée de Noteor Sync Manager (GUI PyQt6 + repli Web UI / CLI)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Permet l'exécution depuis /usr/bin/noteor-sync-manager ou depuis le dépôt
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from manager_core import detect_sync_server_dir, run_dependency_check  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="noteor-sync-manager",
        description="Assistant graphique Noteor pour déployer sync-server/ via Docker Compose (Option A).",
    )
    parser.add_argument("--sync-dir", help="Chemin vers le dossier sync-server/ du dépôt Noteor")
    parser.add_argument("--web", action="store_true", help="Ouvre l'interface Web locale dans le navigateur")
    parser.add_argument("--port", type=int, default=0, help="Port HTTP local en mode --web (défaut : automatique)")
    parser.add_argument("--no-browser", action="store_true", help="Ne pas ouvrir automatiquement le navigateur en --web")
    parser.add_argument("--check-deps", action="store_true", help="Exécute uniquement le contrôle des dépendances en JSON")
    args = parser.parse_args(argv)

    if args.check_deps:
        sync_dir = Path(args.sync_dir).resolve() if args.sync_dir else (detect_sync_server_dir() or Path("./sync-server"))
        report = run_dependency_check(sync_dir)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("all_ready") else 1

    if args.web:
        from gui_web import launch_web_gui

        return launch_web_gui(sync_dir=args.sync_dir, port=args.port, open_browser=not args.no_browser)

    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if has_display:
        try:
            from gui_pyqt import launch_pyqt_gui

            return launch_pyqt_gui(sync_dir=args.sync_dir)
        except ImportError as exc:
            print(
                f"PyQt6 indisponible ({exc}) — bascule vers l'interface Web locale.",
                file=sys.stderr,
            )

    from gui_web import launch_web_gui

    return launch_web_gui(sync_dir=args.sync_dir, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
