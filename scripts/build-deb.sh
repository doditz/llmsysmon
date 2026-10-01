#!/usr/bin/env bash
# Build a local llmsysmon .deb package from the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$(mktemp -d)"
DEB_NAME="llmsysmon_1.0.0_all.deb"

cleanup() { rm -rf "$BUILD_DIR"; }
trap cleanup EXIT

mkdir -p "$BUILD_DIR/DEBIAN"
cp "$REPO_ROOT/debian/DEBIAN/control" "$BUILD_DIR/DEBIAN/control"

# Stage files according to debian/install
while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | awk '{$1=$1};1')"
    [ -z "$line" ] && continue
    src="${line%% *}"
    dst="${line#* }"
    src="$REPO_ROOT/$src"
    dst="$BUILD_DIR/$dst"
    if [ -d "$src" ] || [ "${dst%/}" != "$dst" ]; then
        mkdir -p "$dst"
    else
        mkdir -p "$(dirname "$dst")"
    fi
    cp -r "$src" "$dst"
done < "$REPO_ROOT/debian/install"

# Ensure executable bits
chmod 755 "$BUILD_DIR/usr/bin/llmsysmon"
chmod 755 "$BUILD_DIR/usr/share/llmsysmon/cli.py"

dpkg-deb --build "$BUILD_DIR" "$REPO_ROOT/$DEB_NAME"
echo "Built: $REPO_ROOT/$DEB_NAME"
