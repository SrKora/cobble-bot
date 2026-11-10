import asyncio
import base64
import io
import json
import logging
import os
import re
import sys
import time
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

from . import rankings as rk
from .archivos import crear_fuente
from .cobblemon_datos import (NATURALEZAS, RE_EXTRA, RE_ID, RE_JAR_COBBLEMON, RE_MOTE, TIPOS_TERA,
                              DatosCobblemon, parsear_stats, resumen_stats)
from .entorno import aplicar_entorno, cargar_dotenv
from .objetos_datos import Mod, buscar, catalogo_de_jars
from .texto_mc import COLORES, a_json, componentes, texto_plano
from .perfiles import NOMBRES_COBBLEMON, REGIONES, Perfiles, Perfil, bonito
from .rcon import RconClient, RconError
from .teams import (HEX_COLORES, Registro, Team, cargar_teams, comandos_crear, etiqueta_de, id_para,
                    parsear_miembros)

log = logging.getLogger("cobblebot")
COLOR = 0xE3350D  # rojo Poké Ball
RE_COLORES = re.compile(r"§.")
MEDALLAS_POR_REGION = 8
SKIN_CACHE_SEGUNDOS = 3600
SEGUNDOS_MONITOR_CAIDO = 3     # con el servidor caído se comprueba así de a menudo si ya se puede entrar
# Niveles de operador de Minecraft: qué permite cada uno (cada nivel incluye los anteriores)
NIVELES_OP = {
    0: ("Jugador normal", "sin comandos de operador"),
    1: ("Constructor", "construir y romper en la zona protegida del spawn"),
    2: ("Trucos", "/gamemode, /give, /tp, /time, /weather, /effect, /pokegive, /pokeheal…"),
    3: ("Moderador", "lo del nivel 2 + /kick, /ban, /pardon, /whitelist, /op y /deop"),
    4: ("Dueño", "todo, incluido /stop (apagar el servidor) y /save-off"),
}
RE_NOMBRE_MC = re.compile(r"^[A-Za-z0-9_]{1,16}$")
PALABRAS_FALLO = ("unknown", "invalid", "error", "incorrect", "expected", "not found", "no se ", "desconocid",
                  "could not", "nothing changed", "is not")

# Descripción de los comandos de Minecraft más habituales (para la lista de /comando)
DESCRIPCION_COMANDOS = {
    "list": "Jugadores conectados", "say": "Mensaje a todo el servidor", "tellraw": "Mensaje con formato JSON",
    "msg": "Mensaje privado a un jugador", "tell": "Mensaje privado a un jugador",
    "whitelist": "Gestionar la lista blanca", "kick": "Expulsar a un jugador", "ban": "Banear a un jugador",
    "pardon": "Quitar un ban", "banlist": "Ver los baneados", "op": "Hacer operador", "deop": "Quitar operador",
    "gamemode": "Cambiar modo de juego", "tp": "Teletransportar", "teleport": "Teletransportar",
    "give": "Dar objetos", "pokegive": "Dar un Pokémon (al que lo ejecuta)", "pokegiveother": "Dar un Pokémon a otro",
    "pokeheal": "Curar los Pokémon de un jugador", "time": "Cambiar la hora", "weather": "Cambiar el clima",
    "difficulty": "Cambiar la dificultad", "experience": "Dar o quitar experiencia", "xp": "Dar o quitar experiencia",
    "save-all": "Guardar el mundo", "effect": "Dar efectos de poción", "clear": "Vaciar inventario",
    "kill": "Matar entidades", "gamerule": "Cambiar reglas del juego", "spawnpoint": "Punto de aparición",
    "setworldspawn": "Punto de aparición del mundo", "cobbledollars": "Dinero de CobbleDollars",
    "summon": "Invocar una entidad", "enchant": "Encantar el objeto en mano", "locate": "Buscar estructuras",
}
RE_OBJETO = re.compile(r"^([a-z0-9_.-]+:)?[a-z0-9_./-]+$")


def url_avatar(p: Perfil) -> str:
    """Cara del jugador. Si se conoce el hash de su skin se usa ese (siempre actual);
    si no, por UUID (mc-heads a veces guarda una skin vieja o la de Steve)."""
    return f"https://mc-heads.net/avatar/{getattr(p, 'skin', None) or p.uuid}/64"
OPCIONES_REGION = [app_commands.Choice(name=r.capitalize(), value=r) for r in REGIONES]


def limpiar(texto: str) -> str:
    return RE_COLORES.sub("", texto).strip()


def esc(t: str) -> str:
    return discord.utils.escape_markdown(t)


def parsear_list(respuesta: str) -> tuple[int, int, list[str]]:
    """'There are 2 of a max of 20 players online: Ana, Luis' -> (2, 20, ['Ana', 'Luis'])"""
    t = limpiar(respuesta)
    nums = re.findall(r"\d+", t.split(":")[0])
    online = int(nums[0]) if nums else 0
    maximo = int(nums[1]) if len(nums) > 1 else 0
    nombres = []
    if ":" in t:
        nombres = [n.strip() for n in t.split(":", 1)[1].split(",") if n.strip()]
    return online, maximo, nombres


