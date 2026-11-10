#!/usr/bin/env bash
# Manejo de CobbleBOT en Docker. Uso: ./bot.sh <orden>
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

ayuda() {
  cat <<'EOF'
Órdenes:
  comprobar         Revisa que todo está listo para arrancar (Docker, .env, carpetas, red)
  arrancar          Construye y arranca el bot
  parar             Para el bot
  reiniciar         Reinicia el bot volviendo a leer .env y config.toml
  actualizar        Descarga la última versión de GitHub (git pull) y la pone en marcha
  estado            Si está en marcha
  logs              Sigue el log del bot (Ctrl+C para salir)
  error CODIGO      Detalle de un error (el código que da el bot en Discord)
  pruebas           Pasa las pruebas automáticas dentro de la imagen del bot
EOF
}

# Valor de una variable del .env (vacío si no está)
env_valor() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed -E 's/^"(.*)"$/\1/; s/[[:space:]]+#.*$//' || true; }

# Servidor de Minecraft fuera de Docker: BOT_SIN_RED_DOCKER=1 en el .env
dc() {
  # Si el .env no dice con qué usuario correr, se usa el tuyo
  [ -n "$(env_valor UID_GID)" ] || export UID_GID="$(id -u):$(id -g)"
  if [[ "$(env_valor BOT_SIN_RED_DOCKER)" =~ ^(1|true|si|yes)$ ]]; then
    docker compose -f docker-compose.yml -f docker-compose.host.yml "$@"
  else
    docker compose "$@"
  fi
}

fallo() { echo "❌ $*"; exit 1; }

comprobar() {
  command -v docker >/dev/null || fallo "No está Docker. Instálalo: https://docs.docker.com/engine/install/"
  docker compose version >/dev/null 2>&1 || fallo "Falta Docker Compose (el plugin 'docker compose')."
  docker info >/dev/null 2>&1 || fallo "No puedes usar Docker con tu usuario. Ejecuta:
   sudo usermod -aG docker \$USER     (y cierra y vuelve a abrir la terminal)
   o comprueba que Docker está en marcha: sudo systemctl start docker"
  [ -f .env ] || fallo "Falta .env: cp .env.ejemplo .env && chmod 600 .env, y rellénalo"
  [ -f config.toml ] || fallo "Falta config.toml: cp config.ejemplo.toml config.toml"
  local token; token="$(env_valor DISCORD_TOKEN)"
  [ -n "$token" ] && [ "$token" != "PEGA_AQUI_EL_TOKEN_DEL_BOT" ] || fallo "Pon el token del bot en DISCORD_TOKEN (.env)"
  [ -n "$(env_valor RCON_PASSWORD)" ] || fallo "Pon la contraseña RCON del servidor en RCON_PASSWORD (.env)"
  local datos; datos="$(env_valor MC_DATOS)"
  [ -n "$datos" ] || fallo "Pon en MC_DATOS la carpeta del servidor de Minecraft (.env)"
  [ -d "$datos" ] || fallo "MC_DATOS=$datos no existe en esta máquina"
  [ -r "$datos/usercache.json" ] || [ -r "$datos/server.properties" ] || \
    echo "⚠️  En $datos no veo server.properties ni usercache.json: ¿es la carpeta del servidor?"
  if [[ ! "$(env_valor BOT_SIN_RED_DOCKER)" =~ ^(1|true|si|yes)$ ]]; then
    local red; red="$(env_valor RED_SERVIDOR)"; red="${red:-cobbleverse_default}"
    docker network inspect "$red" >/dev/null 2>&1 || fallo "No existe la red de Docker '$red' (RED_SERVIDOR).
   Mira las que hay con: docker network ls   (arranca antes el servidor de Minecraft)"
  fi
  # Carpeta de datos propia del bot (vínculos, teams, ligas). Si la crea Docker, sería de root y no podría escribir
  mkdir -p datos
  [ -w datos ] || fallo "No puedes escribir en ./datos. Arréglalo con: sudo chown -R $(id -u):$(id -g) datos"
  echo "✅ Todo listo"
}

case "${1:-ayuda}" in
  comprobar)  comprobar ;;
  arrancar)   comprobar; dc up -d --build ;;
  parar)      dc down ;;
  reiniciar)  comprobar; dc up -d --force-recreate ;;
  actualizar) comprobar; git pull --ff-only && dc up -d --build ;;
  estado)     dc ps ;;
  logs)       dc logs -f --tail 100 ;;
  error)
    [ -n "${2:-}" ] || { echo "Uso: ./bot.sh error CODIGO"; exit 1; }
    dc logs --no-log-prefix 2>&1 | grep -A 40 "Error ${2^^} " | sed '/^20[0-9][0-9]-.*\[INFO\]/q' ;;
  pruebas)
    dc build -q >/dev/null
    docker run --rm -v "$PWD":/w -w /w --entrypoint python cobble-bot -X utf8 tests/test_bot.py ;;
  *)          ayuda ;;
esac
