#!/usr/bin/env bash
# Manejo de CobbleBOT en Docker. Uso: ./bot.sh <orden>
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

ayuda() {
  cat <<'EOF'
Órdenes:
  arrancar          Construye y arranca el bot
  parar             Para el bot
  reiniciar         Reinicia el bot volviendo a leer .env y config.toml
  actualizar        Descarga la última versión de GitHub (git pull) y la pone en marcha
  estado            Si está en marcha
  logs              Sigue el log del bot (Ctrl+C para salir)
  error CODIGO      Detalle de un error (el código que da el bot en Discord)
EOF
}

comprobar() {
  [ -f .env ] || { echo "Falta .env: copia .env.ejemplo a .env y rellénalo"; exit 1; }
  [ -f config.toml ] || { echo "Falta config.toml: copia config.ejemplo.toml a config.toml"; exit 1; }
}

case "${1:-ayuda}" in
  arrancar)   comprobar; docker compose up -d --build ;;
  parar)      docker compose down ;;
  reiniciar)  comprobar; docker compose up -d --force-recreate ;;
  actualizar) comprobar; git pull --ff-only && docker compose up -d --build ;;
  estado)     docker compose ps ;;
  logs)       docker compose logs -f --tail 100 ;;
  error)
    [ -n "${2:-}" ] || { echo "Uso: ./bot.sh error CODIGO"; exit 1; }
    docker compose logs --no-log-prefix 2>&1 | grep -A 40 "Error ${2^^} " | sed '/^20[0-9][0-9]-.*\[INFO\]/q' ;;
  *)          ayuda ;;
esac