class CobbleBot(discord.Client):
    def __init__(self, cfg: dict):
        super().__init__(intents=discord.Intents.default())
        self.cfg = cfg
        self.tree = app_commands.CommandTree(self)

        r = cfg.get("rcon", {})
        self.rcon = RconClient(r.get("host", "127.0.0.1"), int(r.get("puerto", 25575)), r.get("password", ""))

        a = cfg.get("archivos", {})
        cv = cfg.get("cobbleverse", {})
        self.fuente = crear_fuente(a)
        self.perfiles = Perfiles(self.fuente, a, cv) if self.fuente else None
        self.simbolo_dinero = cv.get("simbolo_dinero", "₽")
        self.categorias = rk.construir(cfg.get("rankings_cobblemon", {}), self.simbolo_dinero)

        d = cfg.get("discord", {})
        self.guild_id = int(d["guild_id"]) if d.get("guild_id") else None
        self.canal_avisos = int(d["canal_avisos"]) if d.get("canal_avisos") else None
        self.canal_registro = int(d["canal_registro"]) if d.get("canal_registro") else None
        # Canal para "servidor caído / vuelve a estar online / reiniciando" (si no, el de avisos)
        self.canal_estado = int(d["canal_estado"]) if d.get("canal_estado") else self.canal_avisos
        self.roles_admin = {int(x) for x in d.get("roles_admin", [])}
        # Roles que pueden usar /dar (Pokémon, dinero, objetos). Los admins siempre pueden.
        self.roles_regalos = {int(x) for x in d.get("roles_regalos", [])}
        self._datos_cobblemon: DatosCobblemon | None = None
        self._objetos: dict[str, Mod] | None = None
        self.permitidos = [c.lower() for c in cfg.get("comandos", {}).get("permitidos", [])]
        # Nombre con el que firma el bot los mensajes dentro del juego (en vez de "[Rcon]")
        m = cfg.get("mensajes", {})
        self.nombre_juego = str(m.get("nombre", "CobbleBOT"))[:32]
        self.color_juego = str(m.get("color", "gold"))

        self._online_previo: set[str] | None = None
        self._servidor_activo: bool | None = None
        self._caido_desde: float | None = None
        # Avisar en Discord de quién entra y sale del servidor (los avisos de caída/reinicio van siempre)
        self.avisar_entradas = bool(cfg.get("monitor", {}).get("avisar_entradas", False))
        # Teams: los creados desde Discord (y los de config.toml); todo lo que recuerda va en la carpeta de datos del bot
        self.teams_config: dict[str, Team] = cargar_teams(cfg)
        self.registro = Registro(os.environ.get("BOT_DATOS", "datos"))
        self._reinicio_por: str | None = None     # quién pidió el reinicio con /admin reiniciar
        self._visto_caido = False                 # tras pedir un reinicio, hay que verlo caer antes de darlo por levantado
        self.intervalo_monitor = int(cfg.get("monitor", {}).get("intervalo_segundos", 30))
        self._fallos = 0
        self._skins: dict[str, tuple[float, str | None]] = {}

    # ---------------------------------------------------------------- arranque
    async def setup_hook(self):
        registrar_comandos(self)
        self.tree.on_error = self.error_de_comando
        if self.guild_id:
            g = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()
        self.monitor.change_interval(seconds=self.intervalo_monitor)
        self.monitor.start()
        minutos = float(self.cfg.get("archivos", {}).get("actualizar_minutos", 5))
        if self.perfiles and minutos > 0:
            self.auto_actualizar.change_interval(minutes=minutos)
            self.auto_actualizar.start()
        self.sincronizar_teams_loop.start()

    async def close(self):
        if self.fuente:
            await self.fuente.cerrar()
        await super().close()

    async def on_ready(self):
        log.info("Conectado como %s", self.user)
        # Se cargan ya los datos pesados (especies, objetos) para que el primer /dar no tarde
        asyncio.create_task(self._precargar())

    # ---------------------------------------------------------------- teams
    @property
    def teams(self) -> dict[str, Team]:
        """Todos los teams: los de config.toml y los creados desde Discord, ordenados por nombre."""
        todos = {**self.teams_config, **self.registro.teams}
        return dict(sorted(todos.items(), key=lambda kv: kv[1].nombre.lower()))

    def roles_teams(self, guild: discord.Guild) -> dict[str, discord.Role | None]:
        """Rol de Discord de cada team (por ID o por nombre exacto)."""
        res = {}
        for tid, t in self.teams.items():
            rol = guild.get_role(int(t.rol)) if t.rol.isdigit() else discord.utils.get(guild.roles, name=t.rol)
            res[tid] = rol
        return res

    def puede_gestionar_roles(self, guild: discord.Guild, roles: dict) -> str | None:
        """None si el bot puede poner y quitar los roles de los teams; si no, qué le falta."""
        yo = guild.me
        if not yo.guild_permissions.manage_roles:
            return "me falta el permiso **Gestionar roles**"
        altos = [r.name for r in roles.values() if r and r >= yo.top_role]
        if altos:
            return f"mi rol tiene que estar **por encima** de {', '.join(altos)} (Ajustes del servidor → Roles)"
        return None

    async def preparar_teams_mc(self):
        """Crea los teams en Minecraft con su color y su [etiqueta] (si ya existen, solo los actualiza)."""
        try:
            for t in self.teams.values():
                for c in comandos_crear(t):
                    await self.rcon.comando(c)
        except RconError:
            pass

    async def cambiar_team(self, miembro: discord.Member, team: str | None) -> list[str]:
        """Pone (o quita, con None) el team de alguien en Discord, en Minecraft y en el registro."""
        problemas = []
        roles = self.roles_teams(miembro.guild)
        quitar = [r for tid, r in roles.items() if r and r in miembro.roles and tid != team]
        poner = roles.get(team) if team else None
        falta = self.puede_gestionar_roles(miembro.guild, roles)
        rol_ok = False
        if falta:
            problemas.append(f"No he podido cambiar el rol de Discord: {falta}. Lo pondré solo en cuanto pueda.")
        else:
            try:
                if quitar:
                    await miembro.remove_roles(*quitar, reason="CobbleBOT: cambio de team")
                if poner and poner not in miembro.roles:
                    await miembro.add_roles(poner, reason="CobbleBOT: cambio de team")
                rol_ok = True
            except discord.HTTPException as e:
                problemas.append(f"Discord no me dejó cambiar el rol ({e.text or e.status}).")
        if team and not poner:
            problemas.append(f"No encuentro el rol de Discord «{self.teams[team].rol}».")
        jugador = self.registro.jugador_de(miembro.id)
        if jugador:
            try:
                await self.rcon.comando(f"team join {team} {jugador}" if team else f"team leave {jugador}")
            except RconError:
                problemas.append("El servidor de Minecraft no responde: se aplicará en el juego cuando vuelva.")
        async with self.registro.lock:
            self.registro.poner_team(miembro.id, team)
            self.registro.marcar_pendiente(miembro.id, not rol_ok)
        log.info("Team de %s (%s) -> %s", miembro, jugador, team)
        return problemas

    async def sincronizar_teams(self):
        """Manda Discord: copia al registro y a Minecraft los roles de team que tenga cada uno."""
        guild = self.get_guild(self.guild_id) if self.guild_id else None
        if not guild or not self.teams:
            return
        roles = self.roles_teams(guild)
        if not self.puede_gestionar_roles(guild, roles) and any(roles.values()):
            # Solo se copian los roles de Discord si el bot puede gestionarlos (si no, se perderían cambios)
            for did in list(self.registro.vinculos):
                try:
                    m = guild.get_member(int(did)) or await guild.fetch_member(int(did))
                except discord.HTTPException:
                    continue
                if did in self.registro.pendientes:
                    # El rol no se pudo poner en su momento: ahora se pone el del registro (no al revés)
                    await self.cambiar_team(m, self.registro.team_de(m.id))
                    continue
                con_rol = [tid for tid, r in roles.items() if r and r in m.roles]
                actual = self.registro.team_de(m.id)
                nuevo = (actual if actual in con_rol else con_rol[0]) if con_rol else None
                if nuevo != actual:
                    async with self.registro.lock:
                        self.registro.poner_team(m.id, nuevo)
                    log.info("Team de %s cambiado en Discord: %s -> %s", m, actual, nuevo)
        try:
            for tid in self.teams:
                en_mc = {n.lower(): n for n in parsear_miembros(await self.rcon.comando(f"team list {tid}"))}
                deseados = {n.lower(): n for _, n in self.registro.miembros(tid)}
                for n in deseados.keys() - en_mc.keys():
                    await self.rcon.comando(f"team join {tid} {deseados[n]}")
                for n in en_mc.keys() - deseados.keys():
                    if self.registro.discord_de(en_mc[n]):     # solo se toca a jugadores vinculados
                        await self.rcon.comando(f"team leave {en_mc[n]}")
        except RconError:
            pass

    @tasks.loop(minutes=5)
    async def sincronizar_teams_loop(self):
        await self.sincronizar_teams()

    @sincronizar_teams_loop.before_loop
    async def _antes_teams(self):
        await self.wait_until_ready()
        await self.preparar_teams_mc()

    @sincronizar_teams_loop.error
    async def _error_teams(self, error):
        log.exception("Error sincronizando teams", exc_info=error)

    async def _precargar(self):
        try:
            await self.datos_cobblemon()
            await self.objetos()
        except Exception:
            log.exception("Error precargando datos")

    # ---------------------------------------------------------------- errores
    async def responder_error(self, inter: discord.Interaction, texto: str):
        """Responde al usuario aunque la interacción ya tuviera respuesta o 'pensando…'."""
        try:
            if inter.response.is_done():
                await inter.followup.send(texto, ephemeral=True)
            else:
                await inter.response.send_message(texto, ephemeral=True)
        except discord.HTTPException:
            pass    # la interacción caducó (más de 15 min) o Discord no la acepta: solo queda el log

    async def error_de_comando(self, inter: discord.Interaction, error: app_commands.AppCommandError):
        """Cualquier fallo de un comando: mensaje entendible al usuario y detalle completo en el log."""
        nombre = f"/{inter.command.qualified_name}" if inter.command else "comando"
        original = getattr(error, "original", error)

        if isinstance(error, (app_commands.MissingPermissions, app_commands.CheckFailure)):
            await self.responder_error(inter, "🚫 No tienes permiso para usar este comando.")
            return
        if isinstance(error, app_commands.CommandOnCooldown):
            await self.responder_error(inter, f"⏳ Espera {error.retry_after:.0f} s antes de volver a usarlo.")
            return
        if isinstance(error, app_commands.TransformerError):
            await self.responder_error(inter, f"❌ El valor `{error.value}` no es válido para esa opción.")
            return
        if isinstance(original, RconError):
            log.warning("%s: sin conexión con el servidor (%s)", nombre, original)
            await self.responder_error(inter, "🔴 No pude hablar con el servidor de Minecraft: puede estar "
                                              "apagado o reiniciándose. Prueba en un momento (mira `/estado`).")
            return
        if isinstance(original, discord.Forbidden):
            log.warning("%s: Discord no me deja hacerlo (%s)", nombre, original)
            await self.responder_error(inter, "🚫 Discord no me deja hacer eso: me falta algún permiso en este "
                                              "canal o servidor.")
            return

        codigo = uuid.uuid4().hex[:6].upper()
        opciones = getattr(inter, "data", {}) or {}
        log.error("Error %s en %s (usuario %s, datos %s)", codigo, nombre, inter.user, opciones.get("options"),
                  exc_info=original)
        await self.responder_error(
            inter, f"⚠️ Algo ha fallado al ejecutar `{nombre}`. Ya está apuntado en el registro del bot "
                   f"(código **{codigo}**). Si se repite, pásale ese código al admin.")

    # ---------------------------------------------------------------- utilidades
    async def enviar(self, canal_id: int | None, **kwargs):
        if not canal_id:
            return
        canal = self.get_channel(canal_id) or await self.fetch_channel(canal_id)
        await canal.send(**kwargs)

    def es_admin(self, miembro) -> bool:
        if not isinstance(miembro, discord.Member):
            return False
        if miembro.guild_permissions.administrator:
            return True
        return any(r.id in self.roles_admin for r in miembro.roles)

    def puede_regalar(self, miembro) -> bool:
        if self.es_admin(miembro):
            return True
        return isinstance(miembro, discord.Member) and any(r.id in self.roles_regalos for r in miembro.roles)

    async def objetos(self) -> dict[str, Mod]:
        """Catálogo de objetos por mod (namespace -> Mod), sacado de los .jar del servidor."""
        if self._objetos is not None:
            return self._objetos
        mods: dict[str, Mod] = {}
        try:
            if self.fuente:
                jars = [n for n in await self.fuente.listar("mods") if n.endswith(".jar")]
                rutas = [self.fuente.ruta_local(f"mods/{n}") for n in jars]
                if any(r is None for r in rutas):   # archivos remotos: hay que descargarlos
                    rutas = [io.BytesIO(b) for n in jars if (b := await self.fuente.leer_bytes(f"mods/{n}"))]
                vanilla = None
                base = self.fuente.ruta_local("versions")
                if base and base.is_dir():
                    vanilla = next(iter(sorted(base.glob("*/server-*.jar"), reverse=True)), None)
                mods = await asyncio.to_thread(catalogo_de_jars, rutas, vanilla)
        except Exception:
            log.exception("No pude leer el catálogo de objetos")
        self._objetos = mods
        log.info("Catálogo de objetos: %d mods, %d objetos", len(mods), sum(len(m.objetos) for m in mods.values()))
        return mods

    async def datos_cobblemon(self) -> DatosCobblemon:
        """Especies, habilidades, movimientos, formas y Poké Balls, sacados del .jar de Cobblemon del servidor."""
        if self._datos_cobblemon is not None:
            return self._datos_cobblemon
        datos = DatosCobblemon()
        try:
            if self.fuente:
                jar = next((n for n in await self.fuente.listar("mods") if RE_JAR_COBBLEMON.match(n)), None)
                contenido = await self.fuente.leer_bytes(f"mods/{jar}") if jar else None
                if contenido:
                    datos = await asyncio.to_thread(DatosCobblemon.desde_jar, contenido)
        except Exception:
            log.exception("No pude leer los datos de Cobblemon")
        self._datos_cobblemon = datos
        log.info("Datos de Cobblemon: %d especies, %d Poké Balls", len(datos.especies), len(datos.balls))
        return datos

    async def especies(self) -> dict[str, str]:
        """id -> nombre de todas las especies."""
        return {i: e.nombre for i, e in (await self.datos_cobblemon()).especies.items()}

    def cmd_mensaje(self, texto: str, destino: str = "@a", privado: bool = False, estilo: dict | None = None,
                    firma: bool = True) -> str:
        """tellraw firmado con el nombre del bot: '[CobbleBOT] texto'. El texto admite códigos & (colores…)."""
        estilo = estilo or {"color": "light_purple" if privado else "white", "italic": privado}
        cuerpo = componentes(texto, estilo)
        if firma:
            etiqueta = f"[{self.nombre_juego}{' → tú' if privado else ''}] "
            cuerpo = ["", {"text": etiqueta, "color": self.color_juego, "bold": True}, cuerpo]
        return f"tellraw {destino} {a_json(cuerpo)}"

    def cmds_titulo(self, titulo: str, subtitulo: str = "", destino: str = "@a", estilo: dict | None = None,
                    segundos: float = 3.0) -> list[str]:
        """Comandos para mostrar un título grande en el centro de la pantalla (con subtítulo opcional)."""
        estilo = estilo or {"color": "gold", "bold": True}
        cmds = [f"title {destino} times 10 {int(segundos * 20)} 20"]
        if subtitulo:
            cmds.append(f"title {destino} subtitle {a_json(componentes(subtitulo, {'color': 'white'}))}")
        cmds.append(f"title {destino} title {a_json(componentes(titulo or ' ', estilo))}")
        return cmds

    async def operadores(self) -> list[dict]:
        """Operadores y su nivel, leídos de ops.json (solo lectura)."""
        datos = (await self.fuente.leer_json("ops.json") if self.fuente else None) or []
        return [o for o in datos if isinstance(o, dict) and o.get("name")]

    def comando_permitido(self, cmd: str) -> bool:
        if "*" in self.permitidos:
            return True
        return cmd.strip().lstrip("/").split(" ", 1)[0].lower() in self.permitidos

    async def hash_skin(self, uuid: str) -> str | None:
        """Hash de la skin actual del jugador según Mojang (con caché de una hora)."""
        t, h = self._skins.get(uuid, (0.0, None))
        if time.time() - t < SKIN_CACHE_SEGUNDOS:
            return h
        h = None
        try:
            url = f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid.replace('-', '')}"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                async with s.get(url) as r:
                    if r.status == 200:
                        for prop in (await r.json()).get("properties", []):
                            if prop.get("name") == "textures":
                                tex = json.loads(base64.b64decode(prop["value"]))
                                skin = tex.get("textures", {}).get("SKIN", {}).get("url", "")
                                h = skin.rsplit("/", 1)[-1] or None
        except Exception as e:
            log.warning("No pude obtener la skin de %s: %s", uuid, e)
        self._skins[uuid] = (time.time(), h)
        return h

    def fmt_dinero(self, v: float | None) -> str:
        return "—" if v is None else f"{rk.f_int(v)} {self.simbolo_dinero}"

    # ---------------------------------------------------------------- monitor
    def _modo_caido(self):
        """Mientras el servidor está caído se comprueba cada pocos segundos, para avisar en cuanto se pueda entrar."""
        self.monitor.change_interval(seconds=SEGUNDOS_MONITOR_CAIDO)

    async def _teams_tras_reinicio(self):
        await self.preparar_teams_mc()
        await self.sincronizar_teams()

    def marcar_reinicio(self, quien: str):
        """Reinicio pedido desde Discord: no hace falta esperar a los fallos para darlo por caído."""
        self._reinicio_por = quien
        self._servidor_activo = False
        self._visto_caido = False
        self._online_previo = None
        self._caido_desde = time.time()
        self._modo_caido()

    @tasks.loop(seconds=30)
    async def monitor(self):
        try:
            online, maximo, nombres = parsear_list(await self.rcon.comando("list"))
            self._fallos = 0
        except RconError:
            self._fallos += 1
            self._visto_caido = True
            if self._servidor_activo is None:
                # El bot acaba de arrancar y el servidor aún no está: se avisará cuando se levante
                self._servidor_activo = False
                self._caido_desde = time.time()
                self._modo_caido()
            elif self._servidor_activo and self._fallos >= 3:
                self._servidor_activo = False
                self._online_previo = None
                self._caido_desde = time.time() - 2 * self.intervalo_monitor
                self._modo_caido()
                await self.enviar(self.canal_estado, embed=discord.Embed(
                    title="🔴 El servidor no responde",
                    description="Puede estar apagado o reiniciándose. Avisaré aquí en cuanto se pueda entrar.",
                    color=0x8B0000, timestamp=datetime.now(timezone.utc)))
            return

        if self._servidor_activo is False:
            if not self._visto_caido:
                return      # se ha pedido el reinicio pero todavía no se ha parado: aún no cuenta como "de vuelta"
            # RCON solo arranca cuando el servidor ha terminado de cargar ("Done"): ya se puede entrar
            e = discord.Embed(title="🟢 ¡El servidor vuelve a estar online!",
                              description="Ya podéis entrar.", color=0x2E8B57, timestamp=datetime.now(timezone.utc))
            if self._caido_desde:
                segundos = int(time.time() - self._caido_desde)
                e.add_field(name="⏱️ Tiempo caído",
                            value=f"{segundos // 60} min {segundos % 60} s" if segundos >= 60 else f"{segundos} s")
            e.add_field(name="👥 Jugadores", value=f"{online}/{maximo}")
            if self._reinicio_por:
                e.set_footer(text=f"Reinicio pedido por {self._reinicio_por}")
            await self.enviar(self.canal_estado, embed=e)
            self.monitor.change_interval(seconds=self.intervalo_monitor)
            log.info("Servidor de vuelta: avisado en Discord")
            if self.teams:
                asyncio.create_task(self._teams_tras_reinicio())
        self._servidor_activo = True
        self._caido_desde = None
        self._reinicio_por = None

        actuales = set(nombres)
        if self._online_previo is not None and self.avisar_entradas:
            for n in sorted(actuales - self._online_previo):
                await self.enviar(self.canal_avisos, content=f"➡️ **{esc(n)}** ha entrado al servidor")
            for n in sorted(self._online_previo - actuales):
                await self.enviar(self.canal_avisos, content=f"⬅️ **{esc(n)}** ha salido del servidor")
        self._online_previo = actuales

    @monitor.before_loop
    async def _antes_monitor(self):
        await self.wait_until_ready()

    @monitor.error
    async def _error_monitor(self, error):
        log.exception("Error en el monitor", exc_info=error)

    # ---------------------------------------------------------------- actualización
    async def refrescar(self) -> int:
        """Pide al servidor que guarde (stats, Pokémon, dinero) y vuelve a leer los datos."""
        for cmd in ("save-all", "cobbledollars leaderboard update"):
            try:
                await self.rcon.comando(cmd)
            except RconError:
                pass
        await asyncio.sleep(3)
        return len(await self.perfiles.todos(forzar=True))

    @tasks.loop(minutes=5)
    async def auto_actualizar(self):
        # Sin nadie conectado no cambia nada: no hace falta guardar ni releer
        if self._servidor_activo and not self._online_previo:
            return
        n = await self.refrescar()
        log.info("Actualización automática: %d jugadores", n)

    @auto_actualizar.before_loop
    async def _antes_auto(self):
        await self.wait_until_ready()

    @auto_actualizar.error
    async def _error_auto(self, error):
        log.exception("Error en la actualización automática", exc_info=error)


