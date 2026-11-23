# CobbleBOT

Bot de Discord para servidores de Minecraft con **Cobblemon**, pensado para el modpack **COBBLEVERSE**.

- 📊 **Para todos:** fichas de jugador, equipos Pokémon, medallas por región y rankings.
- 🛡️ **Teams:** los de **FTB Teams** (se crean en el juego), copiados a roles de Discord y con `[etiqueta]` de color en el chat, y ranking entre teams.
- 🏅 **Ligas:** cuando alguien vence al campeón de una región, el bot lo anuncia en un canal y le da un rol.
- 🎁 **Para tu equipo:** regala Pokémon (con IVs, EVs, movimientos…), dinero y objetos desde Discord.
- 🔧 **Para los admins:** gestiona el servidor (expulsar, banear, whitelist, operadores, clima, mensajes en pantalla, reinicios…) sin entrar al juego. Lo peligroso pide confirmación con un botón.
- 🟢 **Avisos:** el bot avisa en un canal cuando el servidor se cae, se reinicia y **en cuanto se puede volver a entrar**.
- 🔒 **Seguro:** lee los archivos del servidor en **solo lectura** y todo lo hace con comandos del propio servidor.

> Esta es la rama **`teams-discord`**: los teams son los de **FTB Teams** (mod necesario en el servidor), copiados a roles de Discord; cada jugador vincula su cuenta.
> En la rama **`teams-minecraft`** los teams existen solo en el juego y los gestiona un admin.

---

## Índice

