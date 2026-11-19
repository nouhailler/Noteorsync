"""Interface graphique PyQt6 de déploiement Noteor Sync (Option A : Docker Compose).

Reprend la charte visuelle de Noteor (Indigo #4F46E5 / #6366F1, fond ardoise clair
#F9FAFB, cartes blanches #FFFFFF, bordures #E5E7EB et icône carnet/crayon).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtSvgWidgets import QSvgWidget

    HAS_QTSVG = True
except ImportError:  # python3-pyqt6 sans python3-pyqt6.qtsvg
    QSvgWidget = None  # type: ignore[assignment]
    HAS_QTSVG = False

from manager_core import (
    ComposeConfig,
    build_cors_origins,
    check_healthz,
    detect_sync_server_dir,
    fetch_container_logs,
    fetch_container_status,
    is_valid_sync_server_dir,
    read_compose_config,
    resolve_compose_argv,
    restart_sync_container,
    run_dependency_check,
    save_settings,
    start_sync_container,
    stop_sync_container,
    update_compose_file,
)


NOTEOR_QSS = """
QMainWindow, QWidget#RootWidget {
    background-color: #F9FAFB;
    color: #111827;
    font-family: "Inter", "Segoe UI", "Ubuntu", "Cantarell", sans-serif;
    font-size: 13px;
}

QFrame#HeaderCard {
    background-color: #FFFFFF;
    border-bottom: 1px solid #E5E7EB;
}

QFrame.NoteorCard {
    background-color: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 12px;
}

QLabel.SectionTitle {
    font-size: 15px;
    font-weight: 700;
    color: #111827;
}

QLabel.MutedText {
    color: #6B7280;
    font-size: 12px;
}

QLabel.Badge {
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: 600;
    font-size: 12px;
}

QLineEdit, QSpinBox, QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #D1D5DB;
    border-radius: 8px;
    padding: 7px 10px;
    color: #111827;
    selection-background-color: #4F46E5;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1.5px solid #4F46E5;
}

QPushButton {
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 600;
    border: 1px solid #D1D5DB;
    background-color: #FFFFFF;
    color: #374151;
}

QPushButton:hover {
    background-color: #F3F4F6;
}

QPushButton:disabled {
    opacity: 0.55;
    color: #9CA3AF;
}

QPushButton#PrimaryBtn {
    background-color: #4F46E5;
    border: 1px solid #4338CA;
    color: #FFFFFF;
}

QPushButton#PrimaryBtn:hover {
    background-color: #4338CA;
}

QPushButton#DangerBtn {
    background-color: #FEF2F2;
    border: 1px solid #FECACA;
    color: #B91C1C;
}

QPushButton#DangerBtn:hover {
    background-color: #FEE2E2;
}

QPushButton#SuccessBtn {
    background-color: #ECFDF5;
    border: 1px solid #A7F3D0;
    color: #065F46;
}

QPushButton#SuccessBtn:hover {
    background-color: #D1FAE5;
}

QTabWidget::pane {
    border: none;
    background: transparent;
}

QTabBar::tab {
    background: #EEF2FF;
    color: #4338CA;
    border: 1px solid #E0E7FF;
    border-radius: 8px;
    padding: 8px 14px;
    margin-right: 6px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background: #4F46E5;
    color: #FFFFFF;
    border: 1px solid #4338CA;
}

QPlainTextEdit.Terminal {
    background-color: #111827;
    color: #E5E7EB;
    border: 1px solid #1F2937;
    border-radius: 10px;
    padding: 10px;
    font-family: "JetBrains Mono", "Fira Code", "Ubuntu Mono", monospace;
    font-size: 12px;
}