# =================================================================== embeds
def embed_perfil(bot: CobbleBot, p: Perfil) -> discord.Embed:
    e = discord.Embed(title=f"📊 Ficha de {p.nombre}", color=COLOR)
    e.set_thumbnail(url=url_avatar(p))

    e.add_field(name="⏱️ Horas", value=rk.f_dec(p.horas, " h"))
    e.add_field(name="💰 Dinero", value=bot.fmt_dinero(p.dinero))
    e.add_field(name="🏅 Medallas", value=str(len(p.medallas)))

    # Medallas y ligas por región
    regiones = sorted({m.region for m in p.medallas + p.ligas if m.region}, key=REGIONES.index)
    if regiones:
        lineas = []
        for r in regiones:
            n = len(p.medallas_de(r))
            liga = " · 🏆 Liga completada" if p.ligas_de(r) else ""
            lineas.append(f"**{r.capitalize()}:** {n}/{MEDALLAS_POR_REGION}{liga}")
        e.add_field(name="🗺️ Progreso por región", value="\n".join(lineas), inline=False)
    elif p.ligas:
        e.add_field(name="🏆 Ligas", value=", ".join(l.nombre for l in p.ligas), inline=False)

    # Equipo
    if p.equipo:
        e.add_field(name="🎒 Equipo actual", value="\n".join(x.linea() for x in p.equipo[:6])[:1024], inline=False)
    else:
        e.add_field(name="🎒 Equipo actual", value="Sin datos", inline=False)

    # Colección
    col = (f"**Total:** {rk.f_int(len(p.todos_pokemon))} · **Shinies:** {p.shinies} · "
           f"**Especies:** {p.especies} · **Nivel máx.:** {p.nivel_max}")
    e.add_field(name="📦 Colección (equipo + PC)", value=col, inline=False)

    # Stats de Cobblemon + generales
    lineas = [f"**{NOMBRES_COBBLEMON.get(k, bonito(k))}:** {rk.f_int(v)}" for k, v in sorted(p.cobblemon.items())]
    lineas += [
        f"**Muertes:** {rk.f_int(p.valor('minecraft:deaths'))}",
        f"**Distancia:** {rk.f_dec(p.distancia_km, ' km')}",
        f"**Logros COBBLEVERSE:** {p.logros}",
    ]
    e.add_field(name="📈 Estadísticas", value="\n".join(lineas)[:1024], inline=False)
    e.set_footer(text="Los datos se actualizan cuando el servidor guarda el mundo")
    return e


def embed_equipo(p: Perfil) -> discord.Embed:
    e = discord.Embed(title=f"🎒 Equipo de {p.nombre}", color=COLOR)
    e.set_thumbnail(url=url_avatar(p))
    if not p.equipo:
        e.description = "No encuentro su equipo (puede que no tenga Pokémon o que el servidor aún no haya guardado)."
        return e
    for i, x in enumerate(p.equipo[:6], 1):
        nombre = f"{i}. {x.mote or x.nombre}" + (" ✨" if x.shiny else "")
        detalle = x.nombre + (f" ({bonito(x.forma)})" if x.forma and x.forma.lower() != "normal" else "")
        sexo = {"MALE": "♂", "FEMALE": "♀"}.get((x.genero or "").upper(), "")
        e.add_field(name=nombre, value=f"{detalle} {sexo}\nNivel {x.nivel}")
    e.set_footer(text=f"En el PC: {len(p.pc)} Pokémon · {sum(x.shiny for x in p.pc)} shinies")
    return e


def embed_medallas(p: Perfil, region: str | None) -> discord.Embed:
    titulo = f"🏅 Medallas de {p.nombre}" + (f" — {region.capitalize()}" if region else "")
    e = discord.Embed(title=titulo, color=0xF1C40F)
    e.set_thumbnail(url=url_avatar(p))
    medallas, ligas = p.medallas_de(region), p.ligas_de(region)
    if not medallas and not ligas:
        e.description = "Todavía no tiene medallas" + (f" de {region.capitalize()}." if region else ".")
        return e
    grupos: dict[str, list[str]] = {}
    for m in medallas:
        grupos.setdefault(m.region or "otras", []).append(m.nombre)
    orden = sorted(grupos, key=lambda r: REGIONES.index(r) if r in REGIONES else 99)
    for r in orden:
        liga = " 🏆" if r in REGIONES and p.ligas_de(r) else ""
        e.add_field(name=f"{r.capitalize()} ({len(grupos[r])}/{MEDALLAS_POR_REGION}){liga}",
                    value="\n".join(f"• {n}" for n in grupos[r])[:1024])
    if ligas:
        e.add_field(name="🏆 Ligas / trofeos", value="\n".join(f"• {l.nombre}" for l in ligas)[:1024], inline=False)
    return e


def embed_ayuda(bot: CobbleBot, admin: bool, regalar: bool = False) -> discord.Embed:
    e = discord.Embed(
        title="📖 Comandos de CobbleBOT",
        description="Al escribir el jugador te salen sugerencias (🟢 = conectado ahora).",
        color=COLOR)
    e.add_field(name="🖥️ Servidor", inline=False, value=(
        "`/estado` — si el servidor está online y cuánta gente hay\n"
        "`/jugadores` — quién está conectado ahora mismo"))
    e.add_field(name="👤 Jugadores", inline=False, value=(
        "`/stats jugador` — ficha completa: horas, dinero, medallas, equipo, colección…\n"
        "`/equipo jugador` — los 6 Pokémon del equipo y resumen del PC\n"
        "`/medallas jugador [región]` — medallas por región y ligas conseguidas"))
    cats = ", ".join(f"`{k}`" for k in bot.categorias)
    e.add_field(name="🏆 Rankings", inline=False, value=(
        "`/top categoría [región] [cantidad]` — ranking de jugadores. "
        "La región solo cuenta para medallas y ligas.\n"
        f"Categorías: {cats}")[:1024])
    e.add_field(name="🛡️ Teams", inline=False, value=(
        "`/team vincular jugador` — une tu cuenta de Discord con la de Minecraft (te llega un código al juego)\n"
        "`/team unirse team` — elige tu team (solo la primera vez; para cambiar, pídeselo a un admin)\n"
        "`/team info [team]` — miembros de cada team · `/team ranking categoría` — competición entre teams"))
    if regalar:
        e.add_field(name="🎁 Regalos", inline=False, value=(
            "`/dar pokemon jugador especie […]` — da un Pokémon. Opcional: nivel, shiny, sexo, naturaleza, "
            "habilidad, IVs, EVs, movimientos, objeto, Poké Ball, mote, amistad, forma, teratipo, Dinamax, "
            "Gigamax, tamaño…\n"
            "`/dar dinero jugador cantidad` — da CobbleDollars\n"
            "`/dar objeto jugador mod objeto [cantidad]` — da objetos: elige el mod y busca el objeto por nombre\n"
            "El jugador tiene que estar conectado. Todo queda registrado."))
    if admin:
        e.add_field(name="🔧 Admins", inline=False, value=(
            "`/admin …` — gestión del servidor: expulsar, banear, whitelist, operadores (con niveles), modo de juego, teletransporte, "
            "hora, clima, dificultad, curar, experiencia, guardar y reiniciar\n"
            "`/admin mensaje` — mensajes en el juego (chat, título o encima de la hotbar) con colores: "
            "`&6` dorado, `&c` rojo, `&a` verde, `&l` negrita…\n"
            "`/comando texto` — cualquier comando permitido (con lista de sugerencias)\n"
            "`/admin team-crear` · `team-editar` · `team-borrar` — teams con el nombre, color, [etiqueta] y rol que quieras\n"
            "`/admin team`, `/admin vincular`, `/admin desvincular` — asignar teams y gestionar vínculos\n"
            "`/actualizar` — guarda el mundo y recarga los datos al momento\n"
            "`/diagnostico jugador` — qué archivos y logros encuentra el bot (para ajustar la config)"))
    e.set_footer(text="Los datos se actualizan cuando el servidor guarda el mundo")
    return e


