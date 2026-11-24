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
from .estado_fijo import EstadoFijo, embed_estado
from .ligas import EstadoLigas
from .objetos_datos import Mod, buscar, catalogo_de_jars
from .texto_mc import COLORES, a_json, componentes, texto_plano
from .perfiles import NOMBRES_COBBLEMON, REGIONES, Perfiles, Perfil, bonito
from .rcon import RconClient, RconError
from .teams import COLORES as COLORES_TEAM
from .teams import (EMOJI_COLOR, RE_UUID, Registro, Team, color_de, png_color, comando_ftb, comandos_crear, etiqueta_de, id_mc, nombre_team_valido,
                    parsear_miembros, parsear_party)

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


RE_UUID_LIST = re.compile(r"\s*\([0-9a-fA-F-]{32,36}\)")
RE_ADORNO = re.compile(r"\[[^\]]*\]")
CMD_LIST = "list uuids"   # con uuids, Minecraft da el nombre a secas (sin la [etiqueta] del team)


def nombre_jugador(texto: str) -> str:
    """'[MVP] Ash_K', '🟢 Ash_K (conectado)', 'Ash_K (uuid)' -> 'Ash_K' (el nombre de Minecraft a secas)."""
    t = RE_ADORNO.sub(" ", RE_UUID_LIST.sub("", limpiar(texto)))
    t = t.replace("(conectado)", " ")
    validos = [x for x in re.split(r"[\s,]+", t) if RE_NOMBRE_MC.match(x)]
    return validos[0] if validos else texto.strip()


