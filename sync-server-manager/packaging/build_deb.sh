#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$MANAGER_DIR/.." && pwd)"

PKG_NAME="noteor-sync-manager"
VERSION="1.1.0"
ARCH="all"
OUT_DIR="$MANAGER_DIR/dist"
STAGE_DIR="$(mktemp -d)"
chmod 0755 "$STAGE_DIR"

cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

mkdir -p \
  "$STAGE_DIR/DEBIAN" \
  "$STAGE_DIR/usr/bin" \
  "$STAGE_DIR/usr/lib/$PKG_NAME/scripts" \
  "$STAGE_DIR/usr/share/applications" \
  "$STAGE_DIR/usr/share/icons/hicolor/scalable/apps" \
  "$OUT_DIR"

# Métadonnées Debian
cp "$SCRIPT_DIR/DEBIAN/control" "$STAGE_DIR/DEBIAN/control"
cp "$SCRIPT_DIR/DEBIAN/postinst" "$STAGE_DIR/DEBIAN/postinst"
chmod 0755 "$STAGE_DIR/DEBIAN/postinst"

# Code applicatif Python + script de vérification des dépendances
cp "$MANAGER_DIR/main.py" "$STAGE_DIR/usr/lib/$PKG_NAME/main.py"
cp "$MANAGER_DIR/manager_core.py" "$STAGE_DIR/usr/lib/$PKG_NAME/manager_core.py"
cp "$MANAGER_DIR/gui_pyqt.py" "$STAGE_DIR/usr/lib/$PKG_NAME/gui_pyqt.py"
cp "$MANAGER_DIR/gui_web.py" "$STAGE_DIR/usr/lib/$PKG_NAME/gui_web.py"
cp "$MANAGER_DIR/scripts/check_deps.sh" "$STAGE_DIR/usr/lib/$PKG_NAME/scripts/check_deps.sh"

chmod 0755 "$STAGE_DIR/usr/lib/$PKG_NAME/main.py"
chmod 0644 \
  "$STAGE_DIR/usr/lib/$PKG_NAME/manager_core.py" \
  "$STAGE_DIR/usr/lib/$PKG_NAME/gui_pyqt.py" \
  "$STAGE_DIR/usr/lib/$PKG_NAME/gui_web.py"
chmod 0755 "$STAGE_DIR/usr/lib/$PKG_NAME/scripts/check_deps.sh"

# Lanceur /usr/bin/noteor-sync-manager
cat > "$STAGE_DIR/usr/bin/$PKG_NAME" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="/usr/lib/noteor-sync-manager${PYTHONPATH:+:$PYTHONPATH}"
exec python3 /usr/lib/noteor-sync-manager/main.py "$@"
EOF
chmod 0755 "$STAGE_DIR/usr/bin/$PKG_NAME"

# Entrée bureau & icône Noteor
cp "$SCRIPT_DIR/noteor-sync-manager.desktop" "$STAGE_DIR/usr/share/applications/$PKG_NAME.desktop"
chmod 0644 "$STAGE_DIR/usr/share/applications/$PKG_NAME.desktop"

ICON_SRC="$REPO_ROOT/web/public/icon.svg"
if [ -f "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$STAGE_DIR/usr/share/icons/hicolor/scalable/apps/$PKG_NAME.svg"
  cp "$ICON_SRC" "$STAGE_DIR/usr/lib/$PKG_NAME/icon.svg"
  chmod 0644 \
    "$STAGE_DIR/usr/share/icons/hicolor/scalable/apps/$PKG_NAME.svg" \
    "$STAGE_DIR/usr/lib/$PKG_NAME/icon.svg"
fi

# Calcul Installed-Size (en Ko)
INSTALLED_KB="$(du -sk "$STAGE_DIR/usr" | awk '{print $1}')"
echo "Installed-Size: $INSTALLED_KB" >> "$STAGE_DIR/DEBIAN/control"

DEB_PATH="$OUT_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$STAGE_DIR" "$DEB_PATH"

echo "Paquet .deb généré : $DEB_PATH"
