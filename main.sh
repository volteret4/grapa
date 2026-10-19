#!/usr/bin/env bash
# Grapa: fanzine con las reseñas y entrevistas de los artistas de Encarta.
#   ./main.sh              construye site/ (texto completo) y la sirve en http://127.0.0.1:8790/
#   ./main.sh build        solo construir site/
#   ./main.sh serve        solo servir site/
#   ./main.sh gh           versión pública en gh/ (extractos; enlaza a la Encarta de GitHub Pages)
#   ./main.sh fetch [...]  buscar artículos nuevos (fetch.py; reanudable, horas)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8790}"
VENV_PY="${VENV_PY:-$HOME/Scripts/python_venv/bin/python}"
case "${1:-all}" in
    build) python3 -I build.py ;;
    serve) python3 -I -m http.server "$PORT" -d site ;;
    gh) python3 -I build.py --publico --out gh ;;
    fetch) shift; "$VENV_PY" -I fetch.py "$@" ;;
    all) python3 -I build.py && echo "Grapa en http://127.0.0.1:$PORT/" && python3 -I -m http.server "$PORT" -d site ;;
    *) echo "Uso: $0 [build|serve|gh|fetch [opciones]]"; exit 1 ;;
esac
