# CobbleBOT

Bot de Discord para servidores de Minecraft con **Cobblemon**, pensado para el modpack **COBBLEVERSE**.

- 📊 **Para todos:** fichas de jugador, equipos, medallas por región y rankings.
- 🎁 **Para tu equipo:** regala Pokémon (con IVs, EVs, movimientos…), dinero y objetos desde Discord.
- 🛡️ **Teams:** equipos de Minecraft con color y prefijo, gestionados desde Discord, y ranking entre teams.
- 🔧 **Para los admins:** gestiona el servidor (expulsar, banear, whitelist, clima, mensajes en pantalla, reinicios…) sin entrar al juego.
- 🟢 **Avisos:** el bot avisa en un canal cuando el servidor se cae, se reinicia y **en cuanto se puede volver a entrar**.

---

## Índice

1. [Requisitos](#1-requisitos)
2. [Crear el bot en Discord](#2-crear-el-bot-en-discord)
3. [Preparar el servidor de Minecraft](#3-preparar-el-servidor-de-minecraft)
4. [Instalar el bot](#4-instalar-el-bot)
5. [Primeros pasos en Discord](#5-primeros-pasos-en-discord)
6. [Comandos](#6-comandos)
7. [Configuración completa](#7-configuración-completa)
8. [Adaptarlo a tu mundo o modpack](#8-adaptarlo-a-tu-mundo-o-modpack)
9. [Mantenimiento](#9-mantenimiento)
10. [Problemas frecuentes](#10-problemas-frecuentes)
11. [Seguridad](#11-seguridad)

---

## 1. Requisitos

- Un **servidor de Minecraft con Cobblemon** (Fabric). Probado con **COBBLEVERSE 1.7.42** (Minecraft 1.21.1, Cobblemon 1.7). Con otros modpacks de Cobblemon funciona casi todo; las medallas y ligas hay que ajustarlas ([apartado 8](#8-adaptarlo-a-tu-mundo-o-modpack)).
- **RCON activado** en el servidor (el bot lo usa para mandar comandos y saber quién está conectado).
- **El bot tiene que ir en la misma máquina que el servidor**: lee los archivos del mundo (estadísticas, Pokémon, medallas) directamente del disco, en solo lectura.
- **Docker** con Docker Compose (recomendado; en Windows, dentro de WSL) **o** Python 3.11+.
- Un servidor de Discord donde tengas permiso de **Gestionar servidor**.

Opcional: **CobbleDollars** (para el dinero en fichas, rankings y `/dar dinero`). Si no lo tienes, el dinero simplemente sale vacío.

---

## 2. Crear el bot en Discord

### 2.1 La aplicación y el token

1. Entra en https://discord.com/developers/applications → **New Application** → ponle nombre (será el nombre del bot).
2. En **General Information**, copia el **Application ID** (lo usarás para la invitación).
3. En **Bot**:
   - **Reset Token** → copia el token. **Es la contraseña del bot**: va en el `.env` y en ningún otro sitio. Si se filtra, vuelve a darle a Reset Token.
   - **Privileged Gateway Intents**: no hace falta activar ninguno.
   - Si no quieres que otros puedan invitar tu bot a sus servidores, desactiva **Public Bot**.

### 2.2 Invitar el bot a tu servidor

Abre esta dirección cambiando `TU_APPLICATION_ID`:

```
https://discord.com/oauth2/authorize?client_id=TU_APPLICATION_ID&scope=bot+applications.commands&permissions=52224
```

Elige tu servidor y acepta. Los permisos que pide (`52224`) son los mínimos: **Ver canales, Enviar mensajes, Insertar enlaces y Adjuntar archivos** (el último, para el `.txt` de `/diagnostico`).

### 2.3 Sacar los IDs que necesita el bot

En Discord: **Ajustes de usuario → Avanzado → Modo desarrollador** (actívalo). Ahora con **clic derecho → Copiar ID** puedes copiar:

| Qué | Dónde | Para qué |
|---|---|---|
| ID del **servidor** | Clic derecho en el icono del servidor | `DISCORD_GUILD_ID` (obligatorio: hace que los comandos aparezcan al instante) |
| ID de un **canal de estado** | Clic derecho en el canal | `DISCORD_CANAL_ESTADO`: avisos de servidor caído / reiniciando / online |
| ID de un **canal de registro** (privado) | Clic derecho en el canal | `DISCORD_CANAL_REGISTRO`: queda apuntado quién usa `/dar`, `/admin` y `/comando` |
| ID de los **roles** de admin y de regalos | Ajustes del servidor → Roles → clic derecho | `DISCORD_ROLES_ADMIN`, `DISCORD_ROLES_REGALOS` |

**Canales recomendados:**
- `#estado-del-servidor`: público, de solo lectura para los miembros. Ahí avisa el bot.
- `#registro-bot`: privado, solo para admins. Ahí queda constancia de cada acción.

Asegúrate de que el bot puede **ver** esos canales y **enviar mensajes / insertar enlaces** en ellos (Editar canal → Permisos).

---

## 3. Preparar el servidor de Minecraft

### 3.1 Activar RCON

En `server.properties` del servidor:

```properties
enable-rcon=true
rcon.port=25575
rcon.password=una_contraseña_larga_y_aleatoria
# Opcional: que los operadores no vean "[Rcon: ...]" en el chat cada vez que el bot hace algo
broadcast-rcon-to-ops=false
```

Reinicia el servidor. **No abras el puerto 25575 al exterior** (router, firewall): el bot entra desde la propia máquina o desde la red interna de Docker.

Si tu servidor usa la imagen de Docker **`itzg/minecraft-server`**, en lugar de editar `server.properties` pon estas variables en su `docker-compose.yml`:

```yaml
      ENABLE_RCON: "true"
      RCON_PASSWORD: "una_contraseña_larga_y_aleatoria"
      BROADCAST_RCON_TO_OPS: "false"
```

### 3.2 Datos que vas a necesitar

- **La carpeta del servidor** (donde están `server.properties`, `usercache.json`, `mods/` y la carpeta del mundo). Con `itzg/minecraft-server` es la carpeta que montas en `/data`.
- **El nombre de la carpeta del mundo**: es el `level-name` de `server.properties` (normalmente `world`).
- **Si el servidor está en Docker**: el **nombre de su contenedor** (`docker ps`) y el de su **red** (`docker network ls`; con Docker Compose suele ser `<carpeta>_default`).

---

## 4. Instalar el bot

### 4.1 Descargar y configurar

```bash
git clone https://github.com/<usuario>/cobble-bot.git
cd cobble-bot
cp .env.ejemplo .env
cp config.ejemplo.toml config.toml
chmod 600 .env          # que solo tu usuario pueda leer las claves
nano .env               # rellénalo (ver abajo)
```

Lo mínimo que hay que rellenar en el `.env` (todas las opciones están explicadas en [.env.ejemplo](.env.ejemplo)):

| Variable | Qué poner |
|---|---|
| `DISCORD_TOKEN` | El token del [paso 2.1](#21-la-aplicación-y-el-token) |
| `DISCORD_GUILD_ID` | ID de tu servidor de Discord |
| `DISCORD_CANAL_ESTADO` | ID del canal de avisos de estado (0 = sin avisos) |
| `DISCORD_ROLES_ADMIN` / `DISCORD_ROLES_REGALOS` | IDs de roles separados por comas (los administradores de Discord siempre pueden) |
| `RCON_PASSWORD` | La misma `rcon.password` del servidor |
| `MC_CARPETA_MUNDO` | El `level-name` (normalmente `world`) |

Y según cómo tengas el servidor, elige **una** de estas tres formas:

### 4.2 Opción A: servidor en Docker (recomendada)

El bot se une a la red de Docker del servidor y monta su carpeta en solo lectura.

En el `.env`:
```env
RCON_HOST=nombre-del-contenedor-del-servidor     # ej. mc o cobbleverse-mc (docker ps)
MC_DATOS=/ruta/a/la/carpeta/data/del/servidor    # la carpeta que el servidor monta en /data
RED_SERVIDOR=nombre_de_la_red                    # docker network ls (ej. minecraft_default)
UID_GID=1000:1000                                # id -u : id -g del dueño de esa carpeta
```

```bash
./bot.sh arrancar
./bot.sh logs        # tiene que salir "Conectado como TuBot#1234"
```

### 4.3 Opción B: servidor sin Docker, bot en Docker

En el `.env`:
```env
BOT_SIN_RED_DOCKER=1             # el bot usa la red de la máquina
RCON_HOST=127.0.0.1
MC_DATOS=/ruta/a/la/carpeta/del/servidor
UID_GID=1000:1000                # el usuario que ejecuta el servidor
```

```bash
./bot.sh arrancar
./bot.sh logs
```

### 4.4 Opción C: sin Docker (Python)

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

En el `.env`: `RCON_HOST=127.0.0.1` y `MC_CARPETA_SERVIDOR=/ruta/a/la/carpeta/del/servidor` (en Windows con `/`, no con `\`).

```bash
venv/bin/python run.py config.toml
```

En **Windows** basta con doble clic en `iniciar.bat` (crea el entorno la primera vez). Para que arranque solo en Linux sin Docker tienes una plantilla de servicio en [cobblebot.service](cobblebot.service).

> 💡 Para **probar el bot sin servidor** puedes apuntar `MC_CARPETA_SERVIDOR` a una instancia del juego (la carpeta `minecraft` de Prism/CurseForge) y `MC_CARPETA_MUNDO` a `saves/NombreDelMundo`. Las fichas, equipos, medallas y rankings funcionarán; lo que necesita RCON (`/estado`, `/admin`, `/dar`…) no.

> ⚠️ No tengas **dos copias del bot encendidas con el mismo token** (por ejemplo Docker y `iniciar.bat`): responderían dos veces a cada comando.

---

## 5. Primeros pasos en Discord

1. Escribe `/help`: te enseña los comandos que puedes usar. Si no aparece ninguno, pulsa **Ctrl+R** en Discord.
2. `/estado`: debería decir que el servidor está online. Si dice que no responde, revisa RCON ([problemas frecuentes](#10-problemas-frecuentes)).
3. Entra al servidor, juega un poco y usa `/actualizar` (guarda el mundo y recarga). Luego `/stats TuNombre`.
4. `/diagnostico TuNombre` (admins): te dice qué archivos encuentra del jugador y adjunta un `.txt` con los IDs de sus logros. Útil para ajustar medallas.
5. **Quién ve qué:** los comandos de admin y de regalos solo los ven quienes tienen *Gestionar servidor*. Para enseñárselos a otro rol sin darle ese permiso: **Ajustes del servidor → Integraciones → (tu bot) → elige el comando → añade el rol**. Ese rol, además, tiene que estar en `DISCORD_ROLES_ADMIN` o `DISCORD_ROLES_REGALOS` para que el bot le deje usarlo.

---

## 6. Comandos

Al escribir un **jugador** salen sugerencias; los conectados aparecen arriba con 🟢. El jugador puede estar desconectado para consultar sus datos (se usa lo último que guardó el servidor), pero tiene que estar **conectado** para recibir regalos o acciones de admin.

### Para todos

| Comando | Qué hace |
|---|---|
| `/help` | Lista de comandos que puedes usar |
| `/stats jugador` | Ficha: horas, dinero, medallas y ligas por región, equipo, colección y estadísticas de Cobblemon |
| `/equipo jugador` | Los 6 Pokémon del equipo (mote, nivel, sexo, shiny) y resumen del PC |
| `/medallas jugador [región]` | Medallas por región y ligas conseguidas |
| `/top categoría [región] [cantidad]` | Ranking. Categorías: horas, dinero, medallas, ligas, pokemon, shinies, especies, nivel_max, logros, muertes, distancia, mobs, capturas, shinies_capturados, combates, evoluciones, huevos (y las que añadas). La región filtra medallas y ligas |
| `/estado` | Si el servidor está online y cuánta gente hay |
| `/jugadores` | Quién está conectado |

### Teams

| Comando | Qué hace |
|---|---|
| `/team info [team]` | Miembros de cada team, leídos del servidor (🟢 = conectado) |
| `/team jugador nombre` | En qué team está un jugador |
| `/team ranking categoría [región]` | Qué team va ganando: suma de sus miembros (medallas, capturas, horas…) y el MVP de cada uno |

Los teams existen **solo en Minecraft** (nombre con su color y prefijo en el chat y en la lista de jugadores). Los crea un admin con el nombre que quiera (`/admin team-crear`) y mete a cada jugador con `/admin team`.

### Regalos (roles de `DISCORD_ROLES_REGALOS` y admins)

**`/dar pokemon jugador especie […]`**: solo jugador y especie son obligatorios. Las listas de especies, habilidades, movimientos, formas y Poké Balls salen del Cobblemon de tu servidor, así que solo se puede elegir lo que existe.

| Opción | Valores |
|---|---|
| `nivel` | 1-100 |
| `shiny`, `gigamax`, `intercambiable` | Sí / No |
| `genero` | Macho, Hembra, Sin género |
| `naturaleza` | Las 25 (en español) |
| `habilidad` | Las que puede tener esa especie (incluidas ocultas) |
| `ivs` | 0-31. `max`, un número para todos, seis números `PS,Atq,Def,AtEsp,DefEsp,Vel` o `atk=31 spe=31` |
| `evs` | 0-252, máximo 510 en total. Mismos formatos que los IVs |
| `movimientos` | Hasta 4 de los que aprende la especie, separados por comas |
| `objeto` | Objeto equipado (ej. `cobblemon:leftovers`) |
| `pokeball`, `mote`, `amistad` (0-255), `forma` (Alola, Galar…), `tera`, `dinamax` (0-10), `tamano` | — |
| `extra` | Cualquier otra propiedad de Cobblemon a mano (`clave=valor`) |

**`/dar dinero jugador cantidad`**: CobbleDollars.

**`/dar objeto jugador mod objeto [cantidad]`**: primero eliges el mod (Minecraft, Cobblemon…), luego buscas el objeto por su nombre. La lista sale de los mods del servidor.

### Admins (`DISCORD_ROLES_ADMIN` y administradores de Discord)

| Comando | Qué hace |
|---|---|
| `/admin expulsar` · `banear` · `desbanear` · `baneados` | Moderación (con motivo opcional) |
| `/admin whitelist` | Añadir, quitar, ver, activar o desactivar la whitelist |
| `/admin op` | Dar operador (nivel 4: todo, incluido `/stop`) o quitarlo |
| `/admin operadores` | Quién es operador, con qué nivel, y qué permite cada nivel: **0** jugador normal · **1** construir en el spawn protegido · **2** trucos (`/gamemode`, `/give`, `/tp`, `/pokegive`…) · **3** moderador (+ `/kick`, `/ban`, `/whitelist`, `/op`) · **4** todo. Los niveles 1-3 solo se pueden poner editando `ops.json` con el servidor parado (el bot no escribe en los archivos del servidor) |
| `/admin modo` | Supervivencia, creativo, aventura o espectador |
| `/admin teletransportar` | Lleva a un jugador hasta otro |
| `/admin hora` · `clima` · `dificultad` | Mundo |
| `/admin curar` | Cura los Pokémon del equipo de un jugador |
| `/admin experiencia` | Da (o quita, en negativo) niveles o puntos |
| `/admin guardar` | Guarda el mundo |
| `/admin reiniciar [aviso]` | Avisa a los jugadores (chat y título en pantalla), espera y reinicia. **Pide confirmación con un botón.** Requiere que algo vuelva a arrancar el servidor al pararse (en Docker: `restart: unless-stopped`) |
| `/admin mensaje` | Mensajes en el juego (ver abajo) |
| `/admin team-crear nombre [color] [prefijo] [fuego_amigo]` | Crea un team con el **nombre que quieras**. El prefijo por defecto es `[Nombre]` (pon `ninguno` para no tener) |
| `/admin team-editar team …` | Cambia el nombre, el color, el prefijo o el fuego amigo de un team |
| `/admin team-borrar team` | Borra un team (pide confirmación; sus miembros se quedan sin team) |
| `/admin team jugador team` | Mete a un jugador en un team, lo cambia o lo saca (`Sin team`). No hace falta que esté conectado |
| `/comando texto` | Cualquier comando de la lista `permitidos` de `config.toml`, con sugerencias |
| `/actualizar` | Guarda el mundo y recarga los datos ya |
| `/diagnostico jugador` | Qué encuentra el bot de un jugador + IDs de sus logros |

**`/admin mensaje`**: el texto puede salir en el **chat**, como **título grande en el centro** de la pantalla (con subtítulo) o **encima de la hotbar**; a todos o a un jugador. Tiene opciones de color, negrita, cursiva, subrayado, tachado y "ofuscado", y además admite códigos dentro del texto:

| Código | Efecto | Código | Efecto |
|---|---|---|---|
| `&0`–`&9`, `&a`–`&f` | Los 16 colores (`&6` dorado, `&c` rojo, `&a` verde, `&b` turquesa…) | `&l` | Negrita |
| `&#FF00AA` | Cualquier color exacto | `&o` | Cursiva |
| `&n` | Subrayado | `&m` | Tachado |
| `&k` | Ofuscado | `&r` | Quitar formato |

Ejemplo: `&6&lEvento &rde captura en &bRuta 1 &ra las 22:00`

Si algún comando falla de forma inesperada, el bot responde con un **código de error** (ej. `B4AB63`); con `./bot.sh error B4AB63` ves el detalle.

---

## 7. Configuración completa

La configuración está en dos archivos:

- **`.env`**: claves y datos de **tu** instalación (token, IDs, contraseña RCON, rutas). **Nunca se comparte ni se sube a ningún sitio.** Todas las variables están explicadas en [.env.ejemplo](.env.ejemplo).
- **`config.toml`**: ajuste fino **sin claves** (se puede compartir). Plantilla comentada: [config.ejemplo.toml](config.ejemplo.toml).

Si una variable está en el `.env`, manda sobre `config.toml`. Tras cambiar cualquiera de los dos: `./bot.sh reiniciar`.

| Sección de `config.toml` | Para qué |
|---|---|
| `[archivos]` | Carpeta del mundo, cada cuánto se releen los datos (`cache_segundos`) y cada cuánto guarda el mundo y recarga solo (`actualizar_minutos`, 0 = nunca) |
| `[monitor]` | Cada cuánto comprueba el servidor y si avisa de quién entra y sale (`avisar_entradas`) |
| `[mensajes]` | Nombre y color con el que firma el bot en el chat del juego (en vez de "[Rcon]") |
| `[comandos]` | Comandos permitidos en `/comando` (no pongas `"*"`) |
| `[cobbleverse]` | Símbolo y carpeta del dinero, y qué logros cuentan como medallas y ligas |
| `[rankings_cobblemon]` | Rankings extra de `/top` a partir de estadísticas de Cobblemon |

### Cómo funcionan los teams

- **Manda Minecraft.** Los teams y sus miembros viven en el mundo, como cualquier team del juego. No se definen en ningún archivo: se crean desde Discord (`/admin team-crear`) o dentro del juego con `/team add` (operadores), y el bot enseña todos.
- El bot los **lee** del mundo (`world/data/scoreboard.dat`, solo lectura) y pregunta los miembros en directo al servidor. Lo que se haga en Discord o en el juego se ve en los dos sitios.
- Al crear, editar o borrar un team desde Discord, el bot fuerza un guardado del mundo para que el cambio se vea al momento. Un team creado **dentro del juego** aparece en Discord en cuanto el servidor guarda (cada pocos minutos, o con `/actualizar`).
- El bot **no guarda nada** ni necesita roles de Discord.

---

## 8. Adaptarlo a tu mundo o modpack

### Medallas y ligas

COBBLEVERSE da un logro al vencer a cada líder de gimnasio y a cada campeón. `config.ejemplo.toml` ya trae los de **COBBLEVERSE 1.7.42**: 32 líderes (8 por región: Kanto, Johto, Hoenn y Sinnoh) como medallas y 4 campeones como ligas.

Si usas otra versión u otro modpack:
1. Consigue una medalla en el juego y usa `/actualizar`.
2. `/diagnostico TuNombre` → descarga el `.txt` con los IDs de tus logros.
3. Pon los IDs que correspondan en `ids_medallas` / `ids_ligas`, o deja esas listas vacías y usa `palabras_medalla` / `palabras_liga` (cuentan los logros cuyo ID contenga esas palabras).

La región de cada medalla se saca del propio ID (si contiene `kanto`, `johto`…).

### Rankings de Cobblemon

Las estadísticas de Cobblemon están en `world/stats/<uuid>.json` (claves `cobblemon:…`). `/stats` las enseña todas. Si un ranking de `/top` sale vacío, busca la clave real ahí y corrígela en `[rankings_cobblemon]`. Para añadir un ranking nuevo basta una línea: `nombre = "cobblemon:clave"`.

### Datos que van con retraso

El servidor escribe las estadísticas en disco al guardar el mundo o cuando un jugador sale. El bot fuerza un guardado cada `actualizar_minutos` (solo si hay alguien conectado) y con `/actualizar`.

---

## 9. Mantenimiento

| Orden | Qué hace |
|---|---|
| `./bot.sh actualizar` | Baja la última versión del repositorio (`git pull`) y la pone en marcha |
| `./bot.sh reiniciar` | Reinicia volviendo a leer `.env` y `config.toml` |
| `./bot.sh logs` | Log en directo (Ctrl+C para salir) |
| `./bot.sh error CODIGO` | Detalle de un error que el bot ha dado en Discord |
| `./bot.sh estado` · `parar` · `arrancar` | Lo obvio |

Las pruebas automáticas (no necesitan Discord ni servidor):

```bash
python -X utf8 tests/test_bot.py
```

---

## 10. Problemas frecuentes

| Síntoma | Causa probable y solución |
|---|---|
| No aparece ningún comando | Falta `DISCORD_GUILD_ID` o el bot no está en el servidor (invítalo con la URL del paso 2.2). Pulsa Ctrl+R en Discord |
| Los comandos de admin no le salen a un rol | Solo los ve quien tiene *Gestionar servidor*: dale acceso en *Integraciones* ([paso 5](#5-primeros-pasos-en-discord)) |
| "No tienes permiso" | Tu rol no está en `DISCORD_ROLES_ADMIN` / `DISCORD_ROLES_REGALOS` |
| `/estado` dice que no responde | RCON: revisa `enable-rcon`, `RCON_PASSWORD` (igual en los dos lados), `RCON_HOST` (en Docker, el nombre del contenedor) y `RED_SERVIDOR` |
| "No encuentro a jugador" | Nunca ha entrado al servidor, o `MC_DATOS` / `MC_CARPETA_MUNDO` no apuntan bien. `/diagnostico` te dice qué encuentra |
| Fichas vacías o con 0 horas | El servidor aún no ha guardado: `/actualizar` |
| Medallas a 0 teniéndolas | Los IDs no cuadran con tu versión ([apartado 8](#medallas-y-ligas)) |
| No llegan avisos al canal | El bot no puede escribir ahí (Editar canal → Permisos) o el ID del canal está mal. Tras cambiar el `.env`: `./bot.sh reiniciar` |
| Error de permisos al leer archivos | `UID_GID` no es el dueño de la carpeta del servidor (`ls -ln`) |
| "La aplicación no ha respondido" | Mira `./bot.sh logs`. Si el bot dio un código de error, `./bot.sh error CODIGO` |

---

## 11. Seguridad

- El **token** y la **contraseña RCON** solo van en el `.env` (permisos `600`). `.gitignore` y `.dockerignore` impiden que acaben en git o en la imagen de Docker. Si el token se filtra: **Reset Token** en el portal de Discord.
- El bot monta los archivos del servidor en **solo lectura**: nunca modifica el mundo directamente, todo lo hace mediante comandos del servidor.
- `/comando` solo admite los comandos de `permitidos`; `stop` no está (para eso está `/admin reiniciar`, con confirmación).
- Con `DISCORD_CANAL_REGISTRO` configurado queda constancia de cada `/dar`, `/admin` y `/comando`: quién, cuándo y qué.
- No publiques el puerto RCON (25575) fuera de la máquina.