QPlainTextEdit.PreviewYaml {
    background-color: #F3F4F6;
    color: #1F2937;
    border: 1px solid #E5E7EB;
    border-radius: 8px;
    padding: 8px;
    font-family: "JetBrains Mono", "Fira Code", "Ubuntu Mono", monospace;
    font-size: 12px;
}
"""


def _find_icon_path() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent.parent / "web" / "public" / "icon.svg",
        Path("/usr/share/icons/hicolor/scalable/apps/noteor-sync-manager.svg"),
        Path("/usr/lib/noteor-sync-manager/icon.svg"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


class FunctionWorker(QThread):
    line_output = pyqtSignal(str)
    finished_result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, *args, stream_output: bool = False, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.stream_output = stream_output

    def run(self) -> None:
        try:
            if self.stream_output:
                self.kwargs["on_output"] = self.line_output.emit
            res = self.fn(*self.args, **self.kwargs)
            self.finished_result.emit(res)
        except Exception as exc:
            self.failed.emit(str(exc))


class LogStreamWorker(QThread):
    line_received = pyqtSignal(str)
    stream_ended = pyqtSignal(int, str)

    def __init__(self, sync_dir: Path, service: str, tail: int):
        super().__init__()
        self.sync_dir = sync_dir
        self.service = service
        self.tail = tail
        self._proc: subprocess.Popen[str] | None = None
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self) -> None:
        args = resolve_compose_argv() + ["logs", "-f", "--no-color", f"--tail={int(self.tail)}"]
        if self.service and self.service != "all":
            args.append(self.service)
        try:
            self._proc = subprocess.Popen(
                args,
                cwd=str(self.sync_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                if self._stop_requested:
                    break
                self.line_received.emit(line.rstrip("\r\n"))
            rc = self._proc.wait()
            self.stream_ended.emit(rc, "")
        except Exception as exc:
            self.stream_ended.emit(1, str(exc))


class NoteorSyncManagerWindow(QMainWindow):
    def __init__(self, initial_sync_dir: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Noteor Sync Manager — Déploiement Docker Compose")
        self.resize(980, 740)

        icon_path = _find_icon_path()
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))

        detected = Path(initial_sync_dir).resolve() if initial_sync_dir else detect_sync_server_dir()
        self.sync_dir: Path | None = detected
        self._workers: list[QThread] = []
        self._log_stream_worker: LogStreamWorker | None = None
        self._all_logs_buffer: str = ""

        root = QWidget()
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._build_header(icon_path))

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(14)

        content_layout.addWidget(self._build_path_bar())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_deps_tab(), "1. Dépendances")
        self.tabs.addTab(self._build_config_tab(), "2. Configuration compose.yaml")
        self.tabs.addTab(self._build_deploy_tab(), "3. Conteneur & Santé /healthz")
        self.tabs.addTab(self._build_logs_tab(), "4. Logs Docker")
        content_layout.addWidget(self.tabs, 1)

        main_layout.addWidget(content, 1)
        self.setStyleSheet(NOTEOR_QSS)

        self._load_compose_into_form()
        self._refresh_deps()
        self._refresh_healthz(silent=True)

    def _build_header(self, icon_path: Path | None) -> QFrame:
        header = QFrame()
        header.setObjectName("HeaderCard")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(14)

        if icon_path and HAS_QTSVG and QSvgWidget is not None:
            svg = QSvgWidget(str(icon_path))
            svg.setFixedSize(42, 42)
            layout.addWidget(svg)
        else:
            badge = QLabel("N")
            badge.setFixedSize(42, 42)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #6366F1, stop:1 #3730A3);"
                "color: #FFFFFF; font-size: 20px; font-weight: 800; border-radius: 10px;"
            )
            layout.addWidget(badge)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        title_lbl = QLabel("Noteor Sync Manager")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: 800; color: #111827;")
        subtitle_lbl = QLabel("Gestion locale du serveur relais E2EE (Option A — Docker Compose)")
        subtitle_lbl.setProperty("class", "MutedText")
        titles.addWidget(title_lbl)
        titles.addWidget(subtitle_lbl)
        layout.addLayout(titles, 1)

        self.header_health_badge = QLabel("Serveur : inconnu")
        self.header_health_badge.setProperty("class", "Badge")
        self._set_badge_style(self.header_health_badge, "neutral")
        layout.addWidget(self.header_health_badge)

        return header

    def _build_path_bar(self) -> QFrame:
        card = QFrame()
        card.setProperty("class", "NoteorCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        lbl = QLabel("Dossier sync-server/ :")
        lbl.setStyleSheet("font-weight: 600; color: #374151;")
        layout.addWidget(lbl)

        self.sync_dir_input = QLineEdit(str(self.sync_dir) if self.sync_dir else "")
        self.sync_dir_input.setPlaceholderText("/chemin/vers/Noteor/sync-server")
        layout.addWidget(self.sync_dir_input, 1)

        browse_btn = QPushButton("Parcourir…")
        browse_btn.clicked.connect(self._browse_sync_dir)
        layout.addWidget(browse_btn)

        apply_btn = QPushButton("Charger")
        apply_btn.clicked.connect(self._apply_sync_dir)
        layout.addWidget(apply_btn)

        return card

    def _build_deps_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        top_card = QFrame()
        top_card.setProperty("class", "NoteorCard")
        top_l = QHBoxLayout(top_card)
        top_l.setContentsMargins(16, 14, 16, 14)

        info_l = QVBoxLayout()
        t = QLabel("Vérification des dépendances système")
        t.setProperty("class", "SectionTitle")
        self.deps_summary_lbl = QLabel("Analyse des outils requis pour Option A (Docker Compose, curl, sync-server/)…")
        self.deps_summary_lbl.setProperty("class", "MutedText")
        info_l.addWidget(t)
        info_l.addWidget(self.deps_summary_lbl)
        top_l.addLayout(info_l, 1)

        self.deps_btn = QPushButton("Relancer la vérification")
        self.deps_btn.setObjectName("PrimaryBtn")
        self.deps_btn.clicked.connect(self._refresh_deps)
        top_l.addWidget(self.deps_btn)
        outer.addWidget(top_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.deps_list_container = QWidget()
        self.deps_list_layout = QVBoxLayout(self.deps_list_container)
        self.deps_list_layout.setContentsMargins(0, 0, 0, 0)
        self.deps_list_layout.setSpacing(8)
        self.deps_list_layout.addStretch(1)
        scroll.setWidget(self.deps_list_container)
        outer.addWidget(scroll, 1)

        return page

    def _build_config_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        card = QFrame()
        card.setProperty("class", "NoteorCard")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(18, 16, 18, 16)
        card_l.setSpacing(14)

        title = QLabel("Paramétrage de sync-server/compose.yaml")
        title.setProperty("class", "SectionTitle")
        desc = QLabel(
            "Renseignez l'URL Cloudflare Pages de votre PWA mobile ainsi que les variables d'environnement "
            "avant de générer ou redémarrer le conteneur."
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "MutedText")
        card_l.addWidget(title)
        card_l.addWidget(desc)

        form = QFormLayout()
        form.setSpacing(10)

        cf_row = QHBoxLayout()
        self.cf_url_input = QLineEdit()
        self.cf_url_input.setPlaceholderText("https://noteor-pwa.pages.dev")
        merge_btn = QPushButton("Ajouter dans CORS")
        merge_btn.clicked.connect(self._merge_cloudflare_into_cors)
        cf_row.addWidget(self.cf_url_input, 1)
        cf_row.addWidget(merge_btn)
        form.addRow("URL PWA Cloudflare Pages :", cf_row)

        self.cors_input = QLineEdit()
        self.cors_input.setPlaceholderText("https://noteor-pwa.pages.dev,http://localhost:3000")
        self.cors_input.textChanged.connect(self._update_yaml_preview)
        form.addRow("NOTEOR_CORS_ORIGINS :", self.cors_input)

        self.data_dir_input = QLineEdit("/data")
        self.data_dir_input.textChanged.connect(self._update_yaml_preview)
        form.addRow("NOTEOR_DATA_DIR :", self.data_dir_input)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(8787)
        self.port_spin.valueChanged.connect(self._update_yaml_preview)
        form.addRow("Port local (127.0.0.1) :", self.port_spin)

        card_l.addLayout(form)

        preview_lbl = QLabel("Aperçu injecté dans sync-server/compose.yaml :")
        preview_lbl.setStyleSheet("font-weight: 600; color: #374151;")
        card_l.addWidget(preview_lbl)

        self.yaml_preview = QPlainTextEdit()
        self.yaml_preview.setReadOnly(True)
        self.yaml_preview.setProperty("class", "PreviewYaml")
        self.yaml_preview.setMaximumHeight(115)
        card_l.addWidget(self.yaml_preview)

        actions = QHBoxLayout()
        reload_btn = QPushButton("Recharger depuis compose.yaml")
        reload_btn.clicked.connect(self._load_compose_into_form)
        save_btn = QPushButton("Enregistrer compose.yaml")
        save_btn.setObjectName("PrimaryBtn")
        save_btn.clicked.connect(self._save_compose_config)
        actions.addWidget(reload_btn)
        actions.addStretch(1)
        actions.addWidget(save_btn)
        card_l.addLayout(actions)

        outer.addWidget(card)
        outer.addStretch(1)
        return page

    def _build_deploy_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        ctrl_card = QFrame()
        ctrl_card.setProperty("class", "NoteorCard")
        ctrl_l = QVBoxLayout(ctrl_card)
        ctrl_l.setContentsMargins(16, 14, 16, 14)
        ctrl_l.setSpacing(10)

        t = QLabel("Contrôle du conteneur Docker noteor-sync")
        t.setProperty("class", "SectionTitle")
        ctrl_l.addWidget(t)

        cmd_hint = QLabel("Commande exécutée : cd sync-server && docker compose up -d --build noteor-sync")
        cmd_hint.setProperty("class", "MutedText")
        ctrl_l.addWidget(cmd_hint)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Démarrer / Construire (up -d --build)")
        self.start_btn.setObjectName("PrimaryBtn")
        self.start_btn.clicked.connect(self._start_container)

        self.restart_btn = QPushButton("Redémarrer")
        self.restart_btn.clicked.connect(self._restart_container)

        self.stop_btn = QPushButton("Arrêter")
        self.stop_btn.setObjectName("DangerBtn")
        self.stop_btn.clicked.connect(self._stop_container)

        self.ps_btn = QPushButton("État (ps)")
        self.ps_btn.clicked.connect(self._check_ps)

        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.restart_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.ps_btn)
        ctrl_l.addLayout(btn_row)
        outer.addWidget(ctrl_card)

        health_card = QFrame()
        health_card.setProperty("class", "NoteorCard")
        health_l = QHBoxLayout(health_card)
        health_l.setContentsMargins(16, 12, 16, 12)
        health_l.setSpacing(12)

        h_info = QVBoxLayout()
        h_title = QLabel("Vérification HTTP /healthz")
        h_title.setProperty("class", "SectionTitle")
        self.health_detail_lbl = QLabel("Cliquez sur « Vérifier /healthz » pour interroger http://127.0.0.1:8787/healthz")
        self.health_detail_lbl.setProperty("class", "MutedText")
        self.health_detail_lbl.setWordWrap(True)
        h_info.addWidget(h_title)
        h_info.addWidget(self.health_detail_lbl)
        health_l.addLayout(h_info, 1)

        self.health_btn = QPushButton("Vérifier /healthz")
        self.health_btn.setObjectName("SuccessBtn")
        self.health_btn.clicked.connect(lambda: self._refresh_healthz(silent=False))
        health_l.addWidget(self.health_btn)
        outer.addWidget(health_card)

        self.deploy_console = QPlainTextEdit()
        self.deploy_console.setReadOnly(True)
        self.deploy_console.setProperty("class", "Terminal")
        self.deploy_console.setPlaceholderText("Sortie des commandes Docker Compose…")
        outer.addWidget(self.deploy_console, 1)

        return page

    def _build_logs_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(12)

        bar = QFrame()
        bar.setProperty("class", "NoteorCard")
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(14, 10, 14, 10)
        bar_l.setSpacing(8)

        bar_l.addWidget(QLabel("Service :"))
        self.log_service_combo = QComboBox()
        self.log_service_combo.addItems(["noteor-sync", "caddy", "all"])
        bar_l.addWidget(self.log_service_combo)

        bar_l.addWidget(QLabel("Lignes :"))
        self.log_tail_combo = QComboBox()
        self.log_tail_combo.addItems(["100", "200", "500", "1000"])
        self.log_tail_combo.setCurrentText("200")
        bar_l.addWidget(self.log_tail_combo)

        self.log_filter_input = QLineEdit()
        self.log_filter_input.setPlaceholderText("Filtrer (erreur, traceback, 500…)")
        self.log_filter_input.textChanged.connect(self._apply_log_filter)
        bar_l.addWidget(self.log_filter_input, 1)

        self.fetch_logs_btn = QPushButton("Actualiser")
        self.fetch_logs_btn.setObjectName("PrimaryBtn")
        self.fetch_logs_btn.clicked.connect(self._fetch_logs)
        bar_l.addWidget(self.fetch_logs_btn)

        self.follow_logs_btn = QPushButton("Suivi temps réel")
        self.follow_logs_btn.clicked.connect(self._toggle_log_follow)
        bar_l.addWidget(self.follow_logs_btn)

        clear_btn = QPushButton("Effacer")
        clear_btn.clicked.connect(self._clear_logs)
        bar_l.addWidget(clear_btn)

        outer.addWidget(bar)

        self.logs_console = QPlainTextEdit()
        self.logs_console.setReadOnly(True)
        self.logs_console.setProperty("class", "Terminal")
        self.logs_console.setPlaceholderText("Les logs du serveur Docker apparaîtront ici…")
        outer.addWidget(self.logs_console, 1)

        return page

    @staticmethod
    def _set_badge_style(label: QLabel, state: str) -> None:
        palettes = {
            "ok": ("#ECFDF5", "#065F46", "#A7F3D0"),
            "warn": ("#FFFBEB", "#92400E", "#FDE68A"),
            "error": ("#FEF2F2", "#991B1B", "#FECACA"),
            "neutral": ("#F3F4F6", "#374151", "#E5E7EB"),
        }
        bg, fg, border = palettes.get(state, palettes["neutral"])
        label.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: 1px solid {border};"
            "border-radius: 6px; padding: 4px 10px; font-weight: 600; font-size: 12px;"
        )

    def _ensure_sync_dir(self) -> Path | None:
        raw = self.sync_dir_input.text().strip()
        if raw:
            candidate = Path(raw).expanduser().resolve()
            if is_valid_sync_server_dir(candidate):
                self.sync_dir = candidate
                save_settings({"sync_server_dir": str(candidate)})
                return candidate
        QMessageBox.warning(
            self,
            "Dossier sync-server/ invalide",
            "Veuillez sélectionner un dossier sync-server/ valide contenant compose.yaml, Dockerfile et app/main.py.",
        )
        return None

    def _browse_sync_dir(self) -> None:
        start = str(self.sync_dir) if self.sync_dir else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Sélectionner le dossier sync-server/", start)
        if chosen:
            self.sync_dir_input.setText(chosen)
            self._apply_sync_dir()

    def _apply_sync_dir(self) -> None:
        if self._ensure_sync_dir():
            self._load_compose_into_form()
            self._refresh_deps()

    def _load_compose_into_form(self) -> None:
        if not self.sync_dir or not (self.sync_dir / "compose.yaml").is_file():
            self._update_yaml_preview()
            return
        cfg = read_compose_config(self.sync_dir / "compose.yaml")
        self.cf_url_input.setText(cfg.cloudflare_url_guess)
        self.cors_input.setText(cfg.noteor_cors_origins)
        self.data_dir_input.setText(cfg.noteor_data_dir)
        self.port_spin.setValue(cfg.host_port)
        self._update_yaml_preview()

    def _merge_cloudflare_into_cors(self) -> None:
        merged = build_cors_origins(self.cf_url_input.text(), self.cors_input.text())
        self.cors_input.setText(merged)

    def _current_form_config(self) -> ComposeConfig:
        return ComposeConfig(
            noteor_data_dir=self.data_dir_input.text().strip() or "/data",
            noteor_cors_origins=self.cors_input.text().strip(),
            host_port=int(self.port_spin.value()),
        )

    def _update_yaml_preview(self) -> None:
        cfg = self._current_form_config()
        snippet = (
            "services:\n"
            "  noteor-sync:\n"
            "    environment:\n"
            f"      NOTEOR_DATA_DIR: {cfg.noteor_data_dir}\n"
            f'      NOTEOR_CORS_ORIGINS: "{cfg.noteor_cors_origins}"\n'
            "    ports:\n"
            f'      - "127.0.0.1:{cfg.host_port}:8787"'
        )
        self.yaml_preview.setPlainText(snippet)

    def _save_compose_config(self) -> bool:
        sync_dir = self._ensure_sync_dir()
        if not sync_dir:
            return False
        cfg = self._current_form_config()
        if not cfg.noteor_cors_origins:
            cfg.noteor_cors_origins = build_cors_origins(self.cf_url_input.text(), "http://localhost:3000")
            self.cors_input.setText(cfg.noteor_cors_origins)
        try:
            updated = update_compose_file(sync_dir / "compose.yaml", cfg)
            self._append_console(f"✓ Fichier mis à jour : {updated}")
            QMessageBox.information(
                self,
                "Configuration enregistrée",
                f"compose.yaml a été mis à jour avec succès :\n{updated}",
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Erreur d'enregistrement", str(exc))
            return False

    def _run_worker(self, fn, *args, stream_output: bool = False, on_done=None, **kwargs) -> None:
        worker = FunctionWorker(fn, *args, stream_output=stream_output, **kwargs)
        self._workers.append(worker)
        if stream_output:
            worker.line_output.connect(self._append_console)
        if on_done:
            worker.finished_result.connect(on_done)
        worker.failed.connect(lambda err: self._append_console(f"[ERREUR] {err}"))
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        worker.start()

    def _refresh_deps(self) -> None:
        sync_dir = self.sync_dir or Path(self.sync_dir_input.text().strip() or "./sync-server")
        port = int(self.port_spin.value())
        self.deps_btn.setEnabled(False)
        self.deps_summary_lbl.setText("Vérification en cours…")

        def done(res: dict[str, Any]) -> None:
            self.deps_btn.setEnabled(True)
            self._render_deps(res)

        def fail(err: str) -> None:
            self.deps_btn.setEnabled(True)
            self.deps_summary_lbl.setText(f"Impossible d'exécuter check_deps.sh : {err}")

        worker = FunctionWorker(run_dependency_check, sync_dir, port)
        self._workers.append(worker)
        worker.finished_result.connect(done)
        worker.failed.connect(fail)
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        worker.start()

    def _render_deps(self, payload: dict[str, Any]) -> None:
        while self.deps_list_layout.count():
            item = self.deps_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_ready = bool(payload.get("all_ready"))
        if all_ready:
            self.deps_summary_lbl.setText("Tous les prérequis sont prêts pour démarrer Docker Compose.")
        else:
            self.deps_summary_lbl.setText(
                "Certains prérequis manquent ou nécessitent des droits Docker avant le déploiement."
            )

        for chk in payload.get("checks", []):
            row_card = QFrame()
            row_card.setProperty("class", "NoteorCard")
            row_l = QHBoxLayout(row_card)
            row_l.setContentsMargins(14, 10, 14, 10)
            row_l.setSpacing(12)

            badge = QLabel()
            if not chk.get("ok"):
                badge.setText("MANQUANT")
                self._set_badge_style(badge, "error")
            elif chk.get("warning"):
                badge.setText("OCCUPÉ")
                self._set_badge_style(badge, "warn")
            else:
                badge.setText("PRÊT")
                self._set_badge_style(badge, "ok")
            badge.setFixedWidth(96)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_l.addWidget(badge)

            text_l = QVBoxLayout()
            text_l.setSpacing(2)
            lbl = QLabel(str(chk.get("label", "")))
            lbl.setStyleSheet("font-weight: 700; color: #111827;")
            detail = QLabel(str(chk.get("detail", "")))
            detail.setProperty("class", "MutedText")
            detail.setWordWrap(True)
            text_l.addWidget(lbl)
            text_l.addWidget(detail)

            fix = str(chk.get("fix", "")).strip()
            if (not chk.get("ok")) and fix:
                fix_lbl = QLabel(f"Action suggérée : {fix}")
                fix_lbl.setStyleSheet("color: #4338CA; font-size: 12px; font-family: monospace;")
                fix_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                fix_lbl.setWordWrap(True)
                text_l.addWidget(fix_lbl)

            row_l.addLayout(text_l, 1)
            self.deps_list_layout.addWidget(row_card)

        self.deps_list_layout.addStretch(1)

    def _append_console(self, text: str) -> None:
        self.deploy_console.appendPlainText(text)

    def _set_deploy_buttons_enabled(self, enabled: bool) -> None:
        for b in (self.start_btn, self.restart_btn, self.stop_btn, self.ps_btn):
            b.setEnabled(enabled)

    def _start_container(self) -> None:
        sync_dir = self._ensure_sync_dir()
        if not sync_dir:
            return
        # Applique les paramètres saisis avant le démarrage
        try:
            update_compose_file(sync_dir / "compose.yaml", self._current_form_config())
        except Exception as exc:
            QMessageBox.warning(self, "Configuration compose.yaml", str(exc))
            return

        self._set_deploy_buttons_enabled(False)
        self._append_console(f"$ cd {sync_dir} && docker compose up -d --build noteor-sync")

        def done(res: tuple[int, str]) -> None:
            rc, _ = res
            self._set_deploy_buttons_enabled(True)
            self._append_console(f"[Fin commande : code {rc}]")
            if rc == 0:
                self._refresh_healthz(silent=True)

        self._run_worker(start_sync_container, sync_dir, stream_output=True, on_done=done)

    def _restart_container(self) -> None:
        sync_dir = self._ensure_sync_dir()
        if not sync_dir:
            return
        self._set_deploy_buttons_enabled(False)
        self._append_console("$ docker compose restart noteor-sync")

        def done(res: tuple[int, str]) -> None:
            rc, _ = res
            self._set_deploy_buttons_enabled(True)
            self._append_console(f"[Redémarrage terminé : code {rc}]")
            if rc == 0:
                self._refresh_healthz(silent=True)

        self._run_worker(restart_sync_container, sync_dir, stream_output=True, on_done=done)

    def _stop_container(self) -> None:
        sync_dir = self._ensure_sync_dir()
        if not sync_dir:
            return
        self._set_deploy_buttons_enabled(False)
        self._append_console("$ docker compose stop noteor-sync")

        def done(res: tuple[int, str]) -> None:
            rc, _ = res
            self._set_deploy_buttons_enabled(True)
            self._append_console(f"[Arrêt terminé : code {rc}]")
            self._refresh_healthz(silent=True)

        self._run_worker(stop_sync_container, sync_dir, stream_output=True, on_done=done)

    def _check_ps(self) -> None:
        sync_dir = self._ensure_sync_dir()
        if not sync_dir:
            return
        self._set_deploy_buttons_enabled(False)
        self._append_console("$ docker compose ps")

        def done(res: tuple[int, str]) -> None:
            rc, out = res
            self._set_deploy_buttons_enabled(True)
            if out.strip():
                self._append_console(out.rstrip())
            else:
                self._append_console(f"(code {rc}, aucune sortie)")

        self._run_worker(fetch_container_status, sync_dir, on_done=done)

    def _refresh_healthz(self, silent: bool = False) -> None:
        port = int(self.port_spin.value())

        def done(res: dict[str, Any]) -> None:
            if res.get("ok"):
                version = res.get("data", {}).get("version", "?")
                self.header_health_badge.setText(f"Serveur : en ligne (v{version})")
                self._set_badge_style(self.header_health_badge, "ok")
                self.health_detail_lbl.setText(f"HTTP {res['status_code']} sur {res['url']} → {res['raw']}")
                if not silent:
                    self._append_console(f"✓ /healthz OK : {res['raw']}")
            else:
                self.header_health_badge.setText("Serveur : hors ligne")
                self._set_badge_style(self.header_health_badge, "error")
                err = res.get("error") or res.get("raw") or f"HTTP {res.get('status_code')}"
                self.health_detail_lbl.setText(f"Échec sur {res['url']} : {err}")
                if not silent:
                    self._append_console(f"✗ /healthz indisponible : {err}")

        self._run_worker(check_healthz, port, on_done=done)

    def _fetch_logs(self) -> None:
        sync_dir = self._ensure_sync_dir()
        if not sync_dir:
            return
        service = self.log_service_combo.currentText()
        tail = int(self.log_tail_combo.currentText())
        self.fetch_logs_btn.setEnabled(False)

        def done(res: tuple[int, str]) -> None:
            rc, out = res
            self.fetch_logs_btn.setEnabled(True)
            self._all_logs_buffer = out if out.strip() else f"(Aucun log pour {service}, code {rc})"
            self._apply_log_filter()

        self._run_worker(fetch_container_logs, sync_dir, service=service, tail=tail, on_done=done)

    def _toggle_log_follow(self) -> None:
        if self._log_stream_worker is not None:
            self._log_stream_worker.stop()
            self._log_stream_worker = None
            self.follow_logs_btn.setText("Suivi temps réel")
            return

        sync_dir = self._ensure_sync_dir()
        if not sync_dir:
            return
        service = self.log_service_combo.currentText()
        tail = int(self.log_tail_combo.currentText())

        self._all_logs_buffer = ""
        self.logs_console.clear()
        worker = LogStreamWorker(sync_dir, service, tail)
        self._log_stream_worker = worker
        self.follow_logs_btn.setText("Arrêter le suivi")

        def on_line(line: str) -> None:
            self._all_logs_buffer += line + "\n"
            needle = self.log_filter_input.text().strip().lower()
            if not needle or needle in line.lower():
                self.logs_console.appendPlainText(line)

        def on_end(rc: int, err: str) -> None:
            self._log_stream_worker = None
            self.follow_logs_btn.setText("Suivi temps réel")
            if err:
                self.logs_console.appendPlainText(f"[Suivi interrompu : {err}]")
            elif rc != 0:
                self.logs_console.appendPlainText(f"[Suivi terminé : code {rc}]")

        worker.line_received.connect(on_line)
        worker.stream_ended.connect(on_end)
        worker.start()

    def _apply_log_filter(self) -> None:
        needle = self.log_filter_input.text().strip().lower()
        if not needle:
            self.logs_console.setPlainText(self._all_logs_buffer)
            return
        filtered = [ln for ln in self._all_logs_buffer.splitlines() if needle in ln.lower()]
        self.logs_console.setPlainText("\n".join(filtered))

    def _clear_logs(self) -> None:
        self._all_logs_buffer = ""
        self.logs_console.clear()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._log_stream_worker is not None:
            self._log_stream_worker.stop()
            self._log_stream_worker.wait(1000)
        super().closeEvent(event)


def launch_pyqt_gui(sync_dir: str | None = None) -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Noteor Sync Manager")
    font = QFont("Inter", 10)
    app.setFont(font)
    window = NoteorSyncManagerWindow(initial_sync_dir=sync_dir)
    window.show()
    return app.exec()