1. [Requisitos](#1-requisitos)
2. [Crear el bot en Discord](#2-crear-el-bot-en-discord)
3. [Preparar el servidor de Minecraft](#3-preparar-el-servidor-de-minecraft)
4. [Instalar y arrancar el bot](#4-instalar-y-arrancar-el-bot)
5. [Primeros pasos en Discord](#5-primeros-pasos-en-discord)
6. [Comandos](#6-comandos)
7. [Teams y ligas: cómo funcionan](#7-teams-y-ligas-cómo-funcionan)
8. [Configuración completa](#8-configuración-completa)
9. [Adaptarlo a tu mundo o modpack](#9-adaptarlo-a-tu-mundo-o-modpack)
10. [Mantenimiento](#10-mantenimiento)
11. [Problemas frecuentes](#11-problemas-frecuentes)
12. [Seguridad](#12-seguridad)

---

## 1. Requisitos

- Un **servidor de Minecraft con Cobblemon** (Fabric). Probado con **COBBLEVERSE 1.7.42** (Minecraft 1.21.1, Cobblemon 1.7). Con otros modpacks de Cobblemon funciona casi todo; las medallas y ligas hay que ajustarlas ([apartado 9](#9-adaptarlo-a-tu-mundo-o-modpack)).
- **RCON activado** en el servidor (el bot lo usa para mandar comandos y saber quién está conectado).
- **El bot va en la misma máquina que el servidor**: lee los archivos del mundo (estadísticas, Pokémon, medallas) directamente del disco, en solo lectura.
- **Docker** con Docker Compose (recomendado; en Windows, dentro de WSL) **o** Python 3.11+.
- **git**, para descargar el bot y actualizarlo.
- Un servidor de Discord donde tengas permiso de **Gestionar servidor**.

Opcional: **CobbleDollars** (para el dinero en fichas, rankings y `/dar dinero`). Si no lo tienes, el dinero sale vacío.

### Instalar Docker (Ubuntu / Debian / WSL)

```bash
curl -fsSL https://get.docker.com | sudo sh     # instala Docker y Docker Compose
sudo usermod -aG docker $USER                     # para usar Docker sin sudo
# Cierra la terminal y vuelve a abrirla (o reinicia WSL con "wsl --shutdown" desde Windows)
docker compose version                            # tiene que responder con una versión
```

---

## 2. Crear el bot en Discord

### 2.1 La aplicación y el token

1. Entra en https://discord.com/developers/applications → **New Application** → ponle nombre (será el nombre del bot).
2. En **General Information**, copia el **Application ID** (lo usarás para la invitación).
3. En **Bot**:
   - **Reset Token** → copia el token. **Es la contraseña del bot**: va en el `.env` y en ningún otro sitio. Si se filtra, vuelve a darle a **Reset Token**.
   - **Privileged Gateway Intents**: no hace falta activar ninguno.
   - Si no quieres que otros puedan invitar tu bot a sus servidores, desactiva **Public Bot**.

### 2.2 Invitar el bot a tu servidor

Abre esta dirección cambiando `TU_APPLICATION_ID`:

```
https://discord.com/oauth2/authorize?client_id=TU_APPLICATION_ID&scope=bot+applications.commands&permissions=268487680
```

Elige tu servidor y acepta. Permisos que pide: **Ver canales, Enviar mensajes, Insertar enlaces, Adjuntar archivos** (para el `.txt` de `/diagnostico`) y **Gestionar roles** (para los roles de teams y ligas).

Después, en **Ajustes del servidor → Roles**, **arrastra el rol del bot por encima** de los roles de teams y ligas: Discord solo deja a un bot poner roles que estén por debajo del suyo.

### 2.3 Sacar los IDs que necesita el bot

En Discord: **Ajustes de usuario → Avanzado → Modo desarrollador** (actívalo). Ahora con **clic derecho → Copiar ID** puedes copiar:

| Qué | Dónde | Variable del `.env` |
|---|---|---|
| ID del **servidor** | Clic derecho en el icono del servidor | `DISCORD_GUILD_ID` (obligatorio: hace que los comandos aparezcan al instante) |
| ID de un **canal de estado** | Clic derecho en el canal | `DISCORD_CANAL_ESTADO`: avisos de servidor caído / reiniciando / online |
| ID de un **canal de registro** (privado) | Clic derecho en el canal | `DISCORD_CANAL_REGISTRO`: queda apuntado quién usa `/dar`, `/admin` y `/comando` |
| ID de los **roles** de admin y de regalos | Ajustes del servidor → Roles → clic derecho | `DISCORD_ROLES_ADMIN`, `DISCORD_ROLES_REGALOS` |

Los **roles de teams y ligas** no van en el `.env`: los de teams los crea el bot solo (uno por team de FTB) y los de ligas se eligen con `/admin ligas`.

**Canales recomendados:**
- `#estado-del-servidor`: público, de solo lectura para los miembros. Ahí avisa el bot.
- `#ligas`: público. Ahí se anuncian las ligas completadas.
- `#comandos-admin`: donde los admins usan `/admin` y `/dar`. Las respuestas quedan a la vista como registro.
- `#registro-bot`: privado, solo admins. Copia de cada acción que no se puede borrar desde el canal público.

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

## 4. Instalar y arrancar el bot

### 4.1 Descargar y configurar

```bash
git clone https://github.com/<usuario>/cobble-bot.git     # si el repositorio es privado, pide acceso al dueño
cd cobble-bot
git checkout teams-discord        # o teams-minecraft (ver arriba la diferencia)
cp .env.ejemplo .env
cp config.ejemplo.toml config.toml
chmod 600 .env                    # que solo tu usuario pueda leer las claves
nano .env                         # rellénalo (ver abajo)
```

Lo mínimo que hay que rellenar en el `.env` (todas las opciones están explicadas en [.env.ejemplo](.env.ejemplo)):

| Variable | Qué poner |
|---|---|
| `DISCORD_TOKEN` | El token del [paso 2.1](#21-la-aplicación-y-el-token) |
| `DISCORD_GUILD_ID` | ID de tu servidor de Discord |
| `DISCORD_CANAL_ESTADO` | ID del canal de avisos de estado (0 = sin avisos) |
| `DISCORD_CANAL_REGISTRO` | ID del canal privado de registro (0 = sin registro privado) |
| `DISCORD_ROLES_ADMIN` / `DISCORD_ROLES_REGALOS` | IDs de roles separados por comas (los administradores de Discord siempre pueden) |
| `DISCORD_RESPUESTAS_PUBLICAS` | `true` (por defecto): las respuestas de los comandos las ve todo el canal. `false`: solo quien los usa |
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
UID_GID=1000:1000                                # id -u : id -g del dueño de esa carpeta (vacío = tu usuario)
```

Con el servidor de Minecraft **ya encendido**:

```bash
./bot.sh comprobar   # revisa Docker, el .env, la carpeta del servidor y la red; te dice qué falta
./bot.sh arrancar    # construye la imagen y arranca el bot
./bot.sh logs        # tiene que salir "Conectado como TuBot#1234" (Ctrl+C para salir del log)
```

El bot se vuelve a encender solo si se cae o si se reinicia la máquina (`restart: unless-stopped`), siempre que Docker arranque con el sistema.

### 4.3 Opción B: servidor sin Docker, bot en Docker

En el `.env`:
```env
BOT_SIN_RED_DOCKER=1             # el bot usa la red de la máquina
RCON_HOST=127.0.0.1
MC_DATOS=/ruta/a/la/carpeta/del/servidor
UID_GID=1000:1000                # el usuario que ejecuta el servidor
```

```bash
./bot.sh comprobar
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

En **Windows** basta con doble clic en `iniciar.bat` (crea el entorno la primera vez). Para que arranque solo en Linux sin Docker tienes una plantilla de servicio en [cobblebot.service](cobblebot.service). Lo que el bot recuerda (vínculos, teams, ligas) se guarda en la carpeta `datos/`.

> 💡 Para **probar el bot sin servidor** puedes apuntar `MC_CARPETA_SERVIDOR` a una instancia del juego (la carpeta `minecraft` de Prism/CurseForge) y `MC_CARPETA_MUNDO` a `saves/NombreDelMundo`. Las fichas, equipos, medallas y rankings funcionarán; lo que necesita RCON (`/estado`, `/admin`, `/dar`, teams…) no.

> ⚠️ No tengas **dos copias del bot encendidas con el mismo token** (por ejemplo Docker y `iniciar.bat`): responderían dos veces a cada comando.

---

## 5. Primeros pasos en Discord

1. Escribe `/help`: te enseña los comandos que **tú** puedes usar. Si no aparece ningún comando, pulsa **Ctrl+R** en Discord.
2. `/estado`: debería decir que el servidor está online. Si dice que no responde, revisa RCON ([problemas frecuentes](#11-problemas-frecuentes)).
3. Entra al servidor, juega un poco y usa `/actualizar` (guarda el mundo y recarga). Luego `/stats TuNombre`.
4. **Teams:** se crean en el juego con FTB Teams. Cada jugador vincula su Discord con `/team vincular` → `/team confirmar` y recibe el rol de su team.
5. **Ligas:** elige el canal de anuncios con `/admin ligas canal:#ligas` y un rol por región con `/admin ligas region:Kanto rol:@Campeón de Kanto`.
6. `/diagnostico TuNombre` (admins): qué archivos encuentra el bot del jugador, con un `.txt` de los IDs de sus logros. Útil para ajustar medallas.
7. **Quién ve qué:** los comandos `/admin`, `/dar`, `/comando`, `/actualizar` y `/diagnostico` solo los ve quien tiene *Gestionar servidor*. Para enseñárselos a otro rol sin darle ese permiso: **Ajustes del servidor → Integraciones → (tu bot) → elige el comando → añade el rol**. Ese rol, además, tiene que estar en `DISCORD_ROLES_ADMIN` o `DISCORD_ROLES_REGALOS` para que el bot le deje usarlos.

---

## 6. Comandos

**Jugadores:** al escribir un jugador salen sugerencias, con los conectados arriba (🟢). Se usa siempre el nombre de Minecraft a secas, aunque en el juego lleve la `[etiqueta]` de su team. Para consultar datos puede estar desconectado (se usa lo último que guardó el servidor); para recibir regalos o acciones de admin tiene que estar **conectado**.

**Quién ve las respuestas:** las ve todo el canal, para que quede registro de lo que se hace. Los errores de uso, "no tienes permiso" y `/help` solo los ve quien usa el comando. Se cambia con `DISCORD_RESPUESTAS_PUBLICAS`.

### Para todos

| Comando | Qué hace |
|---|---|
| `/help` | Los comandos que puedes usar tú |
| `/estado` | Si el servidor está online y cuánta gente hay |
| `/jugadores` | Quién está conectado |
| `/stats jugador` | Ficha: horas, dinero, medallas y ligas por región, equipo, colección y estadísticas de Cobblemon |
| `/equipo jugador` | Los 6 Pokémon del equipo (mote, nivel, sexo, shiny) y resumen del PC |
| `/medallas jugador [región]` | Medallas por región y ligas conseguidas |
| `/top categoría [región] [cantidad]` | Ranking. Categorías: horas, dinero, medallas, ligas, pokemon, shinies, especies, nivel_max, logros, muertes, distancia, mobs, capturas, shinies_capturados, combates, evoluciones, huevos (y las que añadas). La región filtra medallas y ligas |

### Teams (para todos)

Los teams son los de **FTB Teams**: se crean, se invita a gente, se echa y se cambia el nombre o el color **dentro del juego** (botón de FTB Teams en el inventario). El bot solo los copia a Discord y al chat.

| Comando | Qué hace |
|---|---|
| `/team vincular jugador` | Une tu Discord con tu jugador de Minecraft. **Tienes que estar conectado**: te llega un **código al chat del juego** (haz clic en él para copiarlo). Los jugadores que ya están vinculados no salen en la lista |
| `/team confirmar codigo` | Termina la vinculación pegando ese código (caduca en 5 minutos y sirve una sola vez) |
| `/team info [team]` | Miembros de cada team (con un team: líder, rol, color, etiqueta y fuego amigo) |
| `/team actualizar` | Guarda el mundo y copia ya a Discord los cambios de teams hechos en el juego (una vez por minuto) |
| `/team panel [team]` | Panel con botones para el dueño y los oficiales del team de FTB: nombre y color (se cambian también en FTB Teams), etiqueta `[ ]`, emoji, fuego amigo, invitar, cancelar invitaciones, echar miembros y disolver (solo el dueño). Los admins pueden abrir el de cualquier team con `team:`, echar a cualquiera menos al dueño y **meter a alguien sin invitación** (si no está conectado, entra al conectarse) |
| `/team crear nombre [color]` | Crea un team de FTB Teams desde Discord. Si no estás conectado al servidor, se crea en cuanto entras |
| `/team invitar usuario` | Dueño y oficiales: invita a alguien de Discord. Le llega por privado (o en el canal) con botones Aceptar/Rechazar; caduca en 7 días |
| `/team invitaciones` | Tus invitaciones (aceptar o rechazar) y lo que tienes pendiente. Si aceptas sin estar conectado, entras al team al conectarte |
| `/team salir` | Sal de tu team (si no estás conectado, al entrar al servidor) |
| `/team ranking categoría [región]` | Qué team va ganando: suma de sus miembros (medallas, capturas, horas…) y el MVP de cada uno |

En el juego, `/teammsg mensaje` (o `/tm mensaje`) habla solo con los de tu team.

### Regalos (roles de `DISCORD_ROLES_REGALOS` y admins)

**`/dar pokemon jugador especie […]`**: solo jugador y especie son obligatorios. Las listas de especies, habilidades, movimientos, formas y Poké Balls salen del Cobblemon de tu servidor, así que solo se puede elegir lo que existe.

| Opción | Valores |
|---|---|
| `nivel` | 1-100 |
| `shiny`, `alfa`, `gigamax`, `intercambiable` | Sí / No. `alfa` = Pokémon Alfa de Cobblemon 1.8+ (más grande, más fuerte y con un movimiento MT extra) |
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

Los marcados con ⚠️ **piden confirmar con un botón** antes de hacerse. Solo puede pulsarlo quien lanzó el comando, y el mensaje queda en el canal con "Confirmado" o "Cancelado".

| Comando | Qué hace |
|---|---|
| `/admin expulsar` · `desbanear` · `baneados` | Moderación (con motivo opcional) |
| `/admin banear` ⚠️ | Banea a un jugador |
| `/admin whitelist` | Añadir, quitar, ver o activar la whitelist. **Desactivarla** ⚠️ |
| `/admin op` | **Dar** operador ⚠️ (nivel 4: todo, incluido `/stop`) o quitarlo |
| `/admin operadores` | Quién es operador, con qué nivel, y qué permite cada nivel: **0** jugador normal · **1** construir en el spawn protegido · **2** trucos (`/gamemode`, `/give`, `/tp`, `/pokegive`…) · **3** moderador (+ `/kick`, `/ban`, `/whitelist`, `/op`) · **4** todo. Los niveles 1-3 solo se pueden poner editando `ops.json` con el servidor parado (el bot no escribe en los archivos del servidor) |
| `/admin modo` | Supervivencia, creativo, aventura o espectador |
| `/admin teletransportar` | Lleva a un jugador hasta otro |
| `/admin hora` · `clima` · `dificultad` | Mundo |
| `/admin curar` | Cura los Pokémon del equipo de un jugador |
| `/admin experiencia` | Da (o quita, en negativo) niveles o puntos |
| `/admin guardar` | Guarda el mundo |
| `/admin reiniciar [aviso]` ⚠️ | Avisa a los jugadores (chat y título en pantalla), espera y reinicia. Requiere que algo vuelva a arrancar el servidor al pararse (en Docker: `restart: unless-stopped`) |
| `/admin mensaje` | Mensajes en el juego (ver abajo) |
| `/admin team-editar team [etiqueta] [emoji] [fuego_amigo] [rol]` | Lo que FTB no tiene: el texto entre `[ ]` del chat (por defecto el nombre del team), el emoji de Discord, si se pueden hacer daño entre ellos y usar otro rol de Discord en vez del que crea el bot. El nombre y el color se cambian en el juego |
| `/admin vincular usuario jugador` · `/admin desvincular usuario` | Vincular a mano (sin código) o deshacer un vínculo |
| `/admin ligas [canal] [region] [rol] [quitar_rol]` | Canal donde se anuncia cada liga completada y rol que recibe quien la completa (uno por región). Sin opciones, enseña cómo está |
| `/comando texto` | Cualquier comando de la lista `permitidos` de `config.toml`, con sugerencias |
| `/actualizar` | Guarda el mundo y recarga los datos ya (también lanza los anuncios de ligas pendientes) |
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

## 7. Teams y ligas: cómo funcionan

### Teams

- **Manda el juego (FTB Teams).** El bot lee los teams del mundo (`<mundo>/ftbteams/party/`) cada 5 minutos, o al momento con `/team actualizar`. FTB escribe esos archivos cuando el servidor guarda el mundo, así que un cambio puede tardar unos minutos en verse.
- **Un rol de Discord por team**, con su nombre y color de FTB; si cambian en el juego, el rol cambia también. Los miembros con el Discord vinculado reciben el rol de su team y pierden los de los demás: si alguien se pone o se quita a mano un rol de team, el bot lo deshace. Cuando un team desaparece, su rol se borra (si lo creó el bot).
- **En el juego** cada miembro sale como `[Team] Nombre` en el chat, la lista de jugadores (Tab) y encima de la cabeza (corchetes en gris, el nombre del team en su color). Para eso el bot usa un team normal de Minecraft por cada team de FTB, llamado `ftb_` + el principio de su id. No los toques con `/team`: el bot los rehace.
- Los invitados y aliados de FTB no cuentan como miembros.
- Si el bot no tiene permiso para gestionar roles, la `[etiqueta]` del juego funciona igual y los roles se ponen en cuanto se arreglen los permisos.

### Ligas

- El bot compara las ligas de cada jugador cada vez que relee los datos (cada `actualizar_minutos`, por defecto 5, si hay alguien conectado; o al momento con `/actualizar`).
- Cada liga nueva se **anuncia una sola vez** en el canal de `/admin ligas` (si no hay, en el de avisos), con la cara del jugador y una mención si tiene el Discord vinculado.
- Si la liga tiene rol, el jugador **vinculado** lo recibe. Quien se vincula más tarde recibe los roles de las ligas que ya tenga.
- La primera vez que se activa no anuncia las ligas antiguas (pero sí da sus roles).
- ⚠️ Dar una liga a mano con `/advancement grant` ejecuta la recompensa de COBBLEVERSE (por ejemplo, completar Kanto **desbloquea Johto para todo el servidor**) y no se puede deshacer.

### Lo que guarda el bot

Quién es quién (vínculos Discord ↔ Minecraft), el rol de Discord de cada team y qué ligas se han anunciado van en la carpeta **`datos/`** del bot (`teams.json`, `ligas.json`), **no** en el servidor. Es la única carpeta en la que escribe. Contiene IDs de Discord y nombres de jugadores: no la compartas (está en `.gitignore`).

---

## 8. Configuración completa

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

---

## 9. Adaptarlo a tu mundo o modpack

### Medallas y ligas

COBBLEVERSE da un logro al vencer a cada líder de gimnasio y a cada campeón. `config.ejemplo.toml` ya trae los de **COBBLEVERSE 1.7.42**: 32 líderes (8 por región: Kanto, Johto, Hoenn y Sinnoh) como medallas y 4 campeones como ligas.

Si usas otra versión u otro modpack:
1. Consigue una medalla en el juego y usa `/actualizar`.
2. `/diagnostico TuNombre` → descarga el `.txt` con los IDs de tus logros.
3. Pon los IDs que correspondan en `ids_medallas` / `ids_ligas`, o deja esas listas vacías y usa `palabras_medalla` / `palabras_liga` (cuentan los logros cuyo ID contenga esas palabras).

La región de cada medalla y liga se saca del propio ID (si contiene `kanto`, `johto`…).

### Rankings de Cobblemon

Las estadísticas de Cobblemon están en `world/stats/<uuid>.json` (claves `cobblemon:…`). `/stats` las enseña todas. Si un ranking de `/top` sale vacío, busca la clave real ahí y corrígela en `[rankings_cobblemon]`. Para añadir un ranking nuevo basta una línea: `nombre = "cobblemon:clave"`.

### Datos que van con retraso

El servidor escribe las estadísticas en disco al guardar el mundo o cuando un jugador sale. El bot fuerza un guardado cada `actualizar_minutos` (solo si hay alguien conectado) y con `/actualizar`.

---

## 10. Mantenimiento

| Orden | Qué hace |
|---|---|
| `./bot.sh comprobar` | Revisa que todo está listo (Docker, `.env`, carpeta del servidor, red, carpeta `datos`) y dice qué falta |
| `./bot.sh arrancar` | Construye la imagen y arranca el bot |
| `./bot.sh actualizar` | Baja la última versión del repositorio (`git pull`) y la pone en marcha |
| `./bot.sh reiniciar` | Reinicia volviendo a leer `.env` y `config.toml` |
| `./bot.sh logs` | Log en directo (Ctrl+C para salir) |
| `./bot.sh error CODIGO` | Detalle de un error que el bot ha dado en Discord |
| `./bot.sh pruebas` | Pruebas automáticas (no necesitan Discord ni servidor) |
| `./bot.sh estado` · `parar` | Si está en marcha / pararlo |

El bot va en su propio proyecto de Docker (`cobble-bot`): arrancarlo, pararlo o actualizarlo **nunca reinicia el servidor de Minecraft**.

Para **cambiar de rama** (por ejemplo a `teams-minecraft`): `git checkout teams-minecraft && ./bot.sh arrancar`.

---

## 11. Problemas frecuentes

| Síntoma | Causa probable y solución |
|---|---|
| `./bot.sh` dice "Permission denied" | `chmod +x bot.sh` |
| "No puedes usar Docker con tu usuario" | `sudo usermod -aG docker $USER` y vuelve a abrir la terminal |
| "No existe la red de Docker" | Arranca primero el servidor de Minecraft y pon en `RED_SERVIDOR` una de `docker network ls` |
| "No puedes escribir en ./datos" | La creó Docker como root: `sudo chown -R $(id -u):$(id -g) datos` |
| No aparece ningún comando | Falta `DISCORD_GUILD_ID` o el bot no está en el servidor (invítalo con la URL del paso 2.2). Pulsa Ctrl+R en Discord |
| Los comandos de admin no le salen a un rol | Solo los ve quien tiene *Gestionar servidor*: dale acceso en *Integraciones* ([paso 5](#5-primeros-pasos-en-discord)) |
| "No tienes permiso" | Tu rol no está en `DISCORD_ROLES_ADMIN` / `DISCORD_ROLES_REGALOS` |
| `/estado` dice que no responde | RCON: revisa `enable-rcon`, `RCON_PASSWORD` (igual en los dos lados), `RCON_HOST` (en Docker, el nombre del contenedor) y `RED_SERVIDOR` |
| "No encuentro a jugador" | Nunca ha entrado al servidor, o `MC_DATOS` / `MC_CARPETA_MUNDO` no apuntan bien. `/diagnostico` te dice qué encuentra |
| Fichas vacías o con 0 horas | El servidor aún no ha guardado: `/actualizar` |
| Medallas a 0 teniéndolas | Los IDs no cuadran con tu versión ([apartado 9](#medallas-y-ligas)) |
| "No he podido cambiar el rol de Discord" | Al bot le falta **Gestionar roles** o su rol está **por debajo** de los roles de teams/ligas (Ajustes del servidor → Roles) |
| `/team vincular` dice que no estás conectado | Tienes que estar dentro del servidor para recibir el código en el chat |
| El código de vinculación no vale | Caduca a los 5 minutos y sirve una vez; si el bot se reinicia entre medias, pide otro |
| No sale el anuncio de una liga | Tarda hasta 5 minutos (o usa `/actualizar`). Mira `/admin ligas` y que el bot pueda escribir en ese canal |
| No llegan avisos al canal | El bot no puede escribir ahí (Editar canal → Permisos) o el ID del canal está mal. Tras cambiar el `.env`: `./bot.sh reiniciar` |
| Error de permisos al leer archivos | `UID_GID` no es el dueño de la carpeta del servidor (`ls -ln`) |
| "La aplicación no ha respondido" | Mira `./bot.sh logs`. Si el bot dio un código de error, `./bot.sh error CODIGO` |

---

## 12. Seguridad

- El **token** y la **contraseña RCON** solo van en el `.env` (permisos `600`). `.gitignore` y `.dockerignore` impiden que acaben en git o en la imagen de Docker. Si el token se filtra: **Reset Token** en el portal de Discord.
- El bot monta los archivos del servidor en **solo lectura**: nunca modifica el mundo directamente, todo lo hace mediante comandos del servidor. Solo escribe en su carpeta `datos/`.
- El contenedor va **endurecido**: sistema de archivos de solo lectura (salvo `/tmp` en memoria y `datos/`), sin privilegios de Linux (`cap_drop: ALL`), `no-new-privileges` y límite de procesos. Corre con tu usuario, no como root.
- Banear, dar operador, desactivar la whitelist y reiniciar **piden confirmación** con un botón que solo puede pulsar quien lanzó el comando.
- `/comando` solo admite los comandos de `permitidos`; `stop` no está (para eso está `/admin reiniciar`, con confirmación).
- Las respuestas públicas y `DISCORD_CANAL_REGISTRO` dejan constancia de cada `/dar`, `/admin` y `/comando`: quién, cuándo y qué.
- La vinculación necesita un código que llega al chat del juego del propio jugador: nadie puede quedarse con la cuenta de otro.
- No publiques el puerto RCON (25575) fuera de la máquina. Recomendado en Discord: **Ajustes del servidor → Seguridad → requerir 2FA para moderación**.