# =================================================================== comandos
def registrar_comandos(bot: CobbleBot):
    tree = bot.tree

    async def ac_jugador(inter: discord.Interaction, actual: str):
        # Primero los conectados ahora (según el monitor, sin esperar a RCON), después el resto.
        conectados = sorted(bot._online_previo or (), key=str.lower)
        conocidos: list[str] = []
        if bot.perfiles:
            try:
                conocidos = await bot.perfiles.nombres()
            except Exception:
                log.exception("Error leyendo jugadores para el autocompletado")
        en_linea = {n.lower() for n in conectados}
        a = actual.lower()
        opciones = [app_commands.Choice(name=f"🟢 {n} (conectado)", value=n) for n in conectados if a in n.lower()]
        opciones += [app_commands.Choice(name=n, value=n) for n in conocidos
                     if a in n.lower() and n.lower() not in en_linea]
        return opciones[:25]

    async def ac_comando(inter: discord.Interaction, actual: str):
        texto = actual.lstrip("/")
        if " " in texto:   # ya ha elegido el comando: se deja lo que escribe tal cual
            return [app_commands.Choice(name=f"/{texto}"[:100], value=texto[:100])]
        lista = sorted(DESCRIPCION_COMANDOS) if "*" in bot.permitidos else bot.permitidos
        a = texto.lower()
        return [app_commands.Choice(name=f"/{c} — {DESCRIPCION_COMANDOS.get(c, 'comando del servidor')}"[:100],
                                    value=f"{c} ")
                for c in lista if c.startswith(a) or (a and a in c)][:25]

    async def ac_categoria(inter: discord.Interaction, actual: str):
        a = actual.lower()
        return [app_commands.Choice(name=c.titulo + (" (filtrable por región)" if c.por_region else ""), value=k)
                for k, c in bot.categorias.items() if a in c.titulo.lower() or a in k][:25]

    async def perfil_o_error(inter: discord.Interaction, jugador: str) -> Perfil | None:
        if not bot.perfiles:
            await inter.followup.send("Las estadísticas no están configuradas (sección [archivos]).")
            return None
        p = await bot.perfiles.buscar(jugador)
        if not p:
            await inter.followup.send(f"No encuentro a **{esc(jugador)}**. ¿Ha entrado alguna vez al servidor?")
            return None
        p.skin = await bot.hash_skin(p.uuid)
        return p

    # ---- /help
    @tree.command(name="help", description="Lista de comandos del bot y cómo usarlos")
    async def ayuda(inter: discord.Interaction):
        await inter.response.send_message(embed=embed_ayuda(bot, bot.es_admin(inter.user), bot.puede_regalar(inter.user)), ephemeral=True)

    # ---- /estado
    @tree.command(name="estado", description="¿Está el servidor online y cuánta gente hay?")
    async def estado(inter: discord.Interaction):
        await inter.response.defer()
        try:
            online, maximo, nombres = parsear_list(await bot.rcon.comando("list"))
        except RconError:
            await inter.followup.send(embed=discord.Embed(
                title="🔴 Servidor offline", description="No responde ahora mismo.", color=0x8B0000))
            return
        e = discord.Embed(title="🟢 Servidor online", color=0x2E8B57)
        e.add_field(name="Jugadores", value=f"{online}/{maximo}")
        if nombres:
            e.add_field(name="Conectados", value=", ".join(esc(n) for n in nombres), inline=False)
        await inter.followup.send(embed=e)

    # ---- /jugadores
    @tree.command(name="jugadores", description="Quién está conectado ahora mismo")
    async def jugadores(inter: discord.Interaction):
        await inter.response.defer()
        try:
            online, maximo, nombres = parsear_list(await bot.rcon.comando("list"))
        except RconError as e:
            await inter.followup.send(f"No pude contactar con el servidor: {e}")
            return
        if not nombres:
            await inter.followup.send(f"No hay nadie conectado (0/{maximo}).")
            return
        await inter.followup.send(embed=discord.Embed(
            title=f"Conectados ({online}/{maximo})",
            description="\n".join(f"• {esc(n)}" for n in nombres), color=COLOR))

    # ---- /stats
    @tree.command(name="stats", description="Ficha completa de un jugador: medallas, ligas, equipo, dinero…")
    @app_commands.describe(jugador="Nombre del jugador en Minecraft")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def stats(inter: discord.Interaction, jugador: str):
        await inter.response.defer()
        if p := await perfil_o_error(inter, jugador):
            await inter.followup.send(embed=embed_perfil(bot, p))

    # ---- /equipo
    @tree.command(name="equipo", description="Los Pokémon que lleva un jugador en el equipo")
    @app_commands.describe(jugador="Nombre del jugador en Minecraft")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def equipo(inter: discord.Interaction, jugador: str):
        await inter.response.defer()
        if p := await perfil_o_error(inter, jugador):
            await inter.followup.send(embed=embed_equipo(p))

    # ---- /medallas
    @tree.command(name="medallas", description="Medallas y ligas de un jugador")
    @app_commands.describe(jugador="Nombre del jugador en Minecraft", region="Solo una región (opcional)")
    @app_commands.autocomplete(jugador=ac_jugador)
    @app_commands.choices(region=OPCIONES_REGION)
    async def medallas(inter: discord.Interaction, jugador: str, region: app_commands.Choice[str] | None = None):
        await inter.response.defer()
        if p := await perfil_o_error(inter, jugador):
            await inter.followup.send(embed=embed_medallas(p, region.value if region else None))

    # ---- /top
    @tree.command(name="top", description="Ranking de jugadores con los filtros que elijas")
    @app_commands.describe(
        categoria="Qué ranking: horas, medallas, ligas, dinero, shinies, Pokémon…",
        region="Para medallas y ligas: contar solo una región",
        cantidad="Cuántos puestos mostrar (por defecto 10)",
    )
    @app_commands.autocomplete(categoria=ac_categoria)
    @app_commands.choices(region=OPCIONES_REGION)
    async def top(inter: discord.Interaction, categoria: str,
                  region: app_commands.Choice[str] | None = None,
                  cantidad: app_commands.Range[int, 3, 25] = 10):
        if not bot.perfiles:
            await inter.response.send_message("Las estadísticas no están configuradas.", ephemeral=True)
            return
        cat = bot.categorias.get(categoria)
        if not cat:
            await inter.response.send_message("Esa categoría no existe. Elígela de la lista.", ephemeral=True)
            return
        await inter.response.defer()
        reg = region.value if (region and cat.por_region) else None
        orden = sorted(await bot.perfiles.todos(), key=lambda p: cat.valor(p, reg), reverse=True)
        orden = [p for p in orden if cat.valor(p, reg) > 0][:cantidad]
        titulo = cat.titulo + (f" de {reg.capitalize()}" if reg else "")
        if not orden:
            await inter.followup.send(f"Nadie tiene datos de **{titulo}** todavía.")
            return
        medallas_e = ["🥇", "🥈", "🥉"]
        lineas = [f"{medallas_e[i] if i < 3 else f'`{i + 1}.`'} **{esc(p.nombre)}** — {cat.formato(cat.valor(p, reg))}"
                  for i, p in enumerate(orden)]
        e = discord.Embed(title=f"🏆 Top {titulo}", description="\n".join(lineas), color=COLOR)
        if region and not cat.por_region:
            e.set_footer(text="El filtro de región solo se aplica a medallas y ligas.")
        await inter.followup.send(embed=e)

    # ---- /comando (admins)
    @tree.command(name="comando", description="Ejecuta un comando en el servidor (solo admins)")
    @app_commands.describe(comando="Elige un comando de la lista y escribe detrás lo que necesite (ej. whitelist add Pepe)")
    @app_commands.autocomplete(comando=ac_comando)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def comando(inter: discord.Interaction, comando: str):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        if not bot.comando_permitido(comando):
            await inter.response.send_message(
                f"Ese comando no está en la lista de permitidos.\nPermitidos: `{', '.join(bot.permitidos) or '(ninguno)'}`",
                ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        try:
            salida, ok = limpiar(await bot.rcon.comando(comando)) or "(sin respuesta)", True
        except RconError as e:
            salida, ok = str(e), False
        await inter.followup.send(f"`/{comando.lstrip('/')}`\n```\n{salida[:1800]}\n```", ephemeral=True)
        await bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"**{inter.user}** ejecutó `/{comando.lstrip('/')}`" + ("" if ok else f"\n❌ {salida[:500]}"),
            color=0x555555, timestamp=datetime.now(timezone.utc)))

    # ---- /admin (gestión del servidor)
    admin = app_commands.Group(name="admin", description="Gestión del servidor de Minecraft (solo admins)",
                               guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    def texto_seguro(t: str | None, maximo: int = 200) -> str:
        return " ".join((t or "").split())[:maximo]

    async def ejecutar(inter: discord.Interaction, comando: str, que: str, jugador: str | None = None,
                       publico: bool = False):
        """Comprueba que es admin, lanza el comando por RCON, responde y lo deja registrado."""
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return None
        if jugador is not None and not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return None
        await inter.response.defer(ephemeral=not publico)
        try:
            salida = limpiar(await bot.rcon.comando(comando))
        except RconError as e:
            await inter.followup.send(f"No pude contactar con el servidor: {e}", ephemeral=True)
            return None
        bajo = salida.lower()
        if "no player was found" in bajo:
            await inter.followup.send(f"**{esc(jugador)}** no está conectado." if jugador
                                      else "No hay ningún jugador conectado.", ephemeral=True)
            return salida
        fallo = any(p in bajo for p in PALABRAS_FALLO)
        if fallo:
            await inter.followup.send(f"❌ {que}: el servidor dijo\n```\n{salida[:1500]}\n```", ephemeral=True)
        else:
            await inter.followup.send(f"✅ {que}" + (f"\n```\n{salida[:1500]}\n```" if salida else ""),
                                      ephemeral=not publico)
        await bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"🔧 **{inter.user}**: {que}\n`/{comando}`" + (f"\n❌ {salida[:300]}" if fallo else ""),
            color=0x8B0000 if fallo else 0x555555, timestamp=datetime.now(timezone.utc)))
        return salida

    async def ac_baneado(inter: discord.Interaction, actual: str):
        datos = (await bot.fuente.leer_json("banned-players.json") if bot.fuente else None) or []
        return [app_commands.Choice(name=b["name"], value=b["name"]) for b in datos
                if isinstance(b, dict) and b.get("name") and actual.lower() in b["name"].lower()][:25]

    @admin.command(name="expulsar", description="Echa a un jugador del servidor (puede volver a entrar)")
    @app_commands.describe(jugador="Jugador conectado", motivo="Motivo (lo verá el jugador)")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_expulsar(inter: discord.Interaction, jugador: str, motivo: str | None = None):
        m = texto_seguro(motivo)
        await ejecutar(inter, f"kick {jugador} {m}".strip(), f"Expulsado **{esc(jugador)}**" + (f" ({m})" if m else ""),
                       jugador)

    @admin.command(name="banear", description="Banea a un jugador (no podrá volver a entrar)")
    @app_commands.describe(jugador="Jugador", motivo="Motivo (lo verá el jugador)")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_banear(inter: discord.Interaction, jugador: str, motivo: str | None = None):
        m = texto_seguro(motivo)
        await ejecutar(inter, f"ban {jugador} {m}".strip(), f"Baneado **{esc(jugador)}**" + (f" ({m})" if m else ""),
                       jugador)

    @admin.command(name="desbanear", description="Quita el ban a un jugador")
    @app_commands.describe(jugador="Jugador baneado")
    @app_commands.autocomplete(jugador=ac_baneado)
    async def adm_desbanear(inter: discord.Interaction, jugador: str):
        await ejecutar(inter, f"pardon {jugador}", f"Desbaneado **{esc(jugador)}**", jugador)

    @admin.command(name="baneados", description="Lista de jugadores baneados")
    async def adm_baneados(inter: discord.Interaction):
        await ejecutar(inter, "banlist players", "Jugadores baneados")

    @admin.command(name="whitelist", description="Lista blanca: añadir, quitar, activar, desactivar o ver")
    @app_commands.describe(accion="Qué hacer", jugador="Jugador (para añadir o quitar)")
    @app_commands.choices(accion=[app_commands.Choice(name=n, value=v) for n, v in (
        ("Añadir jugador", "add"), ("Quitar jugador", "remove"), ("Ver la lista", "list"),
        ("Activar (solo entran los de la lista)", "on"), ("Desactivar (entra cualquiera)", "off"))])
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_whitelist(inter: discord.Interaction, accion: app_commands.Choice[str], jugador: str | None = None):
        if accion.value in ("add", "remove"):
            if not jugador:
                await inter.response.send_message("Pon el jugador que quieres añadir o quitar.", ephemeral=True)
                return
            await ejecutar(inter, f"whitelist {accion.value} {jugador}", f"Whitelist: {accion.name.lower()} **{esc(jugador)}**",
                           jugador)
        else:
            await ejecutar(inter, f"whitelist {accion.value}", f"Whitelist: {accion.name.lower()}")

    # Minecraft solo tiene comandos para dar el nivel máximo (/op) o quitarlo (/deop); los niveles 1-3
    # requerirían escribir en ops.json y el bot no escribe en los archivos del servidor.
    @admin.command(name="op", description="Da o quita permisos de operador (comandos dentro del juego)")
    @app_commands.describe(jugador="Jugador", accion="Dar o quitar (en /admin operadores tienes qué permite cada nivel)")
    @app_commands.choices(accion=[
        app_commands.Choice(name=f"Dar operador · nivel 4 · {NIVELES_OP[4][0]}: {NIVELES_OP[4][1]}"[:100], value="op"),
        app_commands.Choice(name=f"Quitar operador · nivel 0 · {NIVELES_OP[0][0]}: {NIVELES_OP[0][1]}"[:100],
                            value="deop")])
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_op(inter: discord.Interaction, jugador: str, accion: app_commands.Choice[str]):
        if accion.value == "op":
            await ejecutar(inter, f"op {jugador}",
                           f"**{esc(jugador)}** es operador (nivel 4 · {NIVELES_OP[4][0]}: {NIVELES_OP[4][1]})", jugador)
        else:
            await ejecutar(inter, f"deop {jugador}", f"**{esc(jugador)}** ya no es operador (jugador normal)", jugador)

    @admin.command(name="operadores", description="Quién es operador y con qué nivel")
    async def adm_operadores(inter: discord.Interaction):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        ops = await bot.operadores()
        e = discord.Embed(title="🛡️ Operadores del servidor", color=COLOR)
        e.description = "\n".join(
            f"**{esc(o['name'])}** — nivel {o.get('level', 4)} · {NIVELES_OP.get(o.get('level', 4), ('?', ''))[0]}"
            for o in sorted(ops, key=lambda o: (-o.get("level", 4), o["name"].lower()))) or "No hay operadores."
        e.add_field(name="Qué permite cada nivel (cada uno incluye los anteriores)", inline=False,
                    value="\n".join(f"**{n} · {t}:** {d}" for n, (t, d) in NIVELES_OP.items()))
        e.set_footer(text="Se lee de ops.json. Desde Discord solo se puede dar el nivel 4 o quitarlo; los niveles "
                          "1-3 se cambian editando ops.json con el servidor parado.")
        await inter.response.send_message(embed=e, ephemeral=True)

    @admin.command(name="modo", description="Cambia el modo de juego de un jugador")
    @app_commands.describe(jugador="Jugador conectado", modo="Modo de juego")
    @app_commands.choices(modo=[app_commands.Choice(name=n, value=v) for n, v in (
        ("Supervivencia", "survival"), ("Creativo", "creative"), ("Aventura", "adventure"), ("Espectador", "spectator"))])
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_modo(inter: discord.Interaction, jugador: str, modo: app_commands.Choice[str]):
        await ejecutar(inter, f"gamemode {modo.value} {jugador}", f"**{esc(jugador)}** en modo {modo.name}", jugador)

    @admin.command(name="teletransportar", description="Lleva a un jugador hasta otro jugador")
    @app_commands.describe(jugador="Jugador que se mueve", destino="Jugador al que va")
    @app_commands.autocomplete(jugador=ac_jugador, destino=ac_jugador)
    async def adm_tp(inter: discord.Interaction, jugador: str, destino: str):
        if not RE_NOMBRE_MC.match(destino):
            await inter.response.send_message("El destino no es un nombre válido.", ephemeral=True)
            return
        await ejecutar(inter, f"tp {jugador} {destino}", f"**{esc(jugador)}** → **{esc(destino)}**", jugador)

    @admin.command(name="hora", description="Cambia la hora del día")
    @app_commands.choices(momento=[app_commands.Choice(name=n, value=v) for n, v in (
        ("Amanecer", "day"), ("Mediodía", "noon"), ("Anochecer", "night"), ("Medianoche", "midnight"))])
    async def adm_hora(inter: discord.Interaction, momento: app_commands.Choice[str]):
        await ejecutar(inter, f"time set {momento.value}", f"Hora: {momento.name.lower()}")

    @admin.command(name="clima", description="Cambia el tiempo")
    @app_commands.describe(tipo="Qué tiempo", minutos="Cuánto dura (opcional)")
    @app_commands.choices(tipo=[app_commands.Choice(name=n, value=v) for n, v in (
        ("Despejado ☀️", "clear"), ("Lluvia 🌧️", "rain"), ("Tormenta ⛈️", "thunder"))])
    async def adm_clima(inter: discord.Interaction, tipo: app_commands.Choice[str],
                        minutos: app_commands.Range[int, 1, 1440] | None = None):
        dur = f" {minutos * 60}s" if minutos else ""
        await ejecutar(inter, f"weather {tipo.value}{dur}", f"Clima: {tipo.name}" + (f" ({minutos} min)" if minutos else ""))

    @admin.command(name="dificultad", description="Cambia la dificultad del servidor")
    @app_commands.choices(nivel=[app_commands.Choice(name=n, value=v) for n, v in (
        ("Pacífico", "peaceful"), ("Fácil", "easy"), ("Normal", "normal"), ("Difícil", "hard"))])
    async def adm_dificultad(inter: discord.Interaction, nivel: app_commands.Choice[str]):
        await ejecutar(inter, f"difficulty {nivel.value}", f"Dificultad: {nivel.name}")

    @admin.command(name="mensaje", description="Mensaje en el juego: chat, título en el centro o encima de la hotbar")
    @app_commands.describe(
        texto="Texto. Admite códigos: &6 dorado, &c rojo, &a verde, &b turquesa, &l negrita, &o cursiva, &r normal…",
        donde="Dónde sale (por defecto en el chat)",
        jugador="Solo a este jugador (vacío = a todos)",
        subtitulo="Texto pequeño debajo del título (solo para 'Título')",
        color="Color de todo el texto (los códigos & mandan sobre esto)",
        negrita="Negrita", cursiva="Cursiva", subrayado="Subrayado", tachado="Tachado",
        ofuscado="Letras que cambian sin parar (efecto 'mágico')",
        segundos="Cuánto se ve el título (por defecto 3)",
        firma="En el chat, poner delante [CobbleBOT] (por defecto sí)",
    )
    @app_commands.choices(
        donde=[app_commands.Choice(name="💬 Chat", value="chat"),
               app_commands.Choice(name="🖥️ Título grande en el centro de la pantalla", value="titulo"),
               app_commands.Choice(name="📏 Encima de la hotbar", value="actionbar")],
        color=[app_commands.Choice(name=n, value=v) for v, n in COLORES.items()],
    )
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_mensaje(inter: discord.Interaction, texto: str,
                          donde: app_commands.Choice[str] | None = None, jugador: str | None = None,
                          subtitulo: str | None = None, color: app_commands.Choice[str] | None = None,
                          negrita: bool = False, cursiva: bool = False, subrayado: bool = False,
                          tachado: bool = False, ofuscado: bool = False,
                          segundos: app_commands.Range[float, 1, 30] = 3.0, firma: bool = True):
        t = texto_seguro(texto, 250)
        if not t:
            await inter.response.send_message("El mensaje está vacío.", ephemeral=True)
            return
        if jugador and not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return
        destino = jugador or "@a"
        lugar = donde.value if donde else "chat"
        estilo = {"color": color.value if color else None, "bold": negrita, "italic": cursiva,
                  "underlined": subrayado, "strikethrough": tachado, "obfuscated": ofuscado}
        hay_estilo = any(v for v in estilo.values())
        a_quien = f"a **{esc(jugador)}**" if jugador else "a todos"
        plano = texto_plano(t)

        if lugar == "chat":
            privado = bool(jugador)
            cmd = bot.cmd_mensaje(t, destino, privado=privado, estilo=estilo if hay_estilo else None, firma=firma)
            await ejecutar(inter, cmd, f"💬 Mensaje {a_quien}: «{plano}»", jugador)
        elif lugar == "actionbar":
            cmd = f"title {destino} actionbar {a_json(componentes(t, estilo if hay_estilo else {'color': 'yellow'}))}"
            await ejecutar(inter, cmd, f"📏 Encima de la hotbar {a_quien}: «{plano}»", jugador)
        else:
            cmds = bot.cmds_titulo(t, texto_seguro(subtitulo, 150), destino, estilo if hay_estilo else None, segundos)
            if bot.es_admin(inter.user):
                try:
                    for c in cmds[:-1]:     # tiempos y subtítulo; el título (el último) va por ejecutar()
                        await bot.rcon.comando(c)
                except RconError:
                    pass
            sub = f" / «{texto_plano(subtitulo)}»" if subtitulo else ""
            await ejecutar(inter, cmds[-1], f"🖥️ Título {a_quien}: «{plano}»{sub}", jugador)

    @admin.command(name="curar", description="Cura a todos los Pokémon del equipo de un jugador")
    @app_commands.describe(jugador="Jugador conectado")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_curar(inter: discord.Interaction, jugador: str):
        await ejecutar(inter, f"pokeheal {jugador}", f"Pokémon de **{esc(jugador)}** curados", jugador)

    @admin.command(name="experiencia", description="Da (o quita, con número negativo) experiencia a un jugador")
    @app_commands.describe(jugador="Jugador conectado", cantidad="Cantidad (negativa para quitar)", tipo="Niveles o puntos")
    @app_commands.choices(tipo=[app_commands.Choice(name="Niveles", value="levels"),
                                app_commands.Choice(name="Puntos", value="points")])
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_xp(inter: discord.Interaction, jugador: str, cantidad: app_commands.Range[int, -10000, 10000],
                     tipo: app_commands.Choice[str] | None = None):
        t = tipo.value if tipo else "levels"
        nombre = tipo.name.lower() if tipo else "niveles"
        await ejecutar(inter, f"experience add {jugador} {cantidad} {t}",
                       f"**{esc(jugador)}**: {cantidad:+} {nombre} de experiencia", jugador)

    @admin.command(name="guardar", description="Guarda el mundo ahora")
    async def adm_guardar(inter: discord.Interaction):
        await ejecutar(inter, "save-all", "Mundo guardado")

    class ConfirmarReinicio(discord.ui.View):
        def __init__(self, autor_id: int, segundos: int):
            super().__init__(timeout=30)
            self.autor_id, self.segundos = autor_id, segundos

        async def interaction_check(self, i: discord.Interaction) -> bool:
            return i.user.id == self.autor_id

        async def on_error(self, i: discord.Interaction, error: Exception, item) -> None:
            codigo = uuid.uuid4().hex[:6].upper()
            log.error("Error %s en el botón de reinicio (usuario %s)", codigo, i.user, exc_info=error)
            await bot.responder_error(i, f"⚠️ Algo ha fallado con el reinicio (código **{codigo}**).")

        @discord.ui.button(label="Sí, reiniciar", style=discord.ButtonStyle.danger)
        async def si(self, i: discord.Interaction, _):
            self.stop()
            await i.response.edit_message(content=f"🔄 Reiniciando en {self.segundos} s… (avisando a los jugadores)",
                                          view=None)
            await bot.enviar(bot.canal_estado, embed=discord.Embed(
                title="🔄 Reinicio del servidor",
                description=f"El servidor se reinicia en **{self.segundos} s**. Avisaré aquí cuando vuelva a estar online.",
                color=0xE67E22, timestamp=datetime.now(timezone.utc)).set_footer(text=f"Pedido por {i.user}"))
            try:
                await bot.rcon.comando(bot.cmd_mensaje(f"&c&l⚠ El servidor se reinicia en {self.segundos} segundos"))
                for c in bot.cmds_titulo("&c⚠ Reinicio", f"El servidor se reinicia en {self.segundos} s", segundos=5):
                    await bot.rcon.comando(c)
                await asyncio.sleep(self.segundos)
                await bot.rcon.comando("save-all")
                bot.marcar_reinicio(str(i.user))
                await bot.rcon.comando("stop")
            except RconError as e:
                await i.followup.send(f"No pude contactar con el servidor: {e}", ephemeral=True)
                return
            donde = f"en <#{bot.canal_estado}>" if bot.canal_estado else "con `/estado`"
            await i.followup.send(f"✅ Servidor parado. Se vuelve a arrancar solo (tarda 1-2 minutos); "
                                  f"avisaré {donde} en cuanto se pueda entrar.", ephemeral=True)
            await bot.enviar(bot.canal_registro, embed=discord.Embed(
                description=f"🔄 **{i.user}** reinició el servidor", color=0x555555,
                timestamp=datetime.now(timezone.utc)))

        @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
        async def no(self, i: discord.Interaction, _):
            self.stop()
            await i.response.edit_message(content="Reinicio cancelado.", view=None)

    @admin.command(name="reiniciar", description="Reinicia el servidor de Minecraft (avisa antes a los jugadores)")
    @app_commands.describe(aviso="Segundos de aviso a los jugadores (por defecto 30)")
    async def adm_reiniciar(inter: discord.Interaction, aviso: app_commands.Range[int, 5, 300] = 30):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        await inter.response.send_message(
            f"⚠️ ¿Reiniciar el servidor? Se avisará a los jugadores y se reiniciará en {aviso} s.",
            view=ConfirmarReinicio(inter.user.id, aviso), ephemeral=True)

    registrar_teams(bot, tree, admin, ac_jugador, ac_categoria)

    tree.add_command(admin)

    # ---- /dar (roles de regalos)
    # Oculto para quien no tenga "Gestionar servidor". Para enseñárselo a otro rol sin darle ese permiso:
    # Ajustes del servidor > Integraciones > CobbleBOT > /dar > añadir el rol.
    dar = app_commands.Group(name="dar", description="Dar Pokémon, dinero u objetos a un jugador (roles autorizados)",
                             guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    async def ac_especie(inter: discord.Interaction, actual: str):
        a = actual.lower().replace(" ", "")
        esp = await bot.especies()
        return [app_commands.Choice(name=n, value=i) for i, n in esp.items()
                if a in i or a in n.lower().replace(" ", "")][:25]

    async def _especie_elegida(inter: discord.Interaction):
        datos = await bot.datos_cobblemon()
        return datos.especies.get((getattr(inter.namespace, "especie", None) or "").lower())

    async def ac_habilidad(inter: discord.Interaction, actual: str):
        esp = await _especie_elegida(inter)
        if not esp:
            return []
        a = actual.lower().replace(" ", "")
        res = []
        for h in esp.habilidades:
            oculta = h.startswith("h:")
            h = h.split(":", 1)[-1]
            if a in h:
                res.append(app_commands.Choice(name=bonito(h) + (" (oculta)" if oculta else ""), value=h))
        return res[:25]

    async def ac_movimientos(inter: discord.Interaction, actual: str):
        # Varios separados por comas: se autocompleta el último
        esp = await _especie_elegida(inter)
        partes = [p.strip().lower() for p in actual.split(",")]
        hechos, ultimo = [p for p in partes[:-1] if p], partes[-1].replace(" ", "")
        if len(hechos) >= 4:
            return [app_commands.Choice(name=", ".join(bonito(m) for m in hechos)[:100], value=",".join(hechos)[:100])]
        lista = esp.movimientos if esp else []
        prefijo = ",".join(hechos) + ("," if hechos else "")
        return [app_commands.Choice(name=(", ".join(bonito(m) for m in hechos + [mv]))[:100], value=(prefijo + mv)[:100])
                for mv in lista if ultimo in mv and mv not in hechos][:25]

    async def ac_forma(inter: discord.Interaction, actual: str):
        esp = await _especie_elegida(inter)
        if not esp:
            return []
        return [app_commands.Choice(name=f, value=f.lower()) for f in ["Normal"] + esp.formas
                if actual.lower() in f.lower()][:25]

    async def ac_mod(inter: discord.Interaction, actual: str):
        a = actual.lower()
        return [app_commands.Choice(name=f"{m.nombre} ({len(m.objetos)} objetos)"[:100], value=ns)
                for ns, m in (await bot.objetos()).items()
                if a in ns or a in m.nombre.lower()][:25]

    async def ac_objeto(inter: discord.Interaction, actual: str):
        mods = await bot.objetos()
        mod = getattr(inter.namespace, "mod", None)
        res = buscar(mods, actual, mod)
        return [app_commands.Choice(name=(f"{n} — {i}" if mod else f"{n} — {i} [{nm}]")[:100], value=i[:100])
                for i, n, nm in res]

    async def ac_ball(inter: discord.Interaction, actual: str):
        a = actual.lower().replace(" ", "_")
        return [app_commands.Choice(name=bonito(b), value=f"cobblemon:{b}")
                for b in (await bot.datos_cobblemon()).balls if a in b][:25]

    async def regalar(inter: discord.Interaction, jugador: str, comando: str, que: str):
        """Comprueba permisos, lanza el comando por RCON, responde y lo deja registrado."""
        if not bot.puede_regalar(inter.user):
            await inter.response.send_message("No tienes permiso para dar cosas a los jugadores.", ephemeral=True)
            return
        if not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        try:
            salida = limpiar(await bot.rcon.comando(comando))
        except RconError as e:
            await inter.followup.send(f"No pude contactar con el servidor: {e}", ephemeral=True)
            return
        bajo = salida.lower()
        if "no player was found" in bajo:
            await inter.followup.send(f"**{esc(jugador)}** no está conectado. Tiene que estar dentro del servidor.",
                                      ephemeral=True)
            return
        fallo = any(p in bajo for p in PALABRAS_FALLO)
        if fallo:
            await inter.followup.send(f"❌ El servidor no lo aceptó:\n```\n{salida[:1500]}\n```", ephemeral=True)
        else:
            await inter.followup.send(f"🎁 Dado a **{esc(jugador)}**: {que}" + (f"\n> {salida[:500]}" if salida else ""),
                                      ephemeral=True)
        await bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"🎁 **{inter.user}** dio a **{esc(jugador)}**: {que}" + (f"\n❌ {salida[:300]}" if fallo else ""),
            color=0x8B0000 if fallo else 0x2E8B57, timestamp=datetime.now(timezone.utc)))

    @dar.command(name="pokemon", description="Da un Pokémon a un jugador conectado, con lo que quieras personalizar")
    @app_commands.describe(
        jugador="Jugador (tiene que estar conectado)",
        especie="Especie del Pokémon",
        nivel="Nivel (1-100, por defecto 5)",
        shiny="¿Shiny?",
        genero="Sexo",
        naturaleza="Naturaleza",
        habilidad="Habilidad (elige primero la especie)",
        ivs="IVs 0-31: un número para todos, 'max', 6 números (PS,Atq,Def,AtEsp,DefEsp,Vel) o 'atk=31 spe=31'",
        evs="EVs 0-252, máx. 510 en total: 6 números (PS,Atq,Def,AtEsp,DefEsp,Vel) o 'atk=252 spe=252'",
        movimientos="Hasta 4, separados por comas (elige primero la especie)",
        objeto="Objeto equipado, ej. cobblemon:leftovers",
        pokeball="Poké Ball en la que viene",
        mote="Mote (sin espacios, usa _)",
        amistad="Amistad (0-255)",
        forma="Forma regional/alternativa (elige primero la especie)",
        tera="Teratipo",
        dinamax="Nivel Dinamax (0-10)",
        gigamax="¿Factor Gigamax?",
        tamano="Tamaño (1 = normal, 0.5 = mitad, 2 = doble)",
        intercambiable="¿Se puede intercambiar?",
        extra="Otras propiedades de Cobblemon a mano, ej. 'status=burn'",
    )
    @app_commands.choices(
        genero=[app_commands.Choice(name=n, value=v) for n, v in
                (("Macho ♂", "male"), ("Hembra ♀", "female"), ("Sin género", "genderless"))],
        naturaleza=[app_commands.Choice(name=f"{es} ({en.capitalize()})", value=en) for en, es in NATURALEZAS.items()],
        tera=[app_commands.Choice(name=es, value=en) for en, es in TIPOS_TERA.items()],
    )
    @app_commands.autocomplete(jugador=ac_jugador, especie=ac_especie, habilidad=ac_habilidad,
                               movimientos=ac_movimientos, forma=ac_forma, pokeball=ac_ball)
    async def dar_pokemon(inter: discord.Interaction, jugador: str, especie: str,
                          nivel: app_commands.Range[int, 1, 100] = 5, shiny: bool = False,
                          genero: app_commands.Choice[str] | None = None,
                          naturaleza: app_commands.Choice[str] | None = None,
                          habilidad: str | None = None, ivs: str | None = None, evs: str | None = None,
                          movimientos: str | None = None, objeto: str | None = None, pokeball: str | None = None,
                          mote: str | None = None, amistad: app_commands.Range[int, 0, 255] | None = None,
                          forma: str | None = None, tera: app_commands.Choice[str] | None = None,
                          dinamax: app_commands.Range[int, 0, 10] | None = None, gigamax: bool | None = None,
                          tamano: app_commands.Range[float, 0.1, 5.0] | None = None,
                          intercambiable: bool | None = None, extra: str | None = None):
        async def error(msg: str):
            await inter.response.send_message(f"❌ {msg}", ephemeral=True)

        datos = await bot.datos_cobblemon()
        especie = especie.strip().lower().replace(" ", "")
        esp = datos.especies.get(especie)
        if not re.fullmatch(r"[a-z0-9_-]+", especie) or (datos.especies and not esp):
            return await error(f"No conozco la especie **{esc(especie)}**. Elígela de la lista.")

        props = [especie, f"level={nivel}"]
        detalles = [f"Nv. {nivel}"]
        if shiny:
            props.append("shiny=yes"); detalles.append("✨ shiny")
        if genero:
            props.append(f"gender={genero.value}"); detalles.append(genero.name)
        if naturaleza:
            props.append(f"nature={naturaleza.value}"); detalles.append(f"naturaleza {naturaleza.name}")
        if habilidad:
            h = habilidad.strip().lower().replace(" ", "")
            if not re.fullmatch(r"[a-z0-9_]+", h):
                return await error("Habilidad no válida.")
            if esp and esp.habilidades and h not in [x.split(":", 1)[-1] for x in esp.habilidades]:
                return await error(f"**{esp.nombre}** no puede tener la habilidad **{esc(h)}**. Elígela de la lista.")
            props.append(f"ability={h}"); detalles.append(f"habilidad {bonito(h)}")
        for texto, nombre, maximo, total in ((ivs, "ivs", 31, None), (evs, "evs", 252, 510)):
            if not texto:
                continue
            try:
                valores = parsear_stats(texto, maximo, total)
            except ValueError as e:
                msg = str(e) if not str(e).startswith("invalid literal") else "solo se admiten números"
                return await error(f"{nombre.upper()}: {msg}")
            sufijo = "_iv" if nombre == "ivs" else "_ev"
            props += [f"{k}{sufijo}={v}" for k, v in valores.items()]
            detalles.append(f"{nombre.upper()} {resumen_stats(valores)}")
        if movimientos:
            movs = [m.strip().lower().replace(" ", "") for m in movimientos.split(",") if m.strip()]
            if len(movs) > 4 or not all(re.fullmatch(r"[a-z0-9_]+", m) for m in movs):
                return await error("Movimientos: hasta 4, separados por comas.")
            if esp and esp.movimientos:
                malos = [m for m in movs if m not in esp.movimientos]
                if malos:
                    return await error(f"**{esp.nombre}** no aprende: {', '.join(malos)}. Elígelos de la lista.")
            props.append(f"moves={','.join(movs)}"); detalles.append("movs " + ", ".join(bonito(m) for m in movs))
        if objeto:
            o = objeto.strip().lower()
            if not RE_ID.match(o):
                return await error("Objeto equipado no válido. Ejemplo: `cobblemon:leftovers`")
            props.append(f"held_item={o}"); detalles.append(f"lleva {o}")
        if pokeball:
            b = pokeball.strip().lower()
            if not RE_ID.match(b):
                return await error("Poké Ball no válida.")
            props.append(f"pokeball={b}"); detalles.append(bonito(b))
        if mote:
            if not RE_MOTE.match(mote):
                return await error("El mote no puede tener espacios (usa _) ni pasar de 20 caracteres.")
            props.append(f"nickname={mote}"); detalles.append(f"mote «{mote}»")
        if amistad is not None:
            props.append(f"friendship={amistad}"); detalles.append(f"amistad {amistad}")
        if forma and forma.lower() != "normal":
            fo = forma.strip().lower().replace(" ", "")
            if not re.fullmatch(r"[a-z0-9_-]+", fo):
                return await error("Forma no válida.")
            props.append(f"form={fo}"); detalles.append(f"forma {forma}")
        if tera:
            props.append(f"tera_type={tera.value}"); detalles.append(f"teratipo {tera.name}")
        if dinamax is not None:
            props.append(f"dmax_level={dinamax}"); detalles.append(f"Dinamax {dinamax}")
        if gigamax is not None:
            props.append(f"gmax_factor={'yes' if gigamax else 'no'}"); detalles.append("Gigamax" if gigamax else "sin Gigamax")
        if tamano is not None:
            props.append(f"scale_modifier={tamano:g}"); detalles.append(f"tamaño ×{tamano:g}")
        if intercambiable is not None:
            props.append(f"tradeable={'yes' if intercambiable else 'no'}")
            detalles.append("intercambiable" if intercambiable else "no intercambiable")
        if extra:
            trozos = extra.strip().lower().split()
            if not all(RE_EXTRA.match(t) for t in trozos):
                return await error("Extra: escribe propiedades `clave=valor` separadas por espacios.")
            props += trozos; detalles.append(" ".join(trozos))

        nombre = esp.nombre if esp else bonito(especie)
        await regalar(inter, jugador, f"pokegiveother {jugador} {' '.join(props)}",
                      f"**{nombre}** ({' · '.join(detalles)})")

    @dar.command(name="dinero", description="Da CobbleDollars a un jugador conectado")
    @app_commands.describe(jugador="Jugador (tiene que estar conectado)", cantidad="Cuánto dinero")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def dar_dinero(inter: discord.Interaction, jugador: str,
                         cantidad: app_commands.Range[int, 1, 1_000_000]):
        await regalar(inter, jugador, f"cobbledollars give {jugador} {cantidad}", f"**{bot.fmt_dinero(cantidad)}**")

    @dar.command(name="objeto", description="Da objetos a un jugador conectado: elige el mod, luego el objeto")
    @app_commands.describe(jugador="Jugador (tiene que estar conectado)",
                           mod="Mod del objeto (Minecraft, Cobblemon…): filtra la lista de objetos",
                           objeto="Objeto: escribe para buscar por nombre",
                           cantidad="Cuántos (por defecto 1, máximo 2304)")
    @app_commands.autocomplete(jugador=ac_jugador, mod=ac_mod, objeto=ac_objeto)
    async def dar_objeto(inter: discord.Interaction, jugador: str, mod: str, objeto: str,
                         cantidad: app_commands.Range[int, 1, 2304] = 1):
        objeto = objeto.strip().lower()
        if ":" not in objeto:
            objeto = f"{mod.strip().lower()}:{objeto}"
        if not RE_OBJETO.match(objeto):
            await inter.response.send_message("Ese ID de objeto no es válido. Ejemplo: `cobblemon:rare_candy`",
                                              ephemeral=True)
            return
        mods = await bot.objetos()
        ns = objeto.split(":", 1)[0] if ":" in objeto else "minecraft"
        nombre = mods[ns].objetos.get(objeto) if ns in mods else None
        que = f"**{cantidad}× {nombre}** (`{objeto}`)" if nombre else f"**{cantidad}× {objeto}**"
        await regalar(inter, jugador, f"give {jugador} {objeto} {cantidad}", que)

    tree.add_command(dar)

    # ---- /actualizar (admins)
    @tree.command(name="actualizar", description="Guarda el mundo y recarga las estadísticas ahora (solo admins)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def actualizar(inter: discord.Interaction):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        if not bot.perfiles:
            await inter.response.send_message("Las estadísticas no están configuradas.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        n = await bot.refrescar()
        await inter.followup.send(f"Estadísticas recargadas: {n} jugadores.", ephemeral=True)

    # ---- /diagnostico (admins)
    @tree.command(name="diagnostico", description="Muestra qué datos encuentra el bot de un jugador (solo admins)")
    @app_commands.describe(jugador="Nombre del jugador en Minecraft")
    @app_commands.autocomplete(jugador=ac_jugador)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def diagnostico(inter: discord.Interaction, jugador: str):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        p = await perfil_o_error(inter, jugador)
        if not p:
            return
        d = await bot.perfiles.diagnostico(p.uuid)
        texto = [
            f"**UUID:** `{p.uuid}`",
            f"**Equipo:** {d['rutas_pokemon']['playerpartystore'] or '❌ no encontrado'} → {len(p.equipo)} Pokémon",
            f"**PC:** {d['rutas_pokemon']['pcstore'] or '❌ no encontrado'} → {len(p.pc)} Pokémon",
            f"**Dinero:** {d['n_archivos_dinero']} archivos en la carpeta de CobbleDollars → {bot.fmt_dinero(p.dinero)}",
            f"**Medallas detectadas:** {len(p.medallas)} · **Ligas detectadas:** {len(p.ligas)}",
            f"**Stats de Cobblemon:** {', '.join(f'`{k}`' for k in sorted(p.cobblemon)) or 'ninguna'}",
            "",
            f"**Logros de mods completados ({len(d['logros_mods'])}):**",
        ]
        cuerpo = "\n".join(texto)
        lista = "\n".join(d["logros_mods"]) or "(ninguno)"
        await inter.followup.send(cuerpo[:1900], ephemeral=True)
        # La lista de logros puede ser larga: se manda como archivo
        await inter.followup.send(
            "Lista completa de IDs de logros (para ajustar `[cobbleverse]` en config.toml):",
            file=discord.File(io.BytesIO(lista.encode()), filename=f"logros_{p.nombre}.txt"),
            ephemeral=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    ruta = Path(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    if not ruta.exists():
        sys.exit(f"No encuentro {ruta}. Copia config.ejemplo.toml a config.toml.")
    # Claves y ajustes de la instalación: .env junto a config.toml (en Docker llegan del compose)
    cargar_dotenv(ruta.resolve().parent / ".env")
    with ruta.open("rb") as f:
        cfg = aplicar_entorno(tomllib.load(f))
    token = cfg.get("discord", {}).get("token", "")
    if not token or token.startswith("PEGA_"):
        sys.exit("Falta el token del bot: ponlo en DISCORD_TOKEN dentro del archivo .env")
    CobbleBot(cfg).run(token, log_handler=None)


# =================================================================== teams
def registrar_teams(bot: CobbleBot, tree: app_commands.CommandTree, admin: app_commands.Group,
                    ac_jugador, ac_categoria):
    opciones_color = [app_commands.Choice(name=n, value=v) for v, n in COLORES.items()]

    async def ac_team(inter: discord.Interaction, actual: str):
        a = actual.lower()
        return [app_commands.Choice(name=t.titulo[:100], value=tid) for tid, t in bot.teams.items()
                if a in t.nombre.lower() or a in tid][:25]

    async def ac_team_o_ninguno(inter: discord.Interaction, actual: str):
        return ([app_commands.Choice(name="Sin team", value="-")] if "sin team".startswith(actual.lower()) else []) \
            + (await ac_team(inter, actual))[:24]

    async def ac_team_discord(inter: discord.Interaction, actual: str):
        return [c for c in await ac_team(inter, actual) if bot.teams[c.value].desde_discord]

    async def team_o_error(inter: discord.Interaction, team: str) -> Team | None:
        t = bot.teams.get(team) or next((t for t in bot.teams.values() if t.nombre.lower() == team.lower()), None)
        if not t:
            msg = ("Todavía no hay teams: un admin puede crearlos con `/admin team-crear`." if not bot.teams
                   else f"No existe el team «{esc(team)}». Elígelo de la lista.")
            await inter.response.send_message(msg, ephemeral=True)
        return t

    grupo = app_commands.Group(name="team", description="Teams del servidor: vincula tu cuenta, únete y compite",
                               guild_only=True)

    def miembro_de(inter: discord.Interaction) -> discord.Member | None:
        return inter.user if isinstance(inter.user, discord.Member) else None

    @grupo.command(name="vincular", description="Une tu cuenta de Discord con tu jugador de Minecraft (tienes que estar conectado)")
    @app_commands.describe(jugador="Tu nombre en Minecraft")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def team_vincular(inter: discord.Interaction, jugador: str):
        if not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        _, _, conectados = parsear_list(await bot.rcon.comando("list"))
        nombre = next((n for n in conectados if n.lower() == jugador.lower()), None)
        if not nombre:
            await inter.followup.send(f"**{esc(jugador)}** no está conectado. Entra al servidor y vuelve a probar: "
                                      "te mandaré un código al chat del juego.", ephemeral=True)
            return
        codigo = bot.registro.nuevo_codigo(inter.user.id, nombre)
        await bot.rcon.comando(bot.cmd_mensaje(
            f"&eCódigo para vincular tu Discord (&f{inter.user}&e): &a&l{codigo}&r&7 · caduca en 5 min. "
            f"Úsalo con /team confirmar en Discord.", nombre, privado=True,
            estilo={"color": "yellow"}))
        await inter.followup.send(f"📨 Te he mandado un código al **chat del juego**. Escríbelo aquí con "
                                  f"`/team confirmar codigo:…` (caduca en 5 minutos).", ephemeral=True)

    @grupo.command(name="confirmar", description="Termina de vincular tu cuenta con el código que te llegó al juego")
    @app_commands.describe(codigo="El código de 6 cifras del chat del juego")
    async def team_confirmar(inter: discord.Interaction, codigo: str):
        jugador = bot.registro.comprobar_codigo(inter.user.id, codigo)
        if not jugador:
            await inter.response.send_message("❌ Código incorrecto o caducado. Pide otro con `/team vincular`.",
                                              ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        cache = (await bot.fuente.leer_json("usercache.json") if bot.fuente else None) or []
        uuid_mc = next((c.get("uuid") for c in cache if c.get("name", "").lower() == jugador.lower()), None)
        async with bot.registro.lock:
            bot.registro.vincular(inter.user.id, jugador, uuid_mc)
        log.info("%s vinculado con el jugador %s", inter.user, jugador)
        await bot.sincronizar_teams()   # si ya tenía rol de team en Discord, se aplica en el juego
        team = bot.registro.team_de(inter.user.id)
        extra = (f" Ya estás en **{bot.teams[team].titulo}**." if team in bot.teams
                 else " Ahora elige tu team con `/team unirse`.")
        await inter.followup.send(f"✅ Tu Discord queda vinculado con **{esc(jugador)}**.{extra}", ephemeral=True)

    @grupo.command(name="unirse", description="Elige tu team (solo la primera vez; para cambiar, pídeselo a un admin)")
    @app_commands.describe(team="El team al que te unes")
    @app_commands.autocomplete(team=ac_team)
    async def team_unirse(inter: discord.Interaction, team: str):
        elegido = await team_o_error(inter, team)
        if not elegido:
            return
        miembro = miembro_de(inter)
        if not miembro or not bot.registro.jugador_de(inter.user.id):
            await inter.response.send_message("Primero vincula tu cuenta de Minecraft con `/team vincular`.",
                                              ephemeral=True)
            return
        roles = bot.roles_teams(miembro.guild)
        actual = bot.registro.team_de(inter.user.id) or next(
            (tid for tid, r in roles.items() if r and r in miembro.roles), None)
        if actual:
            await inter.response.send_message(
                f"Ya estás en **{bot.teams[actual].titulo}**. Para cambiarte, pídeselo a un admin.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        problemas = await bot.cambiar_team(miembro, elegido.id)
        t = elegido
        await inter.followup.send(f"🛡️ ¡Bienvenido a **{t.titulo}**!" + "".join(f"\n⚠️ {p}" for p in problemas),
                                  ephemeral=True)
        await bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"🛡️ **{inter.user}** ({esc(bot.registro.jugador_de(inter.user.id))}) se unió a {t.titulo}",
            color=0x555555, timestamp=datetime.now(timezone.utc)))

    @grupo.command(name="info", description="Miembros de cada team (o de uno)")
    @app_commands.describe(team="Solo este team (opcional)")
    @app_commands.autocomplete(team=ac_team)
    async def team_info(inter: discord.Interaction, team: str | None = None):
        uno = await team_o_error(inter, team) if team else None
        if team and not uno:
            return
        if not bot.teams:
            await inter.response.send_message("Todavía no hay teams: un admin puede crearlos con `/admin team-crear`.",
                                              ephemeral=True)
            return
        ids = [uno.id] if uno else list(bot.teams)
        e = discord.Embed(title="🛡️ Teams" if not uno else f"🛡️ {uno.titulo}", color=COLOR)
        if uno:
            rol = bot.roles_teams(inter.guild).get(uno.id) if inter.guild else None
            e.description = (f"Rol: {rol.mention if rol else '—'} · Color: **{COLORES.get(uno.color, uno.color)}** · "
                             f"En el chat: `[{uno.etiqueta or uno.nombre}] Jugador` · "
                             f"Fuego amigo: **{'sí' if uno.fuego_amigo else 'no'}**")
        for tid in ids[:25]:
            t = bot.teams[tid]
            miembros = bot.registro.miembros(tid)
            lista = "\n".join(f"<@{d}> · {esc(n)}" for d, n in miembros) if uno else \
                ", ".join(esc(n) for _, n in miembros)
            e.add_field(name=f"{t.titulo} ({len(miembros)})", value=(lista or "Sin miembros todavía")[:1024],
                        inline=not uno)
        if not bot.registro.jugador_de(inter.user.id):
            e.set_footer(text="¿Aún no estás en ninguno? /team vincular y luego /team unirse")
        await inter.response.send_message(embed=e, ephemeral=True)

    @grupo.command(name="ranking", description="Qué team va ganando: suma de sus miembros")
    @app_commands.describe(categoria="Qué se suma: medallas, capturas, horas…", region="Para medallas y ligas")
    @app_commands.autocomplete(categoria=ac_categoria)
    @app_commands.choices(region=OPCIONES_REGION)
    async def team_ranking(inter: discord.Interaction, categoria: str,
                           region: app_commands.Choice[str] | None = None):
        cat = bot.categorias.get(categoria)
        if not cat or not bot.perfiles:
            await inter.response.send_message("Esa categoría no existe. Elígela de la lista.", ephemeral=True)
            return
        if not bot.teams:
            await inter.response.send_message("Todavía no hay teams: un admin puede crearlos con `/admin team-crear`.",
                                              ephemeral=True)
            return
        await inter.response.defer()
        reg = region.value if (region and cat.por_region) else None
        perfiles = {p.nombre.lower(): p for p in await bot.perfiles.todos()}
        filas = []
        for tid, t in bot.teams.items():
            miembros = [perfiles[n.lower()] for _, n in bot.registro.miembros(tid) if n.lower() in perfiles]
            total = sum(cat.valor(p, reg) for p in miembros)
            mejor = max(miembros, key=lambda p: cat.valor(p, reg), default=None)
            filas.append((total, t, len(bot.registro.miembros(tid)), mejor))
        filas.sort(key=lambda f: f[0], reverse=True)
        medallas = ["🥇", "🥈", "🥉"]
        titulo = cat.titulo + (f" de {reg.capitalize()}" if reg else "")
        e = discord.Embed(title=f"🛡️ Ranking de teams — {titulo}", color=COLOR)
        e.description = "\n".join(
            f"{medallas[i] if i < 3 else f'**{i + 1}.**'} **{t.titulo}** — {cat.formato(total)}"
            f" · {n} miembro{'s' if n != 1 else ''}"
            + (f" · MVP: {esc(mejor.nombre)}" if mejor and cat.valor(mejor, reg) > 0 else "")
            for i, (total, t, n, mejor) in enumerate(filas))
        e.set_footer(text="Suma de los miembros vinculados de cada team")
        await inter.followup.send(embed=e)

    tree.add_command(grupo)

    # ---- gestión de teams para admins (dentro de /admin)
    @admin.command(name="team", description="Pone o quita el team de un usuario (Discord y Minecraft)")
    @app_commands.describe(usuario="Usuario de Discord", team="Team (o 'Sin team' para quitarlo)")
    @app_commands.autocomplete(team=ac_team_o_ninguno)
    async def adm_team(inter: discord.Interaction, usuario: discord.Member, team: str):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        if team in ("-", "Sin team"):
            nuevo = None
        else:
            t = await team_o_error(inter, team)
            if not t:
                return
            nuevo = t.id
        await inter.response.defer(ephemeral=True)
        problemas = await bot.cambiar_team(usuario, nuevo)
        jugador = bot.registro.jugador_de(usuario.id)
        if not jugador:
            problemas.append("No tiene la cuenta de Minecraft vinculada: en el juego se aplicará cuando la vincule "
                             "(`/team vincular`, o `/admin vincular`).")
        que = f"{usuario.mention} → **{bot.teams[nuevo].titulo}**" if nuevo else f"{usuario.mention} → sin team"
        await inter.followup.send(f"✅ {que}" + "".join(f"\n⚠️ {p}" for p in problemas), ephemeral=True)
        await bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"🛡️ **{inter.user}**: {que}", color=0x555555, timestamp=datetime.now(timezone.utc)))

    def registrar_cambio(texto: str, quien):
        return bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"🛡️ **{quien}** {texto}", color=0x555555, timestamp=datetime.now(timezone.utc)))

    async def aplicar_en_mc(t: Team) -> str:
        try:
            for c in comandos_crear(t):
                await bot.rcon.comando(c)
            return ""
        except RconError:
            return "\n⚠️ El servidor de Minecraft no responde: el team se creará en el juego cuando vuelva."

    @admin.command(name="team-crear", description="Crea un team (en Minecraft y con su rol de Discord) con el nombre que quieras")
    @app_commands.describe(nombre="Nombre del team (el que quieras)", color="Color de la [etiqueta] y del rol",
                           etiqueta="Lo que sale entre [] delante del jugador en el chat (por defecto el nombre)",
                           rol="Rol de Discord del team (si no eliges ninguno, el bot crea uno con el nombre del team)",
                           emoji="Emoji para Discord (opcional)",
                           fuego_amigo="¿Los del mismo team se pueden hacer daño? (por defecto no)")
    @app_commands.choices(color=opciones_color)
    async def adm_team_crear(inter: discord.Interaction, nombre: app_commands.Range[str, 1, 32],
                             color: app_commands.Choice[str] | None = None,
                             etiqueta: app_commands.Range[str, 1, 24] | None = None,
                             rol: discord.Role | None = None, emoji: app_commands.Range[str, 1, 8] | None = None,
                             fuego_amigo: bool = False):
        if not bot.es_admin(inter.user) or not inter.guild:
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        nombre = " ".join(nombre.split())
        if any(t.nombre.lower() == nombre.lower() for t in bot.teams.values()):
            await inter.response.send_message(f"Ya hay un team llamado **{esc(nombre)}**.", ephemeral=True)
            return
        if rol and (rol.is_default() or rol.managed):
            await inter.response.send_message("Ese rol no vale para un team (elige uno normal o deja que lo cree yo).",
                                              ephemeral=True)
            return
        if rol and any(bot.roles_teams(inter.guild).get(tid) == rol for tid in bot.teams):
            await inter.response.send_message(f"El rol {rol.mention} ya es de otro team.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        c = color.value if color else "white"
        creado = False
        if not rol:
            if not inter.guild.me.guild_permissions.manage_roles:
                await inter.followup.send("❌ Para crear el rol del team me falta el permiso **Gestionar roles** "
                                          "(o elige un rol que ya exista en la opción `rol`).", ephemeral=True)
                return
            try:
                rol = await inter.guild.create_role(name=nombre, colour=discord.Colour(HEX_COLORES.get(c, 0)),
                                                    mentionable=True, reason=f"CobbleBOT: team creado por {inter.user}")
                creado = True
            except discord.HTTPException as e:
                await inter.followup.send(f"❌ Discord no me dejó crear el rol ({e.text or e.status}).", ephemeral=True)
                return
        t = Team(id_para(nombre, bot.teams), nombre, str(rol.id), c, etiqueta_de(etiqueta or "") or nombre,
                 fuego_amigo, emoji or "", rol_creado=creado)
        async with bot.registro.lock:
            bot.registro.guardar_team(t)
        aviso = await aplicar_en_mc(t)
        falta = bot.puede_gestionar_roles(inter.guild, {t.id: rol})
        if falta:
            aviso += f"\n⚠️ Para poner y quitar el rol a los jugadores, {falta}."
        await inter.followup.send(
            f"✅ Team **{esc(t.titulo)}** creado · rol {rol.mention}{' (nuevo)' if creado else ''} · "
            f"color {COLORES.get(c, c)} · en el chat sale `[{t.etiqueta}] Jugador` · "
            f"fuego amigo {'sí' if fuego_amigo else 'no'}.\n"
            f"Los jugadores ya pueden entrar con `/team unirse` (o mételos tú con `/admin team`).{aviso}",
            ephemeral=True)
        await registrar_cambio(f"creó el team **{esc(t.titulo)}** (`{t.id}`)", inter.user)

    @admin.command(name="team-editar", description="Cambia el nombre, color, etiqueta [ ], rol, emoji o fuego amigo de un team")
    @app_commands.describe(team="Team (de los creados desde Discord)", nombre="Nombre nuevo", color="Color nuevo",
                           etiqueta="Texto nuevo entre [] delante del jugador", rol="Otro rol de Discord para el team",
                           emoji="Emoji nuevo ('-' para quitarlo)", fuego_amigo="Fuego amigo")
    @app_commands.autocomplete(team=ac_team_discord)
    @app_commands.choices(color=opciones_color)
    async def adm_team_editar(inter: discord.Interaction, team: str,
                              nombre: app_commands.Range[str, 1, 32] | None = None,
                              color: app_commands.Choice[str] | None = None,
                              etiqueta: app_commands.Range[str, 1, 24] | None = None,
                              rol: discord.Role | None = None, emoji: app_commands.Range[str, 1, 8] | None = None,
                              fuego_amigo: bool | None = None):
        if not bot.es_admin(inter.user) or not inter.guild:
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        t = await team_o_error(inter, team)
        if not t:
            return
        if not t.desde_discord:
            await inter.response.send_message("Ese team está definido en `config.toml`: cámbialo ahí.", ephemeral=True)
            return
        if all(v is None for v in (nombre, color, etiqueta, rol, emoji, fuego_amigo)):
            await inter.response.send_message("No has cambiado nada.", ephemeral=True)
            return
        if nombre and any(o.id != t.id and o.nombre.lower() == " ".join(nombre.split()).lower()
                          for o in bot.teams.values()):
            await inter.response.send_message(f"Ya hay otro team llamado **{esc(nombre)}**.", ephemeral=True)
            return
        if rol and (rol.is_default() or rol.managed or any(
                tid != t.id and r == rol for tid, r in bot.roles_teams(inter.guild).items())):
            await inter.response.send_message("Ese rol no vale (es de otro team o no se puede asignar).", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        rol_antes = bot.roles_teams(inter.guild).get(t.id)
        if nombre:
            t.nombre = " ".join(nombre.split())
        if color:
            t.color = color.value
        if etiqueta:
            t.etiqueta = etiqueta_de(etiqueta) or t.nombre
        if emoji:
            t.emoji = "" if emoji.strip() == "-" else emoji.strip()
        if fuego_amigo is not None:
            t.fuego_amigo = fuego_amigo
        avisos = ""
        if rol and rol != rol_antes:
            # Los miembros pasan al rol nuevo
            t.rol, t.rol_creado = str(rol.id), False
            for did, tid in list(bot.registro.equipos.items()):
                if tid == t.id:
                    m = inter.guild.get_member(int(did))
                    if m and rol_antes and rol_antes in m.roles:
                        try:
                            await m.remove_roles(rol_antes, reason="CobbleBOT: el team cambió de rol")
                        except discord.HTTPException:
                            pass
                    bot.registro.marcar_pendiente(int(did), True)
        elif t.rol_creado and rol_antes and (nombre or color):
            # El rol lo creó el bot: se le pone también el nombre y el color nuevos
            try:
                await rol_antes.edit(name=t.nombre, colour=discord.Colour(HEX_COLORES.get(t.color, 0)),
                                     reason=f"CobbleBOT: team editado por {inter.user}")
            except discord.HTTPException:
                avisos += "\n⚠️ No he podido cambiar el nombre o el color del rol de Discord."
        async with bot.registro.lock:
            bot.registro.guardar_team(t)
        avisos += await aplicar_en_mc(t)
        if rol and rol != rol_antes:
            await bot.sincronizar_teams()
        r = bot.roles_teams(inter.guild).get(t.id)
        await inter.followup.send(
            f"✅ Team **{esc(t.titulo)}** actualizado · rol {r.mention if r else '—'} · "
            f"color {COLORES.get(t.color, t.color)} · en el chat sale `[{t.etiqueta}] Jugador` · "
            f"fuego amigo {'sí' if t.fuego_amigo else 'no'}.{avisos}", ephemeral=True)
        await registrar_cambio(f"editó el team **{esc(t.titulo)}** (`{t.id}`)", inter.user)

    class ConfirmarBorrado(discord.ui.View):
        def __init__(self, autor_id: int, t: Team):
            super().__init__(timeout=30)
            self.autor_id, self.t = autor_id, t

        async def interaction_check(self, i: discord.Interaction) -> bool:
            return i.user.id == self.autor_id

        @discord.ui.button(label="Sí, borrar", style=discord.ButtonStyle.danger)
        async def si(self, i: discord.Interaction, _):
            self.stop()
            await i.response.edit_message(content=f"⏳ Borrando **{esc(self.t.titulo)}**…", view=None)
            t, avisos = self.t, ""
            rol = bot.roles_teams(i.guild).get(t.id) if i.guild else None
            # Fuera de Minecraft (sus miembros se quedan sin team en el juego)
            try:
                await bot.rcon.comando(f"team remove {t.id}")
            except RconError:
                avisos += "\n⚠️ El servidor de Minecraft no responde: borra el team del juego cuando vuelva."
            # Fuera del rol de Discord: si lo creó el bot se borra; si no, solo se les quita a los miembros
            async with bot.registro.lock:
                estaban = bot.registro.borrar_team(t.id)
            if rol:
                try:
                    if t.rol_creado:
                        await rol.delete(reason=f"CobbleBOT: team borrado por {i.user}")
                    else:
                        for did in estaban:
                            m = i.guild.get_member(int(did))
                            if m and rol in m.roles:
                                await m.remove_roles(rol, reason=f"CobbleBOT: team borrado por {i.user}")
                except discord.HTTPException:
                    avisos += "\n⚠️ No he podido quitar el rol de Discord (revisa mis permisos)."
            await i.edit_original_response(
                content=f"🗑️ Team **{esc(t.titulo)}** borrado"
                        + (" (y su rol de Discord)." if rol and t.rol_creado else ".") + avisos)
            await registrar_cambio(f"borró el team **{esc(t.titulo)}** (`{t.id}`)", i.user)

        @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
        async def no(self, i: discord.Interaction, _):
            self.stop()
            await i.response.edit_message(content="No se ha borrado nada.", view=None)

    @admin.command(name="team-borrar", description="Borra un team (sus miembros se quedan sin team)")
    @app_commands.describe(team="Team a borrar (de los creados desde Discord)")
    @app_commands.autocomplete(team=ac_team_discord)
    async def adm_team_borrar(inter: discord.Interaction, team: str):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        t = await team_o_error(inter, team)
        if not t:
            return
        if not t.desde_discord:
            await inter.response.send_message("Ese team está definido en `config.toml`: quítalo de ahí.",
                                              ephemeral=True)
            return
        await inter.response.send_message(
            f"⚠️ ¿Borrar el team **{esc(t.titulo)}**? Sus miembros se quedarán sin team"
            + (" y se borrará su rol de Discord (lo creé yo)." if t.rol_creado else
               "; el rol de Discord no se borra, solo se les quita."),
            view=ConfirmarBorrado(inter.user.id, t), ephemeral=True)

    @admin.command(name="vincular", description="Vincula a mano un usuario de Discord con un jugador de Minecraft")
    @app_commands.describe(usuario="Usuario de Discord", jugador="Jugador de Minecraft")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_vincular(inter: discord.Interaction, usuario: discord.Member, jugador: str):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        if not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        cache = (await bot.fuente.leer_json("usercache.json") if bot.fuente else None) or []
        c = next((c for c in cache if c.get("name", "").lower() == jugador.lower()), None)
        nombre = c["name"] if c else jugador
        async with bot.registro.lock:
            bot.registro.vincular(usuario.id, nombre, c.get("uuid") if c else None)
        await bot.sincronizar_teams()
        aviso = "" if c else "\n⚠️ Ese jugador no ha entrado nunca al servidor (revisa que esté bien escrito)."
        await inter.followup.send(f"✅ {usuario.mention} vinculado con **{esc(nombre)}**.{aviso}", ephemeral=True)

    @admin.command(name="desvincular", description="Quita el vínculo Discord ↔ Minecraft de un usuario (y su team)")
    @app_commands.describe(usuario="Usuario de Discord")
    async def adm_desvincular(inter: discord.Interaction, usuario: discord.Member):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        problemas = await bot.cambiar_team(usuario, None) if bot.registro.team_de(usuario.id) else []
        async with bot.registro.lock:
            nombre = bot.registro.desvincular(usuario.id)
        msg = f"✅ {usuario.mention} ya no está vinculado" + (f" (era **{esc(nombre)}**)." if nombre else ".")
        await inter.followup.send(msg + "".join(f"\n⚠️ {p}" for p in problemas), ephemeral=True)