def parsear_list(respuesta: str) -> tuple[int, int, list[str]]:
    """'There are 2 of a max of 20 players online: Ana, [MVP] Luis (uuid)' -> (2, 20, ['Ana', 'Luis'])"""
    t = limpiar(respuesta)
    nums = re.findall(r"\d+", t.split(":")[0])
    online = int(nums[0]) if nums else 0
    maximo = int(nums[1]) if len(nums) > 1 else 0
    nombres = []
    if ":" in t:
        nombres = [nombre_jugador(n) for n in t.split(":", 1)[1].split(",") if n.strip()]
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
        # Respuestas de los comandos visibles para todo el canal (registro de lo que se ejecuta). Los errores,
        # los "no tienes permiso" y /help siguen saliendo solo para quien usa el comando.
        self.privado = not bool(d.get("respuestas_publicas", True))
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
        # Teams: los de FTB Teams (se crean en el juego). Lo que el bot recuerda va en su carpeta de datos
        self.carpeta_mundo = str(a.get("carpeta_mundo", "world")).strip("/")
        self.teams: dict[str, Team] = {}           # id del team de FTB -> Team (se relee al sincronizar)
        self._nombres: dict[str, str] = {}         # uuid -> nombre de jugador (de usercache.json)
        self._ausencias: dict[str, int] = {}       # team que no aparece -> lecturas seguidas sin verlo
        self._ftb_borrados: set[str] = set()       # teams que FTB ha movido a ftbteams/deleted
        self._lock_pendientes = asyncio.Lock()
        self._lock_teams = asyncio.Lock()
        self._ultima_actualizacion_teams = 0.0
        self.registro = Registro(os.environ.get("BOT_DATOS", "datos"))
        # Ligas completadas: anuncio en un canal y rol de Discord por región (también en la carpeta del bot)
        self.ligas = EstadoLigas(os.environ.get("BOT_DATOS", "datos"))
        # Mensaje fijo con el estado del servidor (/admin estado-fijo)
        self.estado_fijo = EstadoFijo(os.environ.get("BOT_DATOS", "datos"))
        self.direccion_servidor = str(cfg.get("monitor", {}).get("direccion", "")).strip()[:100]
        self._maximo = 0
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
    def team_de_uuid(self, uuid: str | None) -> Team | None:
        if not uuid:
            return None
        return next((t for t in self.teams.values() if uuid.lower() in t.miembros), None)

    def team_de_discord(self, discord_id) -> Team | None:
        uuid = self.registro.uuid_de(discord_id)
        if not uuid:
            jugador = (self.registro.jugador_de(discord_id) or "").lower()
            uuid = next((u for u, n in self._nombres.items() if n.lower() == jugador), None) if jugador else None
        return self.team_de_uuid(uuid)

    def nombres_miembros(self, t: Team) -> list[str]:
        return [self._nombres[u] for u in t.miembros if u in self._nombres]

    def rol_de(self, guild: discord.Guild, t) -> discord.Role | None:
        rol = str(t.rol if isinstance(t, Team) else t.get("rol", "") or "")
        return guild.get_role(int(rol)) if rol.isdigit() else None

    def puede_gestionar_roles(self, guild: discord.Guild, roles) -> str | None:
        """None si el bot puede poner y quitar esos roles; si no, qué le falta."""
        yo = guild.me
        if not yo.guild_permissions.manage_roles:
            return "me falta el permiso **Gestionar roles**"
        altos = [r.name for r in (roles.values() if isinstance(roles, dict) else roles) if r and r >= yo.top_role]
        if altos:
            return f"mi rol tiene que estar **por encima** de {', '.join(altos)} (Ajustes del servidor → Roles)"
        return None

    async def leer_teams(self) -> dict[str, Team] | None:
        """Teams de FTB del mundo. None si no se han podido leer (entonces no se toca nada)."""
        if not self.fuente:
            return None
        base = f"{self.carpeta_mundo}/ftbteams"
        try:
            if await self.fuente.leer_texto(f"{base}/ftbteams.snbt") is None:
                return {}           # FTB Teams no está (o el servidor aún no lo ha guardado nunca)
            teams = {}
            for archivo in await self.fuente.listar(f"{base}/party"):
                if archivo.endswith(".snbt"):
                    t = parsear_party(await self.fuente.leer_texto(f"{base}/party/{archivo}") or "")
                    if t:
                        teams[t.id] = t
            # FTB mueve aquí los teams disueltos: se quitan ya, sin esperar a la segunda lectura
            self._ftb_borrados = {m.group(0).lower() for a in await self.fuente.listar(f"{base}/deleted")
                                  if (m := re.search(RE_UUID, a))}
            cache = await self.fuente.leer_json("usercache.json") or []
            self._nombres = {c["uuid"].lower(): c["name"] for c in cache if c.get("uuid") and c.get("name")}
            for v in self.registro.vinculos.values():
                if v.get("uuid"):
                    self._nombres.setdefault(v["uuid"].lower(), v["nombre"])
            return teams
        except Exception:
            log.exception("No he podido leer los teams de FTB")
            return None

    async def sincronizar_teams(self, guardar_antes: bool = False):
        """Manda el juego: lee los teams de FTB y los copia a Discord (roles) y al chat ([etiqueta])."""
        async with self._lock_teams:
            if guardar_antes:
                try:
                    await self.rcon.comando("save-all")     # FTB escribe sus teams al guardar el mundo
                    await asyncio.sleep(3)
                except RconError:
                    pass
            leidos = await self.leer_teams()
            if leidos is None:
                return
            for t in leidos.values():
                self.registro.aplicar_extras(t)
            # Un team que deja de aparecer se da por borrado a la segunda lectura (por si se leyó a medio guardar)
            borrados = []
            for tid in list(self.registro.extras):
                if tid in leidos:
                    self._ausencias.pop(tid, None)
                else:
                    self._ausencias[tid] = self._ausencias.get(tid, 0) + 1
                    if self._ausencias[tid] >= 2 or tid in self._ftb_borrados:
                        borrados.append(tid)
            self.teams = dict(sorted(leidos.items(), key=lambda kv: kv[1].nombre.lower()))
            guild = self.get_guild(self.guild_id) if self.guild_id else None
            antiguos_hechos = await self._teams_discord(guild, borrados) if guild else set()
            await self._teams_minecraft(borrados)
            async with self.registro.lock:
                for tid in antiguos_hechos:
                    self.registro.antiguo_hecho(tid)
                for tid in borrados:
                    self.registro.olvidar_team(tid)
                    self.registro.olvidar_team_pendientes(tid)
                    self._ausencias.pop(tid, None)

    async def _teams_discord(self, guild: discord.Guild, borrados: list[str]) -> set[str]:
        """Roles de Discord de los teams. Devuelve los teams del sistema anterior que ya se han quitado de Discord."""
        puede = guild.me.guild_permissions.manage_roles
        hechos = set()
        # Roles del sistema anterior y de teams borrados: fuera (si los creó el bot, se borran)
        viejos = [(f"antiguo:{tid}", d) for tid, d in self.registro.antiguos.items()] + \
                 [(tid, self.registro.extras.get(tid, {})) for tid in borrados]
        for clave, d in viejos:
            rol = self.rol_de(guild, d)
            if rol and puede and rol < guild.me.top_role:
                try:
                    if d.get("rol_creado"):
                        await rol.delete(reason="CobbleBOT: el team ya no existe")
                    else:
                        for did in self.registro.vinculos:
                            m = await self.miembro_fresco(guild, did)
                            if m and rol in m.roles:
                                await m.remove_roles(rol, reason="CobbleBOT: el team ya no existe")
                except discord.HTTPException:
                    log.warning("No he podido quitar el rol %s", rol)
                    continue
            elif rol:
                continue        # sin permisos: se reintenta en la siguiente sincronización
            if clave.startswith("antiguo:"):
                hechos.add(clave.split(":", 1)[1])
        if not puede:
            return hechos
        # Un rol por team: lo crea el bot con el nombre y el color de FTB (y lo mantiene al día)
        for t in self.teams.values():
            rol = self.rol_de(guild, t)
            try:
                if not rol:
                    rol = await guild.create_role(name=t.nombre, colour=discord.Colour(t.color_int), mentionable=True,
                                                  reason="CobbleBOT: team nuevo de FTB Teams")
                    t.rol, t.rol_creado = str(rol.id), True
                    async with self.registro.lock:
                        self.registro.guardar_extras(t)
                    log.info("Rol creado para el team %s", t.nombre)
                elif t.rol_creado and rol < guild.me.top_role and (
                        rol.name != t.nombre or rol.colour.value != t.color_int):
                    await rol.edit(name=t.nombre, colour=discord.Colour(t.color_int),
                                   reason="CobbleBOT: el team cambió de nombre o color en el juego")
                elif t.id not in self.registro.extras:
                    async with self.registro.lock:
                        self.registro.guardar_extras(t)
            except discord.HTTPException as e:
                log.warning("No he podido crear o cambiar el rol del team %s: %s", t.nombre, e)
        # Cada usuario vinculado: el rol de su team y ninguno más
        roles = {tid: self.rol_de(guild, t) for tid, t in self.teams.items()}
        manejables = [r for r in roles.values() if r and r < guild.me.top_role]
        for did in list(self.registro.vinculos):
            m = await self.miembro_fresco(guild, did)
            if not m:
                continue
            t = self.team_de_discord(did)
            poner = roles.get(t.id) if t else None
            quitar = [r for r in manejables if r in m.roles and r != poner]
            try:
                if quitar:
                    await m.remove_roles(*quitar, reason="CobbleBOT: no está en ese team de FTB")
                if poner and poner in manejables and poner not in m.roles:
                    await m.add_roles(poner, reason="CobbleBOT: está en ese team de FTB")
            except discord.HTTPException as e:
                log.warning("No he podido cambiar los roles de team de %s: %s", m, e)
        return hechos

    async def _teams_minecraft(self, borrados: list[str]):
        """[Etiqueta] en el juego: un team de Minecraft (/team) por cada team de FTB, con sus miembros."""
        try:
            for tid in list(self.registro.antiguos):
                await self.rcon.comando(f"team remove {tid}")
            for tid in borrados:
                await self.rcon.comando(f"team remove {id_mc(tid)}")
            for t in self.teams.values():
                for c in comandos_crear(t):
                    await self.rcon.comando(c)
                en_mc = {n.lower(): n for n in parsear_miembros(await self.rcon.comando(f"team list {t.id_mc}"))}
                deseados = {n.lower(): n for n in self.nombres_miembros(t)}
                for n in deseados.keys() - en_mc.keys():
                    await self.rcon.comando(f"team join {t.id_mc} {deseados[n]}")
                for n in en_mc.keys() - deseados.keys():
                    await self.rcon.comando(f"team leave {en_mc[n]}")
        except RconError:
            pass

    @tasks.loop(minutes=1)
    async def sincronizar_teams_loop(self):
        # Un fallo no debe parar la sincronización para siempre: se apunta y se reintenta a los 5 minutos
        try:
            await self.sincronizar_teams()
        except Exception:
            log.exception("Error sincronizando teams")

    @sincronizar_teams_loop.before_loop
    async def _antes_teams(self):
        await self.wait_until_ready()

    @sincronizar_teams_loop.error
    async def _error_teams(self, error):
        log.exception("Error sincronizando teams", exc_info=error)

    # ---- lo que se pide desde Discord y se hace en el juego (FTB Teams)
    # Ojo: los comandos de FTB que necesitan "ser" el jugador (party create/leave/invite) NO funcionan desde la
    # consola ni con "execute as" (FTB comprueba los permisos con la consola). Por eso:
    #   · crear: se le manda al jugador un mensaje en el chat; al hacer clic, lo crea él (y el bot pone el color);
    #   · entrar, salir, echar y disolver: con los comandos de admin de FTB (force-add/force-remove/force-disband).
    async def ftb_cmd(self, comando: str) -> str | None:
        """Manda un comando de FTB Teams. None si ha ido bien; si no, el motivo."""
        try:
            salida = limpiar(await self.rcon.comando(comando))
        except RconError:
            return "el servidor no responde (¿está apagado?)"
        log.info("FTB: %s -> %s", comando, salida[:200].replace("\n", " | "))
        if re.search(r"unknown|incomplete|incorrect|error|not found|cannot|can't|no player|already|not in|must|"
                     r"isn't|is not|expected|invalid", salida, re.I):
            return salida[:200] or "FTB no ha aceptado el cambio"
        return None

    async def party_de(self, jugador: str) -> str | None | bool:
        """Nombre del team de FTB en el que está AHORA un jugador conectado (None: en ninguno; False: no se sabe)."""
        try:
            salida = limpiar(await self.rcon.comando(f"execute as {jugador} run ftbteams info"))
        except RconError:
            return False
        m = re.search(r"==\s*(.+?)\s*\[(\w+)\]\s*==", salida)
        if not m:
            return False
        return m.group(1) if m.group(2).lower() == "party" else None

    def jugador_en_team(self, jugador: str) -> Team | None:
        u = next((x for x, n in self._nombres.items() if n.lower() == jugador.lower()), None)
        return self.team_de_uuid(u)

    def _es_dueno(self, t: Team, jugador: str) -> bool:
        return self._nombres.get(t.lider, "").lower() == jugador.lower()

    async def _sacar(self, t: Team, j: str) -> str | None:
        """Saca a un jugador de su team. Si es el dueño y está solo, el team se disuelve."""
        if self._es_dueno(t, j):
            if len(t.miembros) > 1:
                return (f"es el dueño de **{t.nombre}** y tiene más miembros: hay que pasar el liderazgo "
                        "(en el juego) o disolver el team")
            return await self.ftb_cmd(f"ftbteams force-disband {t.id_corto}")
        return await self.ftb_cmd(f"ftbteams force-remove {t.id_corto} {j}")

    async def mandar_clic_crear(self, j: str, nombre: str):
        boton = {"text": f"[Crear el team {nombre}]", "color": "green", "bold": True,
                 "clickEvent": {"action": "run_command", "value": f"/ftbteams party create {nombre}"},
                 "hoverEvent": {"action": "show_text", "contents": {"text": "Haz clic para crear el team", "color": "yellow"}}}
        try:
            await self.rcon.comando(self.cmd_mensaje("&ePediste crear un team desde Discord: ", j, privado=True,
                                                     estilo={"color": "yellow"}, extra=[boton]))
        except RconError:
            pass

    async def hacer_en_juego(self, p: dict) -> tuple[str, str | None]:
        """Hace en el juego lo pedido desde Discord (el jugador tiene que estar conectado).
        Devuelve (estado, error): estado "hecho" o "clic" (esperando a que haga clic en el chat del juego)."""
        j, accion = p["jugador"], p["accion"]
        actual = self.jugador_en_team(j)
        if accion == "crear":
            party = await self.party_de(j)
            if party is False:
                party = actual.nombre if actual else None
            if party:
                if party.lower() != p["nombre"].lower():
                    return "hecho", f"ya estás en el team **{party}**: sal de él antes de crear otro"
                if p.get("color"):    # ya lo ha creado: se le pone el color elegido en Discord
                    await self.ftb_cmd(f"ftbteams party settings_for {j} ftbteams:color {p['color']}")
                return "hecho", None
            await self.mandar_clic_crear(j, p["nombre"])
            return "clic", None
        if accion == "unir":
            t = self.teams.get(p["team"])
            if not t:
                return "hecho", "ese team ya no existe"
            if actual and actual.id == t.id:
                return "hecho", None
            if actual and (f := await self._sacar(actual, j)):
                return "hecho", f"no he podido sacarte de **{actual.nombre}**: {f}"
            return "hecho", await self.ftb_cmd(f"ftbteams force-add {t.id_corto} {j}")
        if accion in ("salir", "echar"):
            t = actual if accion == "salir" else self.teams.get(p.get("team", ""))
            if not t or not actual or actual.id != t.id:
                return "hecho", None     # ya no está en ese team
            return "hecho", await self._sacar(t, j)
        return "hecho", None

    def texto_accion(self, p: dict) -> str:
        t = self.teams.get(p.get("team", ""))
        nombre = t.nombre if t else p.get("nombre", "?")
        return {"crear": f"crear el team **{nombre}**", "unir": f"entrar en el team **{nombre}**",
                "salir": "salir de tu team", "echar": f"salir del team **{nombre}** (te han echado)"}.get(p["accion"], "?")

    async def avisar_discord(self, discord_id, texto: str):
        """Mensaje privado a un usuario (si tiene los MD cerrados, no pasa nada)."""
        try:
            u = self.get_user(int(discord_id)) or await self.fetch_user(int(discord_id))
            await u.send(texto)
        except (discord.HTTPException, ValueError):
            pass

    async def pedir_en_juego(self, p: dict) -> tuple[str, str | None]:
        """Lo hace ya si el jugador está conectado; si no, lo deja pendiente para cuando entre.
        Devuelve (estado, error): estado "hecho", "pendiente" (no está conectado) o "clic" (crear: falta su clic)."""
        try:
            _, _, conectados = parsear_list(await self.rcon.comando(CMD_LIST))
        except RconError:
            conectados = []
        nombre = next((n for n in conectados if n.lower() == p["jugador"].lower()), None)
        datos = {k: v for k, v in p.items() if k not in ("accion", "discord", "jugador", "cuando")}
        if not nombre:
            async with self.registro.lock:
                self.registro.encolar(p["accion"], p["discord"], p["jugador"], **datos)
            return "pendiente", None
        async with self._lock_pendientes:
            estado, error = await self.hacer_en_juego({**p, "jugador": nombre})
            if estado == "clic":     # se comprueba cada 30 s si ya lo ha creado
                async with self.registro.lock:
                    self.registro.encolar(p["accion"], p["discord"], nombre, aviso=time.time(), **datos)
        if estado == "hecho" and not error:
            await self.sincronizar_teams(guardar_antes=True)
        return estado, error

    async def procesar_pendientes(self, conectados: set[str], recien: set[str]):
        """Lo pendiente desde Discord de los jugadores conectados (recien: los que acaban de entrar)."""
        en_linea = {n.lower(): n for n in conectados}
        hechos = []
        async with self._lock_pendientes:
            for p in list(self.registro.pendientes):
                nombre = en_linea.get(p["jugador"].lower())
                if not nombre:
                    continue
                try:
                    if p["accion"] == "crear":
                        party = await self.party_de(nombre)
                        if not party and nombre not in recien and time.time() - p.get("aviso", 0) < 300:
                            continue    # ya tiene el mensaje para hacer clic: se le recuerda cada 5 minutos
                    estado, error = await self.hacer_en_juego({**p, "jugador": nombre})
                except Exception:
                    log.exception("Error haciendo en el juego lo pendiente de %s", nombre)
                    estado, error = "hecho", "error inesperado del bot (avisa a un admin)"
                async with self.registro.lock:
                    if estado == "clic":
                        p["aviso"] = time.time()
                        self.registro.guardar()
                        continue
                    self.registro.quitar_pendiente(nombre)
                hechos.append((p, nombre, error))
        if not hechos:
            return
        await self.sincronizar_teams(guardar_antes=True)
        for p, nombre, error in hechos:
            # Sin avisos cuando sale bien (el cambio ya se ve); solo si falla, por privado, para que se sepa
            if error:
                await self.avisar_discord(p["discord"], f"⚠️ No he podido {self.texto_accion(p)}: {error}")
            log.info("Pendiente de %s (%s): %s", nombre, p["accion"], error or "hecho")

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
                    firma: bool = True, extra: list | None = None) -> str:
        """tellraw firmado con el nombre del bot: '[CobbleBOT] texto'. El texto admite códigos & (colores…).
        'extra': componentes JSON que se añaden al final tal cual (p. ej. texto en el que se puede hacer clic)."""
        estilo = estilo or {"color": "light_purple" if privado else "white", "italic": privado}
        cuerpo = componentes(texto, estilo) + list(extra or [])
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
            online, maximo, nombres = parsear_list(await self.rcon.comando(CMD_LIST))
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
                if not self.estado_fijo.canal:      # con mensaje fijo, el aviso es ese mismo mensaje (no uno nuevo)
                    await self.enviar(self.canal_estado, embed=discord.Embed(
                        title="🔴 El servidor no responde",
                        description="Puede estar apagado o reiniciándose. Avisaré aquí en cuanto se pueda entrar.",
                        color=0x8B0000, timestamp=datetime.now(timezone.utc)))
            if self._servidor_activo is False:
                await self.actualizar_estado_fijo(False, self._maximo, [])
            return

        self._maximo = maximo
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
            if not self.estado_fijo.canal:
                await self.enviar(self.canal_estado, embed=e)
            self.monitor.change_interval(seconds=self.intervalo_monitor)
            log.info("Servidor de vuelta: avisado en Discord")
            asyncio.create_task(self._teams_tras_reinicio())
        self._servidor_activo = True
        self._caido_desde = None
        self._reinicio_por = None

        actuales = set(nombres)
        pendientes = {p["jugador"].lower() for p in self.registro.pendientes}
        if pendientes & {n.lower() for n in actuales} and not self._lock_pendientes.locked():
            asyncio.create_task(self.procesar_pendientes(actuales, actuales - (self._online_previo or set())))
        if self._online_previo is not None and self.avisar_entradas:
            for n in sorted(actuales - self._online_previo):
                await self.enviar(self.canal_avisos, content=f"➡️ **{esc(n)}** ha entrado al servidor")
            for n in sorted(self._online_previo - actuales):
                await self.enviar(self.canal_avisos, content=f"⬅️ **{esc(n)}** ha salido del servidor")
        self._online_previo = actuales
        await self.actualizar_estado_fijo(True, maximo, nombres)

    async def actualizar_estado_fijo(self, online: bool, maximo: int, nombres: list[str]):
        """Edita el mensaje fijo de estado (solo si ha cambiado algo o hace rato que no se toca)."""
        ef = self.estado_fijo
        if not ef.canal:
            return
        ef.marcar(online)
        conectados = []
        for n in nombres:
            t = self.jugador_en_team(n)
            conectados.append((n, (t.etiqueta or t.nombre) if t else ""))
        if not ef.toca((online, tuple(conectados), maximo, self._reinicio_por)):
            return
        e = embed_estado(online, conectados, maximo, ef.online_desde if online else ef.caido_desde,
                         self._reinicio_por, self.direccion_servidor)
        try:
            canal = self.get_channel(ef.canal) or await self.fetch_channel(ef.canal)
            if ef.mensaje:
                try:
                    await canal.get_partial_message(ef.mensaje).edit(embed=e)
                    return
                except discord.NotFound:
                    pass        # lo han borrado: se publica otro
            m = await canal.send(embed=e)
            ef.poner(ef.canal, m.id)
            try:
                await m.pin(reason="CobbleBOT: estado del servidor")
            except discord.HTTPException:
                pass
        except discord.HTTPException as err:
            ef._firma = None        # se reintenta en la siguiente vuelta del monitor (30 s), no a los 5 minutos
            log.warning("No he podido actualizar el mensaje de estado: %s", err)

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
        perfiles = await self.perfiles.todos(forzar=True)
        try:
            await self.revisar_ligas(perfiles)
        except Exception:
            log.exception("Error revisando las ligas")
        return len(perfiles)

    # ---------------------------------------------------------------- ligas
    async def miembro_fresco(self, guild: discord.Guild, discord_id) -> discord.Member | None:
        """El miembro pedido a Discord en el momento (con sus roles al día), o None si ya no está."""
        try:
            return await guild.fetch_member(int(discord_id))
        except discord.HTTPException:
            return None

    async def miembro_de_jugador(self, guild: discord.Guild, jugador: str) -> discord.Member | None:
        """Usuario de Discord vinculado con ese jugador (con /team vincular), si lo hay."""
        did = self.registro.discord_de(jugador)
        if not did:
            return None
        try:
            return await guild.fetch_member(int(did))      # roles al día (la caché puede estar desfasada)
        except discord.HTTPException:
            return None

    async def poner_roles_ligas(self, perfiles) -> dict[str, list[str]]:
        """Pone a cada jugador vinculado el rol de las ligas que tenga. Devuelve {jugador: [regiones nuevas]}."""
        guild = self.get_guild(self.guild_id) if self.guild_id else None
        if not guild or not self.ligas.roles:
            return {}
        roles = {r: guild.get_role(i) for r, i in self.ligas.roles.items()}
        if self.puede_gestionar_roles(guild, {r: rol for r, rol in roles.items() if rol}):
            return {}
        puestos = {}
        for p in perfiles:
            faltan = [roles[l.region] for l in p.ligas if roles.get(l.region)]
            if not faltan:
                continue
            m = await self.miembro_de_jugador(guild, p.nombre)
            faltan = [r for r in dict.fromkeys(faltan) if m and r not in m.roles]
            if m and faltan:
                try:
                    await m.add_roles(*faltan, reason="CobbleBOT: liga completada")
                    puestos[p.nombre] = [r.name for r in faltan]
                    log.info("Rol de liga para %s: %s", p.nombre, ", ".join(r.name for r in faltan))
                except discord.HTTPException as e:
                    log.warning("No he podido poner el rol de liga a %s: %s", p.nombre, e)
        return puestos

    async def revisar_ligas(self, perfiles):
        """Anuncia las ligas nuevas en el canal de ligas y pone los roles."""
        nuevas = self.ligas.nuevas(perfiles)
        await self.poner_roles_ligas(perfiles)
        canal = self.ligas.canal or self.canal_avisos
        guild = self.get_guild(self.guild_id) if self.guild_id else None
        for p, liga in nuevas:
            region = (liga.region or "").capitalize()
            m = await self.miembro_de_jugador(guild, p.nombre) if guild else None
            rol = guild.get_role(self.ligas.roles.get(liga.region, 0)) if guild else None
            e = discord.Embed(title=f"🏆 ¡Liga{' de ' + region if region else ''} completada!", color=0xF1C40F,
                              timestamp=datetime.now(timezone.utc))
            e.description = (f"**{esc(p.nombre)}**{f' ({m.mention})' if m else ''} ha vencido a "
                             f"**{liga.nombre}**" + (f" y es campeón de **{region}**." if region else "."))
            if rol and m and rol in m.roles:
                e.add_field(name="Nuevo rol", value=rol.mention)
            e.set_thumbnail(url=url_avatar(p))
            log.info("Liga completada: %s -> %s", p.nombre, liga.id)
            try:
                await self.enviar(canal, content=m.mention if m else None, embed=e,
                                  allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            except discord.HTTPException as ex:
                log.warning("No he podido anunciar la liga en Discord: %s", ex)

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


def _cmd(comando: str, que: str) -> str:
    """Una línea de la ayuda: el comando arriba y qué hace debajo."""
    return f"**`{comando}`**\n╰ {que}"


def paginas_ayuda(bot: CobbleBot, admin: bool, regalar: bool = False) -> dict[str, tuple[str, str, str, discord.Embed]]:
    """Secciones de /help: clave -> (nombre, emoji, resumen, embed). Solo las que puede usar quien pregunta."""
    paginas: dict[str, tuple[str, str, str, discord.Embed]] = {}

    def pagina(clave: str, nombre: str, emoji: str, resumen: str, lineas: list[str], pie: str = ""):
        e = discord.Embed(title=f"{emoji} {nombre}", description="\n\n".join(lineas)[:4096], color=COLOR)
        e.set_footer(text=pie or "CobbleBOT · elige otra sección en la lista de abajo")
        paginas[clave] = (nombre, emoji, resumen, e)

    pagina("servidor", "Servidor", "🖥️", "Estado y quién está conectado", [
        _cmd("/estado", "Si el servidor está online y cuánta gente hay"),
        _cmd("/jugadores", "Quién está conectado ahora mismo")])
    pagina("jugadores", "Jugadores", "👤", "Fichas, equipos y medallas", [
        _cmd("/stats jugador", "Ficha completa: horas, dinero, medallas, ligas, equipo y colección"),
        _cmd("/equipo jugador", "Los 6 Pokémon del equipo y un resumen del PC"),
        _cmd("/medallas jugador [región]", "Medallas por región y ligas conseguidas")],
        "Al escribir un jugador salen sugerencias · 🟢 = conectado ahora")
    cats = " · ".join(f"`{k}`" for k in bot.categorias)
    pagina("rankings", "Rankings", "🏆", "Quién va ganando", [
        _cmd("/top categoría [región] [cantidad]", "Ranking de jugadores. La región solo cuenta para medallas y ligas"),
        f"**Categorías**\n{cats}"])
    pagina("vincular", "Vincular tu cuenta", "🔗", "Une tu Discord con tu jugador (una vez)", [
        "Hazlo una vez y recibirás el rol de tu team y el de cada liga que ganes.",
        _cmd("1 · /team vincular jugador", "Estando conectado al servidor: te llega un código al chat del juego "
                                           "(haz clic en él para copiarlo)"),
        _cmd("2 · /team confirmar codigo", "Pégalo aquí. Caduca en 5 minutos")])
    pagina("teams", "Teams", "🛡️", "Crear, invitar, panel y ranking", [
        "Los teams son los mismos en el juego (FTB Teams) y aquí: cada uno tiene su **rol** y su **[etiqueta]** "
        "en el chat del juego.",
        _cmd("/team crear nombre [color]", "Te sale un botón en el chat del juego para crearlo"),
        _cmd("/team invitar usuario", "Dueño y oficiales: le llega la invitación con botones"),
        _cmd("/team invitaciones", "Acepta o rechaza las tuyas"),
        _cmd("/team panel", "Dueño y oficiales: ✏️ nombre · 🎨 color · 🏷️ etiqueta y emoji · ⚔️ fuego amigo · "
                            "📨 invitar · 👢 echar · 🗑️ disolver"),
        _cmd("/team salir", "Sal de tu team"),
        _cmd("/team info [team]", "Miembros de cada team"),
        _cmd("/team ranking categoría", "Qué team va ganando (suma de sus miembros)"),
        _cmd("/team actualizar", "Ver ya los cambios hechos en el juego"),
        "**En el juego:** `/tm mensaje` habla solo con tu team."],
        "Si no estás conectado, crear, entrar o salir se hace en cuanto entres al servidor")
    canal = bot.ligas.canal or bot.canal_avisos
    pagina("ligas", "Ligas", "🏅", "Anuncios y roles de campeón", [
        "Al vencer al campeón de una región, el bot lo anuncia" + (f" en <#{canal}>" if canal else "")
        + " y te da el rol de esa liga si tienes el Discord vinculado (tarda unos minutos)."])
    if regalar:
        pagina("regalos", "Regalos", "🎁", "Pokémon, dinero y objetos (roles autorizados)", [
            _cmd("/dar pokemon jugador especie […]", "Nivel, shiny, alfa, sexo, naturaleza, habilidad, IVs, EVs, "
                                                     "movimientos, objeto, Poké Ball, mote, forma, teratipo, "
                                                     "Dinamax, tamaño…"),
            _cmd("/dar dinero jugador cantidad", "CobbleDollars"),
            _cmd("/dar objeto jugador mod objeto [cantidad]", "Elige el mod y busca el objeto por su nombre")],
            "El jugador tiene que estar conectado · todo queda registrado")
    if admin:
        pagina("admin", "Alto mando · servidor", "🔒", "Moderación, mundo y servidor", [
            _cmd("/admin expulsar · banear⚠️ · desbanear · baneados", "Moderación"),
            _cmd("/admin whitelist · op⚠️ · operadores", "Acceso y permisos"),
            _cmd("/admin modo · teletransportar · curar · experiencia", "Jugadores"),
            _cmd("/admin hora · clima · dificultad · guardar · reiniciar⚠️", "Mundo y servidor"),
            _cmd("/admin estado-fijo [canal]", "Mensaje fijo con el estado del servidor, que se actualiza solo "
                                               "(sin canal: quitarlo)"),
            _cmd("/admin mensaje", "En el chat, como título o en la hotbar, con colores (`&6` dorado, `&c` rojo, "
                                   "`&a` verde, `&l` negrita…)"),
            _cmd("/comando texto", "Cualquier comando permitido, con sugerencias"),
            _cmd("/actualizar · /diagnostico jugador", "Recargar los datos ya · qué encuentra el bot de alguien")],
            "🔒 Solo lo ve el alto mando · ⚠️ = pide confirmar con un botón antes de hacerlo")
        pagina("admin_teams", "Alto mando · teams y ligas", "🔒", "Cualquier team, vínculos y ligas", [
            _cmd("/team panel team:…", "El panel de cualquier team: ➕ meter a alguien sin invitación y echar a "
                                       "cualquiera menos al dueño"),
            _cmd("/admin team-editar team […]", "Etiqueta, emoji, fuego amigo o usar otro rol de Discord"),
            _cmd("/admin vincular · desvincular", "Vínculos Discord ↔ Minecraft a mano"),
            _cmd("/admin ligas [canal] [región] [rol]", "Canal de anuncios y rol de cada liga")],
            "🔒 Solo lo ve el alto mando")
    return paginas


def embed_ayuda(bot: CobbleBot, admin: bool, regalar: bool = False) -> discord.Embed:
    """Portada de /help: qué hay en cada sección (se abren con la lista de abajo)."""
    e = discord.Embed(title="📖 Ayuda de CobbleBOT",
                      description="Elige una sección en la lista de abajo para ver sus comandos."
                      + ("\n🔒 = secciones que solo ve el alto mando del servidor." if admin else ""), color=COLOR)
    for nombre, emoji, resumen, _ in paginas_ayuda(bot, admin, regalar).values():
        e.add_field(name=f"{emoji} {nombre}", value=resumen, inline=True)
    e.set_footer(text=("Las respuestas de los comandos las ve todo el canal (los errores y /help, solo tú) · "
                       if not bot.privado else "") + "Los datos se actualizan cuando el servidor guarda el mundo")
    return e


class VistaAyuda(discord.ui.View):
    """/help por secciones: una lista para saltar de una a otra."""

    def __init__(self, bot: CobbleBot, admin: bool, regalar: bool):
        super().__init__(timeout=900)
        self.paginas = paginas_ayuda(bot, admin, regalar)
        self.portada = embed_ayuda(bot, admin, regalar)
        self.seccion.options = [discord.SelectOption(label="Inicio", value="inicio", emoji="📖")] + [
            discord.SelectOption(label=nombre, value=clave, emoji=emoji, description=resumen[:100])
            for clave, (nombre, emoji, resumen, _) in self.paginas.items()]

    @discord.ui.select(placeholder="📚 Elige una sección…", options=[discord.SelectOption(label="-")])
    async def seccion(self, i: discord.Interaction, sel: discord.ui.Select):
        clave = sel.values[0]
        await i.response.edit_message(embed=self.portada if clave == "inicio" else self.paginas[clave][3], view=self)


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
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
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
        admin, regalar = bot.es_admin(inter.user), bot.puede_regalar(inter.user)
        await inter.response.send_message(embed=embed_ayuda(bot, admin, regalar), view=VistaAyuda(bot, admin, regalar),
                                          ephemeral=True)

    # ---- /estado
    @tree.command(name="estado", description="¿Está el servidor online y cuánta gente hay?")
    async def estado(inter: discord.Interaction):
        await inter.response.defer()
        try:
            online, maximo, nombres = parsear_list(await bot.rcon.comando(CMD_LIST))
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
            online, maximo, nombres = parsear_list(await bot.rcon.comando(CMD_LIST))
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
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        await inter.response.defer()
        if p := await perfil_o_error(inter, jugador):
            await inter.followup.send(embed=embed_perfil(bot, p))

    # ---- /equipo
    @tree.command(name="equipo", description="Los Pokémon que lleva un jugador en el equipo")
    @app_commands.describe(jugador="Nombre del jugador en Minecraft")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def equipo(inter: discord.Interaction, jugador: str):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        await inter.response.defer()
        if p := await perfil_o_error(inter, jugador):
            await inter.followup.send(embed=embed_equipo(p))

    # ---- /medallas
    @tree.command(name="medallas", description="Medallas y ligas de un jugador")
    @app_commands.describe(jugador="Nombre del jugador en Minecraft", region="Solo una región (opcional)")
    @app_commands.autocomplete(jugador=ac_jugador)
    @app_commands.choices(region=OPCIONES_REGION)
    async def medallas(inter: discord.Interaction, jugador: str, region: app_commands.Choice[str] | None = None):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
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
                ephemeral=bot.privado)
            return
        await inter.response.defer(ephemeral=bot.privado)
        try:
            salida, ok = limpiar(await bot.rcon.comando(comando)) or "(sin respuesta)", True
        except RconError as e:
            salida, ok = str(e), False
        await inter.followup.send(f"`/{comando.lstrip('/')}`\n```\n{salida[:1800]}\n```", ephemeral=bot.privado)
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
        jugador = nombre_jugador(jugador) if jugador else jugador   # sin [etiqueta] ni '🟢 … (conectado)'
        """Comprueba que es admin, lanza el comando por RCON, responde y lo deja registrado."""
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return None
        if jugador is not None and not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return None
        privado = bot.privado and not publico
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=privado)
        try:
            salida = limpiar(await bot.rcon.comando(comando))
        except RconError as e:
            await inter.followup.send(f"No pude contactar con el servidor: {e}", ephemeral=bot.privado)
            return None
        bajo = salida.lower()
        if "no player was found" in bajo:
            await inter.followup.send(f"**{esc(jugador)}** no está conectado." if jugador
                                      else "No hay ningún jugador conectado.", ephemeral=bot.privado)
            return salida
        fallo = any(p in bajo for p in PALABRAS_FALLO)
        if fallo:
            await inter.followup.send(f"❌ {que}: el servidor dijo\n```\n{salida[:1500]}\n```", ephemeral=bot.privado)
        else:
            await inter.followup.send(f"✅ {que}" + (f"\n```\n{salida[:1500]}\n```" if salida else ""),
                                      ephemeral=privado)
        await bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"🔧 **{inter.user}**: {que}\n`/{comando}`" + (f"\n❌ {salida[:300]}" if fallo else ""),
            color=0x8B0000 if fallo else 0x555555, timestamp=datetime.now(timezone.utc)))
        return salida

    class ConfirmarAccion(discord.ui.View):
        """Botones Sí/Cancelar antes de una acción grave. Solo puede pulsarlos quien lanzó el comando."""

        def __init__(self, autor_id: int, pregunta: str, comando: str, que: str, jugador: str | None):
            super().__init__(timeout=60)
            self.autor_id, self.pregunta, self.comando, self.que, self.jugador = autor_id, pregunta, comando, que, jugador

        async def interaction_check(self, i: discord.Interaction) -> bool:
            if i.user.id != self.autor_id:
                await i.response.send_message("Solo quien lanzó el comando puede confirmarlo.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="Sí, hacerlo", style=discord.ButtonStyle.danger)
        async def si(self, i: discord.Interaction, _):
            self.stop()
            await ejecutar(i, self.comando, self.que, self.jugador)
            try:
                await i.edit_original_response(content=f"{self.pregunta}\n✔️ Confirmado por {i.user.mention}", view=None)
            except discord.HTTPException:
                pass

        @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
        async def no(self, i: discord.Interaction, _):
            self.stop()
            await i.response.edit_message(content=f"{self.pregunta}\n✖️ Cancelado, no se ha hecho nada.", view=None)

    async def pedir_confirmacion(inter: discord.Interaction, pregunta: str, comando: str, que: str,
                                 jugador: str | None = None):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        if jugador is not None and not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return
        pregunta = f"⚠️ {pregunta}"
        await inter.response.send_message(pregunta, view=ConfirmarAccion(inter.user.id, pregunta, comando, que, jugador),
                                          ephemeral=bot.privado)

    async def ac_baneado(inter: discord.Interaction, actual: str):
        datos = (await bot.fuente.leer_json("banned-players.json") if bot.fuente else None) or []
        return [app_commands.Choice(name=b["name"], value=b["name"]) for b in datos
                if isinstance(b, dict) and b.get("name") and actual.lower() in b["name"].lower()][:25]

    @admin.command(name="expulsar", description="Echa a un jugador del servidor (puede volver a entrar)")
    @app_commands.describe(jugador="Jugador conectado", motivo="Motivo (lo verá el jugador)")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_expulsar(inter: discord.Interaction, jugador: str, motivo: str | None = None):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        m = texto_seguro(motivo)
        await ejecutar(inter, f"kick {jugador} {m}".strip(), f"Expulsado **{esc(jugador)}**" + (f" ({m})" if m else ""),
                       jugador)

    @admin.command(name="banear", description="Banea a un jugador (no podrá volver a entrar)")
    @app_commands.describe(jugador="Jugador", motivo="Motivo (lo verá el jugador)")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_banear(inter: discord.Interaction, jugador: str, motivo: str | None = None):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        m = texto_seguro(motivo)
        await pedir_confirmacion(inter, f"¿Banear a **{esc(jugador)}**" + (f" ({esc(m)})" if m else "")
                                 + "? No podrá volver a entrar hasta que alguien lo desbanee.",
                                 f"ban {jugador} {m}".strip(), f"Baneado **{esc(jugador)}**" + (f" ({m})" if m else ""),
                                 jugador)

    @admin.command(name="desbanear", description="Quita el ban a un jugador")
    @app_commands.describe(jugador="Jugador baneado")
    @app_commands.autocomplete(jugador=ac_baneado)
    async def adm_desbanear(inter: discord.Interaction, jugador: str):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
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
        jugador = nombre_jugador(jugador) if jugador else jugador   # sin [etiqueta] ni '🟢 … (conectado)'
        if accion.value in ("add", "remove"):
            if not jugador:
                await inter.response.send_message("Pon el jugador que quieres añadir o quitar.", ephemeral=True)
                return
            await ejecutar(inter, f"whitelist {accion.value} {jugador}", f"Whitelist: {accion.name.lower()} **{esc(jugador)}**",
                           jugador)
        elif accion.value == "off":
            await pedir_confirmacion(inter, "¿Desactivar la lista blanca? Podrá entrar **cualquiera** que llegue al "
                                            "servidor.", "whitelist off", f"Whitelist: {accion.name.lower()}")
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
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        if accion.value == "op":
            await pedir_confirmacion(
                inter, f"¿Hacer operador a **{esc(jugador)}**? Tendrá el **nivel 4** ({NIVELES_OP[4][0]}): "
                       f"{NIVELES_OP[4][1]}.", f"op {jugador}",
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
        await inter.response.send_message(embed=e, ephemeral=bot.privado)

    @admin.command(name="modo", description="Cambia el modo de juego de un jugador")
    @app_commands.describe(jugador="Jugador conectado", modo="Modo de juego")
    @app_commands.choices(modo=[app_commands.Choice(name=n, value=v) for n, v in (
        ("Supervivencia", "survival"), ("Creativo", "creative"), ("Aventura", "adventure"), ("Espectador", "spectator"))])
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_modo(inter: discord.Interaction, jugador: str, modo: app_commands.Choice[str]):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        await ejecutar(inter, f"gamemode {modo.value} {jugador}", f"**{esc(jugador)}** en modo {modo.name}", jugador)

    @admin.command(name="teletransportar", description="Lleva a un jugador hasta otro jugador")
    @app_commands.describe(jugador="Jugador que se mueve", destino="Jugador al que va")
    @app_commands.autocomplete(jugador=ac_jugador, destino=ac_jugador)
    async def adm_tp(inter: discord.Interaction, jugador: str, destino: str):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        destino = nombre_jugador(destino)   # sin [etiqueta] ni '🟢 … (conectado)'
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
        jugador = nombre_jugador(jugador) if jugador else jugador   # sin [etiqueta] ni '🟢 … (conectado)'
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
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        await ejecutar(inter, f"pokeheal {jugador}", f"Pokémon de **{esc(jugador)}** curados", jugador)

    @admin.command(name="experiencia", description="Da (o quita, con número negativo) experiencia a un jugador")
    @app_commands.describe(jugador="Jugador conectado", cantidad="Cantidad (negativa para quitar)", tipo="Niveles o puntos")
    @app_commands.choices(tipo=[app_commands.Choice(name="Niveles", value="levels"),
                                app_commands.Choice(name="Puntos", value="points")])
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_xp(inter: discord.Interaction, jugador: str, cantidad: app_commands.Range[int, -10000, 10000],
                     tipo: app_commands.Choice[str] | None = None):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        t = tipo.value if tipo else "levels"
        nombre = tipo.name.lower() if tipo else "niveles"
        await ejecutar(inter, f"experience add {jugador} {cantidad} {t}",
                       f"**{esc(jugador)}**: {cantidad:+} {nombre} de experiencia", jugador)

    @admin.command(name="guardar", description="Guarda el mundo ahora")
    async def adm_guardar(inter: discord.Interaction):
        await ejecutar(inter, "save-all", "Mundo guardado")

    @admin.command(name="estado-fijo", description="Mensaje fijo en un canal con el estado del servidor (se actualiza solo)")
    @app_commands.describe(canal="Canal donde ponerlo (sin canal: quitarlo)")
    async def adm_estado_fijo(inter: discord.Interaction, canal: discord.TextChannel | None = None):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        ef = bot.estado_fijo
        if ef.canal and ef.mensaje:      # el anterior fuera (también si se cambia de canal)
            try:
                viejo = bot.get_channel(ef.canal) or await bot.fetch_channel(ef.canal)
                await viejo.get_partial_message(ef.mensaje).delete()
            except discord.HTTPException:
                pass
        if not canal:
            ef.poner(None, None)
            await inter.followup.send("✅ Mensaje de estado quitado.", ephemeral=True)
            return
        permisos = canal.permissions_for(canal.guild.me)
        if not (permisos.send_messages and permisos.embed_links):
            await inter.followup.send(f"❌ No puedo escribir mensajes con embeds en {canal.mention}.", ephemeral=True)
            return
        ef.poner(canal.id, None)
        try:
            online, maximo, nombres = parsear_list(await bot.rcon.comando(CMD_LIST))
            await bot.actualizar_estado_fijo(True, maximo, nombres)
        except RconError:
            await bot.actualizar_estado_fijo(False, bot._maximo, [])
        fijado = ""
        if ef.mensaje and not permisos.manage_messages:
            fijado = "\n(No he podido fijarlo arriba del canal: me falta el permiso **Gestionar mensajes**; fíjalo tú.)"
        await inter.followup.send(f"✅ Mensaje de estado puesto en {canal.mention}. Se actualiza solo cuando alguien "
                                  f"entra o sale, si se cae el servidor y cada 5 minutos.{fijado}", ephemeral=True)

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
                await i.followup.send(f"No pude contactar con el servidor: {e}", ephemeral=bot.privado)
                return
            donde = f"en <#{bot.canal_estado}>" if bot.canal_estado else "con `/estado`"
            await i.followup.send(f"✅ Servidor parado. Se vuelve a arrancar solo (tarda 1-2 minutos); "
                                  f"avisaré {donde} en cuanto se pueda entrar.", ephemeral=bot.privado)
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
            view=ConfirmarReinicio(inter.user.id, aviso), ephemeral=bot.privado)

    registrar_teams(bot, tree, admin, ac_jugador, ac_categoria)
    registrar_ligas(bot, admin)

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
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        """Comprueba permisos, lanza el comando por RCON, responde y lo deja registrado."""
        if not bot.puede_regalar(inter.user):
            await inter.response.send_message("No tienes permiso para dar cosas a los jugadores.", ephemeral=True)
            return
        if not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=bot.privado)
        try:
            salida = limpiar(await bot.rcon.comando(comando))
        except RconError as e:
            await inter.followup.send(f"No pude contactar con el servidor: {e}", ephemeral=bot.privado)
            return
        bajo = salida.lower()
        if "no player was found" in bajo:
            await inter.followup.send(f"**{esc(jugador)}** no está conectado. Tiene que estar dentro del servidor.",
                                      ephemeral=bot.privado)
            return
        fallo = any(p in bajo for p in PALABRAS_FALLO)
        if fallo:
            await inter.followup.send(f"❌ El servidor no lo aceptó:\n```\n{salida[:1500]}\n```", ephemeral=bot.privado)
        else:
            await inter.followup.send(f"🎁 Dado a **{esc(jugador)}**: {que}" + (f"\n> {salida[:500]}" if salida else ""),
                                      ephemeral=bot.privado)
        await bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"🎁 **{inter.user}** dio a **{esc(jugador)}**: {que}" + (f"\n❌ {salida[:300]}" if fallo else ""),
            color=0x8B0000 if fallo else 0x2E8B57, timestamp=datetime.now(timezone.utc)))

    @dar.command(name="pokemon", description="Da un Pokémon a un jugador conectado, con lo que quieras personalizar")
    @app_commands.describe(
        jugador="Jugador (tiene que estar conectado)",
        especie="Especie del Pokémon",
        nivel="Nivel (1-100, por defecto 5)",
        shiny="¿Shiny?",
        alfa="¿Alfa? (más grande, más fuerte y con un movimiento MT extra)",
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
                          alfa: bool = False,
                          genero: app_commands.Choice[str] | None = None,
                          naturaleza: app_commands.Choice[str] | None = None,
                          habilidad: str | None = None, ivs: str | None = None, evs: str | None = None,
                          movimientos: str | None = None, objeto: str | None = None, pokeball: str | None = None,
                          mote: str | None = None, amistad: app_commands.Range[int, 0, 255] | None = None,
                          forma: str | None = None, tera: app_commands.Choice[str] | None = None,
                          dinamax: app_commands.Range[int, 0, 10] | None = None, gigamax: bool | None = None,
                          tamano: app_commands.Range[float, 0.1, 5.0] | None = None,
                          intercambiable: bool | None = None, extra: str | None = None):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
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
        if alfa:   # Pokémon Alfa (Cobblemon 1.8+): propiedad "alpha"
            props.append("alpha=yes"); detalles.append("🔴 alfa")
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
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        await regalar(inter, jugador, f"cobbledollars give {jugador} {cantidad}", f"**{bot.fmt_dinero(cantidad)}**")

    @dar.command(name="objeto", description="Da objetos a un jugador conectado: elige el mod, luego el objeto")
    @app_commands.describe(jugador="Jugador (tiene que estar conectado)",
                           mod="Mod del objeto (Minecraft, Cobblemon…): filtra la lista de objetos",
                           objeto="Objeto: escribe para buscar por nombre",
                           cantidad="Cuántos (por defecto 1, máximo 2304)")
    @app_commands.autocomplete(jugador=ac_jugador, mod=ac_mod, objeto=ac_objeto)
    async def dar_objeto(inter: discord.Interaction, jugador: str, mod: str, objeto: str,
                         cantidad: app_commands.Range[int, 1, 2304] = 1):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
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
        await inter.response.defer(ephemeral=bot.privado)
        n = await bot.refrescar()
        await inter.followup.send(f"Estadísticas recargadas: {n} jugadores.", ephemeral=bot.privado)

    # ---- /diagnostico (admins)
    @tree.command(name="diagnostico", description="Muestra qué datos encuentra el bot de un jugador (solo admins)")
    @app_commands.describe(jugador="Nombre del jugador en Minecraft")
    @app_commands.autocomplete(jugador=ac_jugador)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def diagnostico(inter: discord.Interaction, jugador: str):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=bot.privado)
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
        await inter.followup.send(cuerpo[:1900], ephemeral=bot.privado)
        # La lista de logros puede ser larga: se manda como archivo
        await inter.followup.send(
            "Lista completa de IDs de logros (para ajustar `[cobbleverse]` en config.toml):",
            file=discord.File(io.BytesIO(lista.encode()), filename=f"logros_{p.nombre}.txt"),
            ephemeral=bot.privado)


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


