# CobbleBOT

Bot de Discord para un servidor de Minecraft con **COBBLEVERSE** (Cobblemon). Muestra estadísticas, equipos, medallas y rankings de los jugadores, avisa del estado del servidor y deja a los admins gestionarlo desde Discord.

## Comandos

| Comando | Quién | Qué hace |
|---|---|---|
| `/help` | Todos | Lista de comandos (cada uno ve los que puede usar) |
| `/stats jugador` | Todos | Ficha completa: horas, dinero, medallas y ligas por región, equipo, colección y estadísticas |
| `/equipo jugador` | Todos | Los 6 Pokémon del equipo (mote, nivel, sexo, shiny) y resumen del PC |
| `/medallas jugador [region]` | Todos | Medallas por región y ligas conseguidas |
| `/top categoria [region] [cantidad]` | Todos | Rankings: horas, dinero, medallas, ligas, Pokémon, shinies, especies, capturas… |
| `/estado`, `/jugadores` | Todos | Si el servidor está online y quién está conectado |
| `/dar pokemon …` | Rol de regalos | Da un Pokémon con todo personalizable: nivel, shiny, sexo, naturaleza, habilidad, IVs, EVs, movimientos, objeto, Poké Ball, mote, forma, teratipo… |
| `/dar dinero jugador cantidad` | Rol de regalos | Da CobbleDollars |
| `/dar objeto jugador mod objeto [cantidad]` | Rol de regalos | Da objetos: se elige el mod y se busca el objeto por nombre |
| `/admin …` | Admins | Expulsar, banear, whitelist, op, modo de juego, teletransporte, hora, clima, dificultad, curar, experiencia, guardar y reiniciar (con confirmación) |
| `/admin mensaje` | Admins | Mensajes en el juego: chat, título en el centro o encima de la hotbar, con colores (`&6`, `&l`…) |
| `/comando texto` | Admins | Cualquier comando de la lista de permitidos, con sugerencias |
| `/actualizar` | Admins | Guarda el mundo y recarga los datos al momento |
| `/diagnostico jugador` | Admins | Qué archivos y logros encuentra el bot (para ajustar la config) |

Los comandos de admin y de regalos solo los ven quienes tienen el permiso *Gestionar servidor* (se puede dar acceso a otros roles en *Ajustes del servidor → Integraciones*).

Además, el bot avisa en un canal cuando el servidor se cae, se reinicia y **en cuanto vuelve a estar en pie**, y actualiza los datos solo cada pocos minutos.

### De dónde saca cada dato

| Dato | Archivo (dentro de la carpeta del mundo) |
|---|---|
| Horas, muertes, distancia, stats de Cobblemon | `stats/<uuid>.json` |
| Medallas y ligas | `advancements/<uuid>.json` (COBBLEVERSE las da como logros al vencer a líderes y campeones) |
| Equipo y PC | `pokemon/playerpartystore/…` y `pokemon/pcstore/…` |
| Dinero | `cobbledollarsplayerdata/` (CobbleDollars) |
| Especies, objetos, movimientos… | Los `.jar` de la carpeta `mods/` del servidor |

---

## Instalación con Docker

El bot va en su propio `docker compose`, **separado del servidor de Minecraft**: se une a la red de Docker del servidor para hablar con él por RCON y monta los archivos del servidor **en solo lectura** (nunca toca el mundo).

### 1. Crear el bot en Discord

1. https://discord.com/developers/applications → **New Application**.
2. **Bot** → **Reset Token** → copia el token (va en el `.env`, nunca en otro sitio).
3. **OAuth2 → URL Generator**: scopes `bot` y `applications.commands`; permisos `Send Messages`, `Embed Links`, `View Channels`. Abre la URL e invita el bot.
4. En Discord activa **Ajustes → Avanzado → Modo desarrollador** para copiar IDs (clic derecho → Copiar ID).

### 2. Servidor de Minecraft

Necesita RCON activado (`enable-rcon=true`, `rcon.password=…` en `server.properties`). **No publiques el puerto RCON** fuera de Docker: el bot entra por la red interna.

### 3. Poner en marcha el bot

```bash
git clone https://github.com/<tu-usuario>/cobble-bot.git
cd cobble-bot
cp .env.ejemplo .env               # rellena token, IDs, contraseña RCON, MC_DATOS y RED_SERVIDOR
cp config.ejemplo.toml config.toml # ajuste fino (no lleva claves)
chmod 600 .env
./bot.sh arrancar
./bot.sh logs                      # debería decir "Conectado como ..."
```

### Manejo diario

| Orden | Qué hace |
|---|---|
| `./bot.sh actualizar` | Baja la última versión de GitHub y la pone en marcha |
| `./bot.sh reiniciar` | Reinicia el bot volviendo a leer `.env` y `config.toml` |
| `./bot.sh logs` | Log del bot |
| `./bot.sh error CODIGO` | Detalle de un error (cuando algo falla, el bot da un código en Discord) |
| `./bot.sh parar` / `arrancar` / `estado` | Lo obvio |

### Sin Docker (pruebas)

Con Python 3.11+: `python -m venv venv`, `venv/bin/pip install -r requirements.txt`, rellena `.env` (con `MC_CARPETA_SERVIDOR` apuntando al servidor o a una instancia del juego) y `python run.py config.toml`. En Windows, `iniciar.bat` hace todo eso con doble clic.

---

## Configuración

- **`.env`**: claves y datos de *esta* instalación (token, IDs de Discord, contraseña RCON, rutas). Lista completa y explicada en [.env.ejemplo](.env.ejemplo). **Nunca se sube al repositorio.**
- **`config.toml`**: ajuste fino sin claves (comandos permitidos en `/comando`, medallas, rankings, nombre con el que firma el bot en el juego…). Plantilla: [config.ejemplo.toml](config.ejemplo.toml).

Prioridad: variable de entorno real > `.env` > `config.toml`.

### Medallas y ligas

`config.ejemplo.toml` ya trae los IDs de los 32 líderes de gimnasio y los 4 campeones de COBBLEVERSE 1.7.42 (Kanto, Johto, Hoenn y Sinnoh). Si en otra versión no cuadran, usa `/diagnostico` con un jugador que tenga medallas: te da un `.txt` con sus IDs de logros para ajustar `ids_medallas` / `ids_ligas`.

### Estadísticas de Cobblemon

`/stats` muestra todas las claves `cobblemon:` que encuentre. Si algún ranking de `/top` sale vacío, mira las claves reales en `world/stats/<uuid>.json` y corrígelas en `[rankings_cobblemon]`. Los datos se escriben en disco cuando el servidor guarda; el bot fuerza un guardado cada `actualizar_minutos` (y con `/actualizar`).

---

## Seguridad

- Claves (token, contraseña RCON) e IDs solo en `.env`, con permisos `600`. `.gitignore` y `.dockerignore` impiden que acaben en el repo o en la imagen.
- `/comando` solo admite los comandos de `[comandos] permitidos` (evita `"*"`). `stop` no está: para reiniciar está `/admin reiniciar`, que pide confirmación.
- Cada uso de `/comando`, `/dar` y `/admin` queda registrado en `DISCORD_CANAL_REGISTRO` (si está configurado).

## Pruebas

```bash
python -X utf8 tests/test_bot.py
```
Prueban RCON contra un servidor falso, el lector NBT, perfiles, medallas, rankings y embeds con un mundo de COBBLEVERSE inventado.
