#!/usr/bin/env bash
# Publica gh/ (versión pública de Grapa, ./main.sh gh) en la rama gh-pages de un repo de GitHub.
#   ./publish_gh_pages.sh git@github.com:volteret4/grapa.git
# Cada publicación sustituye la anterior (rama huérfana con un solo commit, push forzado).
# En GitHub: Settings → Pages → «Deploy from a branch» → gh-pages / (root).
# Encarta lee https://volteret4.github.io/grapa/data/artistas.json (GRAPA_URL en encarta/web/app.js).

set -euo pipefail
cd "$(dirname "$0")"

REMOTE="${1:?Uso: $0 <url del repo de GitHub>}"
[ -f gh/index.html ] || { echo "Falta gh/: ejecuta ./main.sh gh"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp -r gh/. "$TMP/"
touch "$TMP/.nojekyll"
git -C "$TMP" init -q -b gh-pages
git -C "$TMP" add -A
git -C "$TMP" commit -q -m "Grapa: versión pública $(date +%Y-%m-%d)"
git -C "$TMP" push -f "$REMOTE" gh-pages
echo "Publicado en la rama gh-pages de $REMOTE"
