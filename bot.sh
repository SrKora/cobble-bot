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

# Servidor de Minecraft fuera de Docker: BOT_SIN_RED_DOCKER=1 en el .env
dc() {
  if [ -f .env ] && grep -qE '^BOT_SIN_RED_DOCKER=(1|true|si|yes)' .env; then
    docker compose -f docker-compose.yml -f docker-compose.host.yml "$@"
  else
    docker compose "$@"
  fi
}

comprobar() {
  [ -f .env ] || { echo "Falta .env: copia .env.ejemplo a .env y rellénalo"; exit 1; }
  [ -f config.toml ] || { echo "Falta config.toml: copia config.ejemplo.toml a config.toml"; exit 1; }
  mkdir -p datos   # datos propios del bot (vínculos y teams); si la crea Docker, sería de root
}

case "${1:-ayuda}" in
  arrancar)   comprobar; dc up -d --build ;;
  parar)      dc down ;;
  reiniciar)  comprobar; dc up -d --force-recreate ;;
  actualizar) comprobar; git pull --ff-only && dc up -d --build ;;
  estado)     dc ps ;;
  logs)       dc logs -f --tail 100 ;;
  error)
    [ -n "${2:-}" ] || { echo "Uso: ./bot.sh error CODIGO"; exit 1; }
    dc logs --no-log-prefix 2>&1 | grep -A 40 "Error ${2^^} " | sed '/^20[0-9][0-9]-.*\[INFO\]/q' ;;
  *)          ayuda ;;
esac
