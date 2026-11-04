#!/usr/bin/env bash
# BibexPy v2 — pip wheel build (macOS / Linux).
#
# Copies three pieces under python_pkg/src and produces a single self-contained wheel:
#   • apps/web/out/        → src/bibexpy/_web/      (Next.js static export)
#   • apps/api/{...}       → src/bibexpy/_server/   (FastAPI application)
#   • packages/bibex_core/ → src/bibex_core/        (core modules)
#
# Output: python_pkg/dist/bibexpy-*.whl
# Usage: bash scripts/build_wheel.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$ROOT/apps/web"
API="$ROOT/apps/api"
CORE="$ROOT/packages/bibex_core"
PKG="$ROOT/python_pkg"
SRC="$PKG/src"
DEST_WEB="$SRC/bibexpy/_web"
DEST_SERVER="$SRC/bibexpy/_server"
DEST_CORE="$SRC/bibex_core"

# Python interpreter: use the PYTHON env var if set, otherwise python3.
PY="${PYTHON:-python3}"

copy_tree() {  # $1=from $2=to  — recursive copy excluding __pycache__/*.pyc
    local from="$1" to="$2"
    mkdir -p "$to"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' "$from"/ "$to"/
    else
        cp -R "$from"/. "$to"/
        find "$to" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
        find "$to" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
    fi
}

echo "==> 1/6  Frontend static export (npm run build:static)"
( cd "$WEB"
  if [ ! -d node_modules ]; then echo "     node_modules missing — npm install"; npm install; fi
  npm run build:static )
[ -f "$WEB/out/index.html" ] || { echo "ERROR: apps/web/out/index.html was not produced"; exit 1; }

echo "==> 2/6  Clean old copies"
rm -rf "$DEST_WEB" "$DEST_SERVER" "$DEST_CORE"

echo "==> 3/6  Copy frontend -> _web"
copy_tree "$WEB/out" "$DEST_WEB"

echo "==> 4/6  Copy server -> _server"
mkdir -p "$DEST_SERVER"
cp "$API/main.py" "$API/config.py" "$DEST_SERVER"/
for sub in routers services models jobs; do
    copy_tree "$API/$sub" "$DEST_SERVER/$sub"
done
# Make _server a Python subpackage — setuptools includes .py files in the wheel
# only if it discovers them as a package (.py is not reliably carried via package-data).
printf '"""BibexPy embedded FastAPI server (build-time copy). Added to sys.path at runtime."""\n' \
    > "$DEST_SERVER/__init__.py"

echo "==> 5/6  Copy bibex_core"
copy_tree "$CORE" "$DEST_CORE"
rm -f "$DEST_CORE/pyproject.toml"   # unnecessary in the vendored copy

echo "==> 6/6  Wheel build (python -m build)"
( cd "$PKG"
  "$PY" -m pip install --quiet --upgrade build
  rm -rf dist build
  find . -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  # --wheel: skip the sdist. Since the _server/_web build-time copies are not tracked
  # in git, they do not make it into the sdist; building the wheel directly from the
  # source tree includes all of them (parity with build_wheel.ps1).
  "$PY" -m build --wheel )

echo ""
echo "SUCCESS. Built packages:"
ls -1 "$PKG/dist"
echo ""
echo "Install test:   pip install $PKG/dist/bibexpy-2.0.0-py3-none-any.whl"
echo "Publish:        python -m twine upload python_pkg/dist/*"