# =================================================================== ligas
def registrar_ligas(bot: CobbleBot, admin: app_commands.Group):
    regiones = sorted({l.split(":")[-1].split("/")[1] for l in bot.perfiles.reglas.ids_ligas if l.count("/") >= 2},
                      key=lambda r: REGIONES.index(r) if r in REGIONES else 99) if bot.perfiles else []
    opciones = [app_commands.Choice(name=r.capitalize(), value=r) for r in (regiones or REGIONES)][:25]

    def resumen(guild: discord.Guild | None) -> str:
        canal = bot.ligas.canal or bot.canal_avisos
        lineas = [f"📢 Canal de anuncios: {f'<#{canal}>' if canal else '— (no hay: elige uno con `canal`)'}"]
        for o in opciones:
            rid = bot.ligas.roles.get(o.value)
            lineas.append(f"🏆 Liga de {o.name}: {f'<@&{rid}>' if rid else 'sin rol'}")
        return "\n".join(lineas)

    @admin.command(name="ligas", description="Canal donde se anuncian las ligas completadas y rol de Discord de cada liga")
    @app_commands.describe(canal="Canal donde anunciar cada liga completada",
                           region="Liga a la que poner (o quitar) un rol", rol="Rol para quien complete esa liga",
                           quitar_rol="Quitar el rol de esa liga (ya no se pondrá)")
    @app_commands.choices(region=opciones)
    async def adm_ligas(inter: discord.Interaction, canal: discord.TextChannel | None = None,
                        region: app_commands.Choice[str] | None = None, rol: discord.Role | None = None,
                        quitar_rol: bool = False):
        if not bot.es_admin(inter.user) or not inter.guild:
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        if (rol or quitar_rol) and not region:
            await inter.response.send_message("Elige también la `region` de la liga.", ephemeral=True)
            return
        if rol and (rol.is_default() or rol.managed):
            await inter.response.send_message("Ese rol no se puede asignar: elige uno normal.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=bot.privado)
        avisos = ""
        if canal:
            permisos = canal.permissions_for(inter.guild.me)
            if not (permisos.send_messages and permisos.embed_links and permisos.view_channel):
                avisos += f"\n⚠️ No puedo escribir en {canal.mention}: dame permiso para ver el canal, enviar mensajes e insertar enlaces."
            bot.ligas.poner_canal(canal.id)
        if region and (rol or quitar_rol):
            bot.ligas.poner_rol(region.value, None if quitar_rol else rol.id)
            if rol and not quitar_rol:
                falta = bot.puede_gestionar_roles(inter.guild, {region.value: rol})
                if falta:
                    avisos += f"\n⚠️ Para poner el rol, {falta}."
                elif bot.perfiles:
                    # Quien ya tenga esa liga recibe el rol ahora
                    puestos = await bot.poner_roles_ligas(await bot.perfiles.todos())
                    if puestos:
                        avisos += "\n✅ Rol puesto ya a: " + ", ".join(esc(n) for n in puestos)
        await inter.followup.send(resumen(inter.guild) + avisos + "\n\nLos jugadores tienen que tener su Discord "
                                  "vinculado (`/team vincular`) para recibir el rol. El aviso sale en unos minutos "
                                  "(cuando el bot relee los datos; `/actualizar` lo hace al momento).", ephemeral=bot.privado)


# =================================================================== teams
def componente_copiar(texto: str) -> dict:
    """Texto del chat de Minecraft que se copia al portapapeles al hacer clic (formato JSON de 1.21.1)."""
    return {"text": texto, "color": "green", "bold": True, "underlined": True,
            "clickEvent": {"action": "copy_to_clipboard", "value": texto},
            "hoverEvent": {"action": "show_text", "contents": {"text": "Haz clic para copiar", "color": "yellow"}}}


def registrar_teams(bot: CobbleBot, tree: app_commands.CommandTree, admin: app_commands.Group,
                    ac_jugador, ac_categoria):
    SIN_TEAMS = ("Todavía no hay teams. Se crean **dentro del juego** con FTB Teams (botón de FTB Teams en el "
                 "inventario) y salen aquí en unos minutos, o al momento con `/team actualizar`.")

    async def ac_team(inter: discord.Interaction, actual: str):
        a = actual.lower()
        return [app_commands.Choice(name=t.titulo[:100], value=tid) for tid, t in bot.teams.items()
                if a in t.nombre.lower()][:25]

    async def team_o_error(inter: discord.Interaction, team: str) -> Team | None:
        t = bot.teams.get(team) or next((t for t in bot.teams.values() if t.nombre.lower() == team.lower()), None)
        if not t:
            msg = SIN_TEAMS if not bot.teams else f"No existe el team «{esc(team)}». Elígelo de la lista."
            await inter.response.send_message(msg, ephemeral=True)
        return t

    grupo = app_commands.Group(name="team", description="Teams del servidor (FTB Teams): vincula tu cuenta y compite",
                               guild_only=True)

    async def ac_vincular(inter: discord.Interaction, actual: str):
        """Los jugadores de siempre, menos los que ya tienen su Discord vinculado."""
        return [c for c in await ac_jugador(inter, actual) if not bot.registro.discord_de(c.value)]

    @grupo.command(name="vincular", description="Une tu cuenta de Discord con tu jugador de Minecraft (tienes que estar conectado)")
    @app_commands.describe(jugador="Tu nombre en Minecraft")
    @app_commands.autocomplete(jugador=ac_vincular)
    async def team_vincular(inter: discord.Interaction, jugador: str):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        if not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=bot.privado)
        _, _, conectados = parsear_list(await bot.rcon.comando(CMD_LIST))
        nombre = next((n for n in conectados if n.lower() == jugador.lower()), None)
        if not nombre:
            await inter.followup.send(f"**{esc(jugador)}** no está conectado. Entra al servidor y vuelve a probar: "
                                      "te mandaré un código al chat del juego.", ephemeral=bot.privado)
            return
        codigo = bot.registro.nuevo_codigo(inter.user.id, nombre)
        await bot.rcon.comando(bot.cmd_mensaje(
            f"&eCódigo para vincular tu Discord (&f{inter.user}&e): ", nombre, privado=True,
            estilo={"color": "yellow"}, extra=[
                componente_copiar(codigo),
                {"text": " ← haz clic para copiarlo · caduca en 5 min. Pégalo en /team confirmar en Discord.",
                 "color": "gray"}]))
        await inter.followup.send(f"📨 Te he mandado un código al **chat del juego**. Escríbelo aquí con "
                                  f"`/team confirmar codigo:…` (caduca en 5 minutos).", ephemeral=bot.privado)

    @grupo.command(name="confirmar", description="Termina de vincular tu cuenta con el código que te llegó al juego")
    @app_commands.describe(codigo="El código de 6 cifras del chat del juego")
    async def team_confirmar(inter: discord.Interaction, codigo: str):
        jugador = bot.registro.comprobar_codigo(inter.user.id, codigo)
        if not jugador:
            await inter.response.send_message("❌ Código incorrecto o caducado. Pide otro con `/team vincular`.",
                                              ephemeral=True)
            return
        await inter.response.defer(ephemeral=bot.privado)
        cache = (await bot.fuente.leer_json("usercache.json") if bot.fuente else None) or []
        uuid_mc = next((c.get("uuid") for c in cache if c.get("name", "").lower() == jugador.lower()), None)
        async with bot.registro.lock:
            bot.registro.vincular(inter.user.id, jugador, uuid_mc)
        log.info("%s vinculado con el jugador %s", inter.user, jugador)
        await bot.sincronizar_teams()   # si ya está en un team de FTB, recibe su rol
        if bot.perfiles:
            await bot.poner_roles_ligas(await bot.perfiles.todos())   # y los roles de las ligas que ya tenga
        t = bot.team_de_discord(inter.user.id)
        extra = (f" Estás en el team **{esc(t.titulo)}**: ya tienes su rol." if t else
                 " Para tener team, créalo o únete a uno **dentro del juego** con FTB Teams.")
        await inter.followup.send(f"✅ Tu Discord queda vinculado con **{esc(jugador)}**.{extra}", ephemeral=bot.privado)

    @grupo.command(name="actualizar", description="Copia ya a Discord los cambios de teams hechos en el juego")
    async def team_actualizar(inter: discord.Interaction):
        espera = 60 - (time.time() - bot._ultima_actualizacion_teams)
        if espera > 0 and not bot.es_admin(inter.user):
            await inter.response.send_message(f"Se acaba de actualizar. Prueba otra vez en {int(espera) + 1} s.",
                                              ephemeral=True)
            return
        bot._ultima_actualizacion_teams = time.time()
        await inter.response.defer(ephemeral=bot.privado)
        await bot.sincronizar_teams(guardar_antes=True)
        lista = ", ".join(f"**{esc(t.titulo)}** ({len(t.miembros)})" for t in bot.teams.values())
        await inter.followup.send(f"🔄 Teams al día: {lista}" if lista else SIN_TEAMS, ephemeral=bot.privado)

    @grupo.command(name="info", description="Miembros de cada team (o de uno)")
    @app_commands.describe(team="Solo este team (opcional)")
    @app_commands.autocomplete(team=ac_team)
    async def team_info(inter: discord.Interaction, team: str | None = None):
        uno = await team_o_error(inter, team) if team else None
        if team and not uno:
            return
        if not bot.teams:
            await inter.response.send_message(SIN_TEAMS, ephemeral=True)
            return
        teams = [uno] if uno else list(bot.teams.values())
        e = discord.Embed(title="🛡️ Teams" if not uno else f"🛡️ {uno.titulo}",
                          color=uno.color_int if uno else COLOR)
        if uno:
            rol = bot.rol_de(inter.guild, uno) if inter.guild else None
            lider = bot._nombres.get(uno.lider, "—")
            e.description = (f"Líder: **{esc(lider)}** · Rol: {rol.mention if rol else '—'} · "
                             f"Color: `{uno.color}` · En el chat: `[{uno.etiqueta or uno.nombre}] Jugador` · "
                             f"Fuego amigo: **{'sí' if uno.fuego_amigo else 'no'}**")
        for t in teams[:25]:
            filas = []
            for u in t.miembros:
                n = bot._nombres.get(u, u[:8])
                did = bot.registro.discord_de(n)
                filas.append(f"{'👑 ' if u == t.lider else ''}{esc(n)}" + (f" · <@{did}>" if did and uno else ""))
            e.add_field(name=f"{t.titulo} ({len(t.miembros)})",
                        value=("\n" if uno else ", ").join(filas)[:1024] or "Sin miembros", inline=not uno)
        e.set_footer(text="Los teams se gestionan en el juego con FTB Teams · /team actualizar para verlos ya")
        await inter.response.send_message(embed=e, ephemeral=bot.privado)

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
            await inter.response.send_message(SIN_TEAMS, ephemeral=True)
            return
        await inter.response.defer()
        reg = region.value if (region and cat.por_region) else None
        perfiles = {p.uuid.lower(): p for p in await bot.perfiles.todos()}
        filas = []
        for t in bot.teams.values():
            miembros = [perfiles[u] for u in t.miembros if u in perfiles]
            total = sum(cat.valor(p, reg) for p in miembros)
            mejor = max(miembros, key=lambda p: cat.valor(p, reg), default=None)
            filas.append((total, t, len(t.miembros), mejor))
        filas.sort(key=lambda f: f[0], reverse=True)
        medallas = ["🥇", "🥈", "🥉"]
        titulo = cat.titulo + (f" de {reg.capitalize()}" if reg else "")
        e = discord.Embed(title=f"🛡️ Ranking de teams — {titulo}", color=COLOR)
        e.description = "\n".join(
            f"{medallas[i] if i < 3 else f'**{i + 1}.**'} **{esc(t.titulo)}** — {cat.formato(total)}"
            f" · {n} miembro{'s' if n != 1 else ''}"
            + (f" · MVP: {esc(mejor.nombre)}" if mejor and cat.valor(mejor, reg) > 0 else "")
            for i, (total, t, n, mejor) in enumerate(filas))
        e.set_footer(text="Suma de todos los miembros de cada team de FTB")
        await inter.followup.send(embed=e)

    # ---- panel del team: el dueño y los oficiales de FTB (y los admins, de cualquier team)
    def uuid_de_usuario(did) -> str | None:
        u = bot.registro.uuid_de(did)
        if not u:
            j = (bot.registro.jugador_de(did) or "").lower()
            u = next((x for x, n in bot._nombres.items() if n.lower() == j), None) if j else None
        return u

    def puede_panel(usuario, t: Team) -> bool:
        return bot.es_admin(usuario) or t.puede_gestionar(uuid_de_usuario(usuario.id))

    def embed_panel(t: Team) -> discord.Embed:
        e = discord.Embed(title=f"⚙️ Panel del team {t.titulo}", color=t.color_int)
        e.add_field(name="Nombre", value=esc(t.nombre))
        e.add_field(name="Color", value=f"`{t.color}`")
        e.add_field(name="En el chat", value=f"`[{t.etiqueta or t.nombre}] Jugador`")
        e.add_field(name="Emoji", value=t.emoji or "—")
        e.add_field(name="Fuego amigo", value="sí" if t.fuego_amigo else "no")
        filas = [("👑 " if u == t.lider else "⭐ " if u in t.oficiales else "") + esc(bot._nombres.get(u, u[:8]))
                 for u in t.miembros]
        e.add_field(name=f"Miembros ({len(t.miembros)})", value="\n".join(filas)[:1024] or "—", inline=False)
        invs = bot.registro.invitaciones_de_team(t.id)
        if invs:
            e.add_field(name=f"📨 Invitaciones sin responder ({len(invs)})",
                        value=", ".join(f"<@{x['para']}>" for x in invs)[:1024], inline=False)
        pend = [p for p in bot.registro.pendientes if p.get("team") == t.id]
        if pend:
            e.add_field(name="⏳ Al conectarse al servidor",
                        value="\n".join(f"{esc(p['jugador'])}: {'entra' if p['accion'] == 'unir' else 'sale'}"
                                        for p in pend)[:1024], inline=False)
        e.set_footer(text="👑 dueño · ⭐ oficial · El nombre y el color se cambian también en FTB Teams (en el juego)")
        return e

    ftb = bot.ftb_cmd

    async def pedir(p: dict) -> tuple[bool, str | None]:
        """(ya hecho en el juego, error). Si no está conectado, queda pendiente para cuando entre."""
        estado, error = await bot.pedir_en_juego(p)
        return estado == "hecho", error

    # ---- invitaciones desde Discord: botones que siguen funcionando aunque el bot se reinicie
    class BotonInvitacion(discord.ui.DynamicItem[discord.ui.Button],
                          template=r"cbteam:inv:(?P<si>[01]):(?P<tid>[0-9a-f-]{36}):(?P<para>\d+)"):
        def __init__(self, si: bool, tid: str, para: str, etiqueta: str | None = None):
            super().__init__(discord.ui.Button(
                label=etiqueta or ("Aceptar" if si else "Rechazar"), emoji="✅" if si else "✖️",
                style=discord.ButtonStyle.success if si else discord.ButtonStyle.secondary,
                custom_id=f"cbteam:inv:{int(si)}:{tid}:{para}"))
            self.si, self.tid, self.para = si, tid, para

        @classmethod
        async def from_custom_id(cls, interaction, item, match):
            return cls(match["si"] == "1", match["tid"], match["para"])

        async def callback(self, i: discord.Interaction):
            await responder_invitacion(i, self.tid, self.para, self.si)

    bot.add_dynamic_items(BotonInvitacion)

    def vista_invitacion(tid: str, para) -> discord.ui.View:
        v = discord.ui.View(timeout=None)
        v.add_item(BotonInvitacion(True, tid, str(para)))
        v.add_item(BotonInvitacion(False, tid, str(para)))
        return v

    async def responder_invitacion(i: discord.Interaction, tid: str, para: str, si: bool):
        if str(i.user.id) != para:
            await i.response.send_message("Esta invitación no es para ti.", ephemeral=True)
            return
        inv = next((x for x in bot.registro.invitaciones_para(para) if x["team"] == tid), None)
        t = bot.teams.get(tid)
        if not inv or not t:
            if inv:
                async with bot.registro.lock:
                    bot.registro.quitar_invitacion(tid, para)
            await i.response.send_message("Esta invitación ya no vale (caducada, cancelada, ya respondida o el team "
                                          "ya no existe).", ephemeral=True)
            return
        if not si:
            async with bot.registro.lock:
                bot.registro.quitar_invitacion(tid, para)
            await i.response.send_message(f"Has rechazado la invitación a **{esc(t.titulo)}**.", ephemeral=True)
            await quitar_botones(i)
            return
        jugador = bot.registro.jugador_de(i.user.id)
        if not jugador:
            await i.response.send_message("Antes tienes que vincular tu cuenta de Minecraft: `/team vincular` "
                                          "(estando conectado al servidor). La invitación sigue en pie.", ephemeral=True)
            return
        actual = bot.team_de_discord(i.user.id)
        if actual and actual.id == tid:
            async with bot.registro.lock:
                bot.registro.quitar_invitacion(tid, para)
            await i.response.send_message(f"Ya estás en **{esc(t.titulo)}**.", ephemeral=True)
            return
        await i.response.defer(ephemeral=True, thinking=True)
        async with bot.registro.lock:
            bot.registro.quitar_invitacion(tid, para)
        hecho, error = await pedir({"accion": "unir", "discord": str(i.user.id), "jugador": jugador,
                                                 "team": tid})
        if error:
            await i.followup.send(f"⚠️ No he podido meterte en **{esc(t.titulo)}**: {error}", ephemeral=True)
            return
        cambio = f" (saldrás de **{esc(actual.titulo)}**)" if actual else ""
        await i.followup.send(f"✅ ¡Ya estás en **{esc(t.titulo)}**! Tienes su rol y su etiqueta en el juego." if hecho else
                              f"✅ Invitación aceptada. Entrarás en **{esc(t.titulo)}** en cuanto te conectes al "
                              f"servidor{cambio}.", ephemeral=True)
        await registrar_cambio(f"aceptó la invitación al team **{esc(t.titulo)}**", i.user)
        await quitar_botones(i)

    async def quitar_botones(i: discord.Interaction):
        """La invitación ya está respondida: fuera los botones del mensaje (si se puede)."""
        try:
            if i.message and i.message.author == bot.user:
                await i.message.edit(view=None)
        except discord.HTTPException:
            pass

    async def invitar_a(i: discord.Interaction, t: Team, miembro) -> str:
        """Invita desde Discord. Devuelve el texto para quien invita."""
        if miembro.bot:
            return "❌ No se puede invitar a un bot."
        if miembro.id == i.user.id:
            return "❌ No te puedes invitar a ti mismo."
        su_team = bot.team_de_discord(miembro.id)
        if su_team and su_team.id == t.id:
            return f"❌ {miembro.mention} ya está en el team."
        async with bot.registro.lock:
            bot.registro.invitar(t.id, i.user.id, miembro.id)
        texto = (f"📨 **{i.user.display_name}** te invita al team **{t.titulo}** de COBBLEVERSE."
                 + (f"\n(Ahora estás en **{su_team.titulo}**: si aceptas, te cambias de team.)" if su_team else "")
                 + ("" if bot.registro.jugador_de(miembro.id) else
                    "\nPara aceptar, antes vincula tu cuenta de Minecraft con `/team vincular` en el servidor de Discord.")
                 + "\nSi no estás conectado al juego, entrarás al team en cuanto te conectes. Caduca en 7 días.")
        e = discord.Embed(description=texto, color=t.color_int)
        donde = "por mensaje privado"
        try:
            await miembro.send(embed=e, view=vista_invitacion(t.id, miembro.id))
        except discord.HTTPException:
            donde = "en este canal (tiene los mensajes privados cerrados)"
            try:
                await i.channel.send(content=miembro.mention, embed=e, view=vista_invitacion(t.id, miembro.id))
            except (discord.HTTPException, AttributeError):
                donde = "sin aviso (no he podido escribirle): que use `/team invitaciones`"
        await registrar_cambio(f"invitó a {miembro.mention} al team **{esc(t.titulo)}**", i.user)
        return f"📨 Invitación enviada a {miembro.mention} {donde}. También la ve con `/team invitaciones`."

    class ModalNombreColor(discord.ui.Modal, title="Nombre del team"):
        def __init__(self, panel: "PanelTeam", t: Team):
            super().__init__()
            self.panel = panel
            self.nombre = discord.ui.TextInput(label="Nombre", default=t.nombre[:32], min_length=1, max_length=32)
            self.add_item(self.nombre)

        async def on_submit(self, i: discord.Interaction):
            t = self.panel.team()
            nombre = nombre_team_valido(self.nombre.value)
            if not t or not nombre:
                await i.response.send_message("❌ " + ("Ese team ya no existe." if not t else "Nombre vacío."),
                                              ephemeral=True)
                return
            await i.response.defer()
            f = await ftb(comando_ftb(t, "display_name", nombre)) if nombre != t.nombre else None
            await bot.sincronizar_teams(guardar_antes=True)
            if not f:
                await registrar_cambio(f"cambió el nombre del team a **{esc(nombre)}** desde el panel", i.user)
            await self.panel.refrescar(i, f"⚠️ {f}" if f else "✅ Guardado en FTB Teams y en Discord.")

    # ---- selector de color: lista de colores con su cuadrado, o #hex a mano, con vista previa del color
    def vista_previa(color: str, t: Team) -> tuple[discord.Embed, discord.File]:
        e = discord.Embed(title=f"🎨 Vista previa: {color}", color=int(color[1:], 16),
                          description=f"Así se verá el color de **{esc(t.titulo)}** (rol de Discord y `[etiqueta]` en "
                                      "el juego). Si te gusta, pulsa **Aplicar**.")
        e.set_thumbnail(url="attachment://color.png")
        return e, discord.File(io.BytesIO(png_color(color)), filename="color.png")

    class ModalHex(discord.ui.Modal, title="Color en hexadecimal"):
        hex_ = discord.ui.TextInput(label="Color (#RRGGBB)", min_length=6, max_length=7,
                                    placeholder="Por ejemplo #FF5555 (búscalo con el botón 🌐)")

        def __init__(self, selector: "SelectorColor"):
            super().__init__()
            self.selector = selector

        async def on_submit(self, i: discord.Interaction):
            color = color_de(self.hex_.value)
            if not color:
                await i.response.send_message("❌ Ese color no vale: tiene que ser como #FF5555.", ephemeral=True)
                return
            await self.selector.mostrar(i, color)

    class SelectorColor(discord.ui.View):
        def __init__(self, panel: "PanelTeam", autor_id: int):
            super().__init__(timeout=600)
            self.panel, self.autor_id, self.color = panel, autor_id, None
            self.lista.options = [discord.SelectOption(label=n.capitalize(), value=h, description=h,
                                                       emoji=EMOJI_COLOR.get(n)) for n, h in COLORES_TEAM.items()]
            self.add_item(discord.ui.Button(label="Buscar un color", emoji="🌐", row=1,
                                            url="https://htmlcolorcodes.com/color-picker/"))

        async def interaction_check(self, i: discord.Interaction) -> bool:
            t = self.panel.team()
            if i.user.id != self.autor_id or not t or not puede_panel(i.user, t):
                await i.response.send_message("❌ No puedes cambiar el color de este team.", ephemeral=True)
                return False
            return True

        async def mostrar(self, i: discord.Interaction, color: str):
            self.color = color
            self.aplicar.disabled = False
            e, archivo = vista_previa(color, self.panel.team())
            await i.response.edit_message(content=None, embed=e, attachments=[archivo], view=self)

        @discord.ui.select(placeholder="🎨 Elige un color…", row=0, options=[discord.SelectOption(label="-")])
        async def lista(self, i: discord.Interaction, sel: discord.ui.Select):
            await self.mostrar(i, sel.values[0])

        @discord.ui.button(label="Escribir #hex", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
        async def escribir(self, i: discord.Interaction, _):
            await i.response.send_modal(ModalHex(self))

        @discord.ui.button(label="Aplicar", emoji="✅", style=discord.ButtonStyle.success, row=1, disabled=True)
        async def aplicar(self, i: discord.Interaction, _):
            t = self.panel.team()
            await i.response.edit_message(content="⏳ Aplicando…", view=None)
            f = await ftb(comando_ftb(t, "color", self.color)) if self.color != t.color else None
            await bot.sincronizar_teams(guardar_antes=True)
            if not f:
                await registrar_cambio(f"cambió el color del team **{esc(t.titulo)}** a `{self.color}`", i.user)
            await i.edit_original_response(content=f"⚠️ {f}" if f else f"✅ Color `{self.color}` aplicado en FTB Teams "
                                           "y en Discord.")
            self.stop()
            if self.panel.inter:     # el panel se pone al día con el color nuevo
                await self.panel.refrescar(self.panel.inter)

    class ModalEtiqueta(discord.ui.Modal, title="Etiqueta y emoji"):
        def __init__(self, panel: "PanelTeam", t: Team):
            super().__init__()
            self.panel = panel
            # Discord: label de 45 caracteres como mucho; la explicación va en el placeholder
            self.etiqueta = discord.ui.TextInput(label="Etiqueta [ ] del chat", default=t.etiqueta, required=False,
                                                 max_length=24, placeholder="Vacío = el nombre del team")
            self.emoji = discord.ui.TextInput(label="Emoji para Discord", default=t.emoji, required=False,
                                              max_length=8, placeholder="Vacío = sin emoji")
            self.add_item(self.etiqueta)
            self.add_item(self.emoji)

        async def on_submit(self, i: discord.Interaction):
            t = self.panel.team()
            if not t:
                await i.response.send_message("❌ Ese team ya no existe.", ephemeral=True)
                return
            await i.response.defer()
            t.etiqueta = etiqueta_de(self.etiqueta.value) if self.etiqueta.value.strip() else ""
            t.emoji = self.emoji.value.strip()
            async with bot.registro.lock:
                bot.registro.guardar_extras(t)
            await bot.sincronizar_teams()
            await registrar_cambio(f"cambió etiqueta/emoji del team **{esc(t.titulo)}** desde el panel", i.user)
            await self.panel.refrescar(i, "✅ Guardado.")

    class SelectEchar(discord.ui.Select):
        def __init__(self, opciones: list[discord.SelectOption]):
            vacio = not opciones     # se ve siempre; si no hay a quién echar, sale desactivado y lo explica
            super().__init__(placeholder="👢 No hay nadie a quien puedas echar" if vacio else
                             "👢 Echar a un miembro del team…", row=1, disabled=vacio,
                             options=opciones or [discord.SelectOption(label="—", value="-")])

        async def callback(self, i: discord.Interaction):
            panel: PanelTeam = self.view
            t = panel.team()
            uuid = self.values[0]
            nombre = bot._nombres.get(uuid)
            if not t or uuid not in t.miembros or not nombre or not panel.puede_echar(i.user, t, uuid):
                await i.response.send_message("❌ No puedes echar a ese jugador.", ephemeral=True)
                return
            await i.response.defer()
            f = await ftb(f"ftbteams force-remove {t.id_corto} {nombre}")
            aviso = f"✅ {esc(nombre)} ya no está en el team."
            if f:
                # Puede que FTB no deje si no está conectado: entonces se hace en cuanto entre
                hecho, f2 = await pedir({"accion": "echar", "discord": bot.registro.discord_de(nombre) or "",
                                                      "jugador": nombre, "team": t.id})
                aviso = (f"⚠️ No he podido echar a {esc(nombre)}: {f2 or f}" if hecho and f2 else
                         f"⏳ {esc(nombre)} no está conectado: lo saco del team en cuanto entre al servidor." if not hecho
                         else aviso)
            else:
                await bot.sincronizar_teams(guardar_antes=True)
            if not aviso.startswith("⚠️"):
                await registrar_cambio(f"echó a **{esc(nombre)}** del team **{esc(t.titulo)}** desde el panel", i.user)
            await panel.refrescar(i, aviso)

    class SelectInvitar(discord.ui.UserSelect):
        def __init__(self):
            super().__init__(placeholder="📨 Invitar a alguien de Discord…", row=2)

        async def callback(self, i: discord.Interaction):
            panel: PanelTeam = self.view
            t = panel.team()
            await i.response.defer()
            texto = await invitar_a(i, t, self.values[0]) if t else "❌ Ese team ya no existe."
            await panel.refrescar(i, texto)

    class SelectMeter(discord.ui.UserSelect):
        """Solo admins: mete a alguien en el team sin invitación (si no está conectado, al entrar)."""
        def __init__(self):
            super().__init__(placeholder="➕ (Admin) Meter a alguien directamente…", row=4)

        async def callback(self, i: discord.Interaction):
            panel: PanelTeam = self.view
            t = panel.team()
            m = self.values[0]
            if not bot.es_admin(i.user):
                await i.response.send_message("Solo los admins pueden meter gente sin invitación.", ephemeral=True)
                return
            jugador = bot.registro.jugador_de(m.id)
            if not t or not jugador:
                await i.response.send_message("❌ " + ("Ese team ya no existe." if not t else
                                              f"{m.mention} no tiene el Discord vinculado con Minecraft: vincúlalo antes "
                                              "con `/admin vincular`."), ephemeral=True)
                return
            await i.response.defer()
            hecho, error = await pedir({"accion": "unir", "discord": str(m.id), "jugador": jugador, "team": t.id})
            if not error:
                async with bot.registro.lock:
                    bot.registro.quitar_invitacion(t.id, m.id)
                await registrar_cambio(f"metió a {m.mention} ({esc(jugador)}) en el team **{esc(t.titulo)}** "
                                       "desde el panel", i.user)
            await panel.refrescar(i, f"⚠️ No he podido meter a {esc(jugador)}: {error}" if error else
                                  f"✅ {esc(jugador)} ya está en el team." if hecho else
                                  f"⏳ {esc(jugador)} no está conectado: entra al team en cuanto se conecte.")

    class SelectCancelarInvitacion(discord.ui.Select):
        def __init__(self, invitaciones: list[dict]):
            opciones = []
            for x in invitaciones[:25]:
                m = bot.get_user(int(x["para"]))
                opciones.append(discord.SelectOption(label=(m.display_name if m else x["para"])[:100], value=x["para"]))
            super().__init__(placeholder="✖️ Cancelar una invitación…", options=opciones, row=3)

        async def callback(self, i: discord.Interaction):
            panel: PanelTeam = self.view
            await i.response.defer()
            async with bot.registro.lock:
                bot.registro.quitar_invitacion(panel.tid, self.values[0])
            await panel.refrescar(i, f"✖️ Invitación a <@{self.values[0]}> cancelada.")

    class ConfirmarDisolver(discord.ui.View):
        def __init__(self, panel: "PanelTeam"):
            super().__init__(timeout=60)
            self.panel = panel

        @discord.ui.button(label="Sí, disolver el team", style=discord.ButtonStyle.danger)
        async def si(self, i: discord.Interaction, _):
            t = self.panel.team()
            if not t or not (bot.es_admin(i.user) or uuid_de_usuario(i.user.id) == t.lider):
                await i.response.edit_message(content="❌ No puedes disolver este team.", view=None)
                return
            await i.response.edit_message(content="⏳ Disolviendo…", view=None)
            f = await ftb(f"ftbteams force-disband {t.id_corto}")
            if not f:
                await bot.sincronizar_teams(guardar_antes=True)
                await registrar_cambio(f"disolvió el team **{esc(t.titulo)}** desde el panel", i.user)
            await i.edit_original_response(content=f"⚠️ No he podido disolverlo: {f}" if f else
                                           f"🗑️ El team **{esc(t.titulo)}** ya no existe.")

        @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
        async def no(self, i: discord.Interaction, _):
            await i.response.edit_message(content="No se ha disuelto nada.", view=None)

    class PanelTeam(discord.ui.View):
        def __init__(self, autor, tid: str, inter: discord.Interaction | None = None):
            super().__init__(timeout=900)
            self.autor_id, self.tid = autor.id, tid
            self.inter = inter      # la interacción cuyo mensaje es este panel (para ponerlo al día desde fuera)
            t = self.team()
            if not t:
                return
            self.fuego.label = f"Fuego amigo: {'sí' if t.fuego_amigo else 'no'}"
            self.fuego.style = discord.ButtonStyle.danger if t.fuego_amigo else discord.ButtonStyle.secondary
            echables = [u for u in t.miembros if u in bot._nombres and self.puede_echar(autor, t, u)]
            self.add_item(SelectEchar([discord.SelectOption(label=bot._nombres[u], value=u,
                                                            emoji="⭐" if u in t.oficiales else None)
                                       for u in echables[:25]]))
            self.add_item(SelectInvitar())
            invitaciones = bot.registro.invitaciones_de_team(tid)
            if invitaciones:
                self.add_item(SelectCancelarInvitacion(invitaciones))
            if bot.es_admin(autor):
                self.add_item(SelectMeter())
            if not (bot.es_admin(autor) or uuid_de_usuario(autor.id) == t.lider):
                self.remove_item(self.disolver)     # disolver: solo el dueño (o un admin)

        def team(self) -> Team | None:
            return bot.teams.get(self.tid)

        def puede_echar(self, usuario, t: Team, uuid: str) -> bool:
            yo = uuid_de_usuario(usuario.id)
            if uuid == t.lider or uuid == yo:
                return False     # al dueño no se le echa; uno mismo sale desde el juego
            return bot.es_admin(usuario) or yo == t.lider or uuid not in t.oficiales

        async def interaction_check(self, i: discord.Interaction) -> bool:
            t = self.team()
            if i.user.id != self.autor_id:
                await i.response.send_message("Este panel es de otra persona: abre el tuyo con `/team panel`.",
                                              ephemeral=True)
                return False
            if not t or not puede_panel(i.user, t):
                await i.response.send_message("❌ Ese team ya no existe o ya no lo gestionas.", ephemeral=True)
                return False
            return True

        async def refrescar(self, i: discord.Interaction, aviso: str = ""):
            self.stop()
            t = self.team()
            try:
                if t:
                    await i.edit_original_response(content=aviso or None, embed=embed_panel(t),
                                                   view=PanelTeam(i.user, self.tid, i))
                else:
                    await i.edit_original_response(content="Este team ya no existe.", embed=None, view=None)
            except discord.HTTPException:
                pass

        @discord.ui.button(label="Nombre", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
        async def nombre_color(self, i: discord.Interaction, _):
            await i.response.send_modal(ModalNombreColor(self, self.team()))

        @discord.ui.button(label="Color", emoji="🎨", style=discord.ButtonStyle.primary, row=0)
        async def color(self, i: discord.Interaction, _):
            t = self.team()
            e, archivo = vista_previa(t.color, t)
            e.title, e.description = f"🎨 Color actual: {t.color}", "Elige uno de la lista o escribe tu #hex."
            await i.response.send_message(embed=e, file=archivo, view=SelectorColor(self, i.user.id), ephemeral=True)

        @discord.ui.button(label="Etiqueta y emoji", emoji="🏷️", style=discord.ButtonStyle.primary, row=0)
        async def etiqueta(self, i: discord.Interaction, _):
            await i.response.send_modal(ModalEtiqueta(self, self.team()))

        @discord.ui.button(label="Fuego amigo", emoji="⚔️", row=0)
        async def fuego(self, i: discord.Interaction, _):
            t = self.team()
            await i.response.defer()
            t.fuego_amigo = not t.fuego_amigo
            async with bot.registro.lock:
                bot.registro.guardar_extras(t)
            await bot.sincronizar_teams()
            await registrar_cambio(f"puso el fuego amigo del team **{esc(t.titulo)}** en "
                                   f"**{'sí' if t.fuego_amigo else 'no'}** desde el panel", i.user)
            await self.refrescar(i, "✅ Guardado.")

        @discord.ui.button(label="Disolver", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
        async def disolver(self, i: discord.Interaction, _):
            t = self.team()
            await i.response.send_message(f"⚠️ ¿Seguro que quieres **disolver {esc(t.titulo)}**? Todos sus miembros se "
                                          "quedan sin team y se borra su rol de Discord.",
                                          view=ConfirmarDisolver(self), ephemeral=True)

    # ---- crear, invitar y salir desde Discord (se hace en el juego; si no estás conectado, al entrar)
    async def ac_color(inter: discord.Interaction, actual: str):
        a = actual.lower()
        return [app_commands.Choice(name=f"{EMOJI_COLOR.get(n, '')} {n} ({h})".strip(), value=h)
                for n, h in COLORES_TEAM.items() if a in n][:25]

    @grupo.command(name="crear", description="Crea un team (en FTB Teams). Si no estás conectado, se crea al entrar")
    @app_commands.describe(nombre="Nombre del team", color="Color: elige uno o escribe #RRGGBB")
    @app_commands.autocomplete(color=ac_color)
    async def team_crear(inter: discord.Interaction, nombre: app_commands.Range[str, 1, 32], color: str | None = None):
        jugador = bot.registro.jugador_de(inter.user.id)
        if not jugador:
            await inter.response.send_message("Primero vincula tu cuenta de Minecraft con `/team vincular`.",
                                              ephemeral=True)
            return
        nombre = nombre_team_valido(nombre)
        hex_ = color_de(color) if color else None
        if not nombre or (color and not hex_):
            await inter.response.send_message("❌ " + ("Nombre no válido." if not nombre else
                                              "Color no válido: elígelo de la lista o usa #RRGGBB."), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True, thinking=True)
        # ¿Ya está en un team? Se mira en el juego en directo (si está conectado) y en lo que sabe el bot
        party = await bot.party_de(jugador)
        actual = bot.team_de_discord(inter.user.id)
        if party or (party is False and actual):
            await inter.followup.send(f"❌ Ya estás en el team **{esc(party or actual.titulo)}**: solo se puede estar "
                                      "en uno. Sal antes con `/team salir`.", ephemeral=True)
            return
        if any(t.nombre.lower() == nombre.lower() for t in bot.teams.values()):
            await inter.followup.send(f"❌ Ya existe un team llamado **{esc(nombre)}**.", ephemeral=True)
            return
        antes = bot.registro.pendiente_de(jugador)
        estado, error = await bot.pedir_en_juego({"accion": "crear", "discord": str(inter.user.id), "jugador": jugador,
                                                 "nombre": nombre, "color": hex_})
        if error:
            await inter.followup.send(f"⚠️ No he podido crear el team: {error}", ephemeral=True)
            return
        await registrar_cambio(f"pidió crear el team **{esc(nombre)}** desde Discord", inter.user)
        reemplaza = (f"\n(Sustituye a lo que tenías pendiente: {bot.texto_accion(antes)}.)"
                     if antes and antes.get("nombre") != nombre else "")
        await inter.followup.send(
            f"🎮 Te he mandado un mensaje al **chat del juego**: haz clic en **[Crear el team {esc(nombre)}]** y "
            "listo. Después te pongo el color, el rol de Discord y la etiqueta." if estado == "clic" else
            f"✅ Team **{esc(nombre)}** listo." if estado == "hecho" else
            f"⏳ No estás conectado: cuando entres al servidor te saldrá en el chat un botón para crear "
            f"**{esc(nombre)}**.", ephemeral=True)
        if reemplaza:
            await inter.followup.send(reemplaza.strip(), ephemeral=True)

    @grupo.command(name="invitar", description="Invita a alguien de Discord a tu team (dueño y oficiales)")
    @app_commands.describe(usuario="A quién invitas")
    async def team_invitar(inter: discord.Interaction, usuario: discord.Member):
        t = bot.team_de_discord(inter.user.id)
        if not t or not puede_panel(inter.user, t):
            await inter.response.send_message("Solo el dueño y los oficiales de un team pueden invitar. (Admins: "
                                              "desde `/team panel team:…`.)", ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        await inter.followup.send(await invitar_a(inter, t, usuario), ephemeral=True)

    @grupo.command(name="invitaciones", description="Tus invitaciones a teams (aceptar o rechazar) y lo pendiente")
    async def team_invitaciones(inter: discord.Interaction):
        invs = [x for x in bot.registro.invitaciones_para(inter.user.id) if x["team"] in bot.teams]
        jugador = bot.registro.jugador_de(inter.user.id)
        pend = bot.registro.pendiente_de(jugador) if jugador else None
        if not invs and not pend:
            await inter.response.send_message("No tienes invitaciones ni nada pendiente.", ephemeral=True)
            return
        e = discord.Embed(title="📨 Tus invitaciones", color=COLOR)
        v = discord.ui.View(timeout=None)
        for x in invs[:4]:
            t = bot.teams[x["team"]]
            e.add_field(name=t.titulo, value=f"Te invita <@{x['de']}>", inline=False)
            v.add_item(BotonInvitacion(True, t.id, str(inter.user.id), f"Aceptar {t.nombre}"[:80]))
            v.add_item(BotonInvitacion(False, t.id, str(inter.user.id), "Rechazar"))
        if pend:
            e.description = f"⏳ Pendiente para cuando entres al servidor: {bot.texto_accion(pend)}."
        await inter.response.send_message(embed=e, view=v if invs else discord.utils.MISSING, ephemeral=True)

    class ConfirmarSalir(discord.ui.View):
        def __init__(self, autor_id: int):
            super().__init__(timeout=60)
            self.autor_id = autor_id

        @discord.ui.button(label="Sí, salir del team", style=discord.ButtonStyle.danger)
        async def si(self, i: discord.Interaction, _):
            jugador = bot.registro.jugador_de(i.user.id)
            t = bot.team_de_discord(i.user.id)
            if i.user.id != self.autor_id or not jugador or not t:
                await i.response.edit_message(content="No hay nada que hacer.", view=None)
                return
            await i.response.edit_message(content="⏳ Saliendo…", view=None)
            hecho, error = await pedir({"accion": "salir", "discord": str(i.user.id), "jugador": jugador,
                                                     "team": t.id})
            if not error:
                await registrar_cambio(f"salió del team **{esc(t.titulo)}** desde Discord", i.user)
            await i.edit_original_response(content=f"⚠️ No he podido sacarte: {error}" if error else
                                           f"✅ Ya no estás en **{esc(t.titulo)}**." if hecho else
                                           f"⏳ No estás conectado: saldrás de **{esc(t.titulo)}** al entrar al servidor.")

        @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
        async def no(self, i: discord.Interaction, _):
            await i.response.edit_message(content="Sigues en tu team.", view=None)

    @grupo.command(name="salir", description="Sal de tu team (si no estás conectado, al entrar al servidor)")
    async def team_salir(inter: discord.Interaction):
        t = bot.team_de_discord(inter.user.id)
        if not t:
            await inter.response.send_message("No estás en ningún team (o no tienes el Discord vinculado).",
                                              ephemeral=True)
            return
        if uuid_de_usuario(inter.user.id) == t.lider and len(t.miembros) > 1:
            await inter.response.send_message(
                "Eres el dueño y el team tiene más miembros: pasa el liderazgo en el juego (FTB Teams) o disuélvelo "
                "con `/team panel`.", ephemeral=True)
            return
        aviso = " Eres el único miembro: el team **se disolverá**." if len(t.miembros) <= 1 else ""
        await inter.response.send_message(f"¿Salir de **{esc(t.titulo)}**?{aviso}", view=ConfirmarSalir(inter.user.id),
                                          ephemeral=True)

    @grupo.command(name="panel", description="Cambia tu team desde Discord (dueño y oficiales de FTB; admins: cualquiera)")
    @app_commands.describe(team="Solo admins: el team que quieres gestionar")
    @app_commands.autocomplete(team=ac_team)
    async def team_panel(inter: discord.Interaction, team: str | None = None):
        if team:
            t = await team_o_error(inter, team)
            if not t:
                return
        else:
            t = bot.team_de_discord(inter.user.id)
            if not t:
                msg = ("No estás en ningún team (o no tienes el Discord vinculado: `/team vincular`). "
                       "Los teams se crean dentro del juego con FTB Teams.")
                if bot.es_admin(inter.user):
                    msg += "\nComo admin, puedes abrir el de cualquiera con `/team panel team:…`."
                await inter.response.send_message(msg, ephemeral=True)
                return
        if not puede_panel(inter.user, t):
            await inter.response.send_message(
                f"Solo el dueño y los oficiales de **{esc(t.titulo)}** pueden usar su panel. El dueño puede hacerte "
                "oficial en el juego (pantalla de FTB Teams → tu nombre → Oficial).", ephemeral=True)
            return
        await inter.response.send_message(embed=embed_panel(t), view=PanelTeam(inter.user, t.id, inter), ephemeral=True)

    tree.add_command(grupo)

    # ---- para admins (dentro de /admin): lo que no se cambia desde FTB
    def registrar_cambio(texto: str, quien):
        return bot.enviar(bot.canal_registro, embed=discord.Embed(
            description=f"🛡️ **{quien}** {texto}", color=0x555555, timestamp=datetime.now(timezone.utc)))

    @admin.command(name="team-editar", description="Etiqueta [ ], emoji, fuego amigo o rol de Discord de un team (nombre y color: en el juego)")
    @app_commands.describe(team="Team", etiqueta="Texto entre [] delante del jugador ('-' = el nombre del team)",
                           emoji="Emoji para Discord ('-' para quitarlo)", fuego_amigo="¿Se pueden hacer daño entre ellos?",
                           rol="Usar este rol de Discord en vez del que creó el bot")
    @app_commands.autocomplete(team=ac_team)
    async def adm_team_editar(inter: discord.Interaction, team: str,
                              etiqueta: app_commands.Range[str, 1, 24] | None = None,
                              emoji: app_commands.Range[str, 1, 8] | None = None,
                              fuego_amigo: bool | None = None, rol: discord.Role | None = None):
        if not bot.es_admin(inter.user) or not inter.guild:
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        t = await team_o_error(inter, team)
        if not t:
            return
        if all(v is None for v in (etiqueta, emoji, fuego_amigo, rol)):
            await inter.response.send_message("No has cambiado nada. (El nombre y el color se cambian en el juego, "
                                              "en los ajustes del team de FTB.)", ephemeral=True)
            return
        if rol and (rol.is_default() or rol.managed or any(
                o.id != t.id and bot.rol_de(inter.guild, o) == rol for o in bot.teams.values())):
            await inter.response.send_message("Ese rol no vale (es de otro team o no se puede asignar).", ephemeral=True)
            return
        await inter.response.defer(ephemeral=bot.privado)
        avisos = ""
        if etiqueta:
            t.etiqueta = "" if etiqueta.strip() == "-" else etiqueta_de(etiqueta)
        if emoji:
            t.emoji = "" if emoji.strip() == "-" else emoji.strip()
        if fuego_amigo is not None:
            t.fuego_amigo = fuego_amigo
        if rol:
            antes = bot.rol_de(inter.guild, t)
            if antes and antes != rol and t.rol_creado:
                try:
                    await antes.delete(reason=f"CobbleBOT: el team pasa a usar otro rol ({inter.user})")
                except discord.HTTPException:
                    avisos += "\n⚠️ No he podido borrar el rol anterior."
            t.rol, t.rol_creado = str(rol.id), False
        async with bot.registro.lock:
            bot.registro.guardar_extras(t)
        await bot.sincronizar_teams()
        r = bot.rol_de(inter.guild, t)
        await inter.followup.send(
            f"✅ Team **{esc(t.titulo)}** · rol {r.mention if r else '—'} · en el chat sale "
            f"`[{t.etiqueta or t.nombre}] Jugador` · fuego amigo {'sí' if t.fuego_amigo else 'no'}.{avisos}",
            ephemeral=bot.privado)
        await registrar_cambio(f"editó el team **{esc(t.titulo)}**", inter.user)

    @admin.command(name="vincular", description="Vincula a mano un usuario de Discord con un jugador de Minecraft")
    @app_commands.describe(usuario="Usuario de Discord", jugador="Jugador de Minecraft")
    @app_commands.autocomplete(jugador=ac_jugador)
    async def adm_vincular(inter: discord.Interaction, usuario: discord.Member, jugador: str):
        jugador = nombre_jugador(jugador)   # sin [etiqueta] ni '🟢 … (conectado)'
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        if not RE_NOMBRE_MC.match(jugador):
            await inter.response.send_message("Ese nombre de jugador no es válido.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=bot.privado)
        cache = (await bot.fuente.leer_json("usercache.json") if bot.fuente else None) or []
        c = next((c for c in cache if c.get("name", "").lower() == jugador.lower()), None)
        nombre = c["name"] if c else jugador
        async with bot.registro.lock:
            bot.registro.vincular(usuario.id, nombre, c.get("uuid") if c else None)
        await bot.sincronizar_teams()
        aviso = "" if c else "\n⚠️ Ese jugador no ha entrado nunca al servidor (revisa que esté bien escrito)."
        await inter.followup.send(f"✅ {usuario.mention} vinculado con **{esc(nombre)}**.{aviso}", ephemeral=bot.privado)

    @admin.command(name="desvincular", description="Quita el vínculo Discord ↔ Minecraft de un usuario (y el rol de su team)")
    @app_commands.describe(usuario="Usuario de Discord")
    async def adm_desvincular(inter: discord.Interaction, usuario: discord.Member):
        if not bot.es_admin(inter.user):
            await inter.response.send_message("No tienes permiso para usar esto.", ephemeral=True)
            return
        await inter.response.defer(ephemeral=bot.privado)
        async with bot.registro.lock:
            nombre = bot.registro.desvincular(usuario.id)
        aviso = ""
        quitar = [r for r in (bot.rol_de(usuario.guild, t) for t in bot.teams.values()) if r and r in usuario.roles]
        if quitar:
            try:
                await usuario.remove_roles(*quitar, reason="CobbleBOT: desvinculado")
            except discord.HTTPException:
                aviso = "\n⚠️ No he podido quitarle el rol del team (revisa mis permisos)."
        msg = f"✅ {usuario.mention} ya no está vinculado" + (f" (era **{esc(nombre)}**)." if nombre else ".")
        await inter.followup.send(msg + aviso, ephemeral=bot.privado)
