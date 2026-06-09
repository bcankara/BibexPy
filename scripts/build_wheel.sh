#!/usr/bin/env bash
# BibexPy v2 — pip wheel build (macOS / Linux).
#
# Üç parçayı python_pkg/src altına kopyalar ve tek self-contained wheel üretir:
#   • apps/web/out/        → src/bibexpy/_web/      (Next.js static export)
#   • apps/api/{...}       → src/bibexpy/_server/   (FastAPI uygulaması)
#   • packages/bibex_core/ → src/bibex_core/        (çekirdek modüller)
#
# Çıktı: python_pkg/dist/bibexpy-*.whl
# Kullanım: bash scripts/build_wheel.sh

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

# Python yorumlayıcısı: PYTHON env'i varsa onu, yoksa python3'ü kullan.
PY="${PYTHON:-python3}"

copy_tree() {  # $1=from $2=to  — __pycache__/*.pyc hariç recursive kopya
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
  if [ ! -d node_modules ]; then echo "     node_modules yok — npm install"; npm install; fi
  npm run build:static )
[ -f "$WEB/out/index.html" ] || { echo "HATA: apps/web/out/index.html üretilmedi"; exit 1; }

echo "==> 2/6  Eski kopyaları temizle"
rm -rf "$DEST_WEB" "$DEST_SERVER" "$DEST_CORE"

echo "==> 3/6  Frontend kopyala -> _web"
copy_tree "$WEB/out" "$DEST_WEB"

echo "==> 4/6  Sunucu kopyala -> _server"
mkdir -p "$DEST_SERVER"
cp "$API/main.py" "$API/config.py" "$DEST_SERVER"/
for sub in routers services models jobs; do
    copy_tree "$API/$sub" "$DEST_SERVER/$sub"
done
# _server'ı Python alt-paketi yap — setuptools .py dosyalarını ancak paket
# olarak keşfederse wheel'e dahil eder (package-data ile .py güvenilir taşınmaz).
printf '"""BibexPy gömülü FastAPI sunucusu (build-time kopya). Runtime sys.path'"'"'e eklenir."""\n' \
    > "$DEST_SERVER/__init__.py"

echo "==> 5/6  bibex_core kopyala"
copy_tree "$CORE" "$DEST_CORE"
rm -f "$DEST_CORE/pyproject.toml"   # vendored kopyada gereksiz

echo "==> 6/6  Wheel build (python -m build)"
( cd "$PKG"
  "$PY" -m pip install --quiet --upgrade build
  rm -rf dist build
  find . -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  # --wheel: sdist'i atla. _server/_web build-time kopyalari git'te izlenmediginden
  # sdist'e girmez; wheel'i dogrudan kaynak agacindan kurmak hepsini dahil eder
  # (build_wheel.ps1 ile parity).
  "$PY" -m build --wheel )

echo ""
echo "BAŞARILI. Üretilen paketler:"
ls -1 "$PKG/dist"
echo ""
echo "Kurulum testi:  pip install $PKG/dist/bibexpy-2.0.0-py3-none-any.whl"
echo "Yayınlama:      python -m twine upload python_pkg/dist/*"
