"""Teams: los de FTB Teams (los que crea la gente dentro del juego), copiados a Discord y al chat.

- Los teams se crean y se gestionan DENTRO DEL JUEGO con FTB Teams: crear, invitar, echar, nombre y color.
- El bot los lee del mundo (<mundo>/ftbteams/party/*.snbt; FTB los guarda cuando el servidor guarda el mundo)
  y por cada team:
    · crea un rol de Discord con su nombre y color y se lo pone a los miembros que tengan el Discord vinculado;
    · pone [Etiqueta] delante del nombre en el chat, la lista de jugadores (Tab) y encima de la cabeza, con un
      team normal de Minecraft (/team) que maneja el propio bot (se llama ftb_ + el principio del id del team).
- Manda el juego: si alguien se pone o se quita a mano el rol de un team en Discord, el bot lo deshace.
- Cada jugador vincula su Discord con su cuenta de Minecraft (/team vincular → código en el chat del juego).

El bot NO escribe en los archivos del servidor. Lo que necesita recordar (vínculos, el rol de Discord de cada
team y lo que un admin le cambie: etiqueta, emoji, fuego amigo) va en su carpeta de datos (datos/teams.json).
"""
import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

CODIGO_SEGUNDOS = 300
INVITACION_DIAS = 7
RE_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
RE_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
RANGOS_MIEMBRO = ("owner", "officer", "member")     # "ally" e "invited" no son miembros
EXTRAS = ("rol", "rol_creado", "etiqueta", "emoji", "fuego_amigo")


@dataclass
class Team:
    id: str                    # UUID del team de FTB
    nombre: str
    color: str = "#FFFFFF"     # el que tiene en FTB (hexadecimal)
    lider: str = ""            # UUID del dueño
    miembros: list[str] = field(default_factory=list)   # UUIDs, el dueño primero
    oficiales: list[str] = field(default_factory=list)  # UUIDs con rango "officer" en FTB (pueden gestionar)
    # Lo que pone el bot o un admin desde Discord (se guarda en datos/teams.json)
    rol: str = ""              # ID del rol de Discord
    rol_creado: bool = False   # el rol lo creó el bot (se borra cuando desaparece el team)
    etiqueta: str = ""         # lo que sale entre [] (por defecto, el nombre)
    emoji: str = ""
    fuego_amigo: bool = False

    @property
    def titulo(self) -> str:
        return f"{self.emoji} {self.nombre}".strip()

    @property
    def id_mc(self) -> str:
        """Nombre del team de Minecraft (/team) que lleva la [etiqueta]: máximo 16 caracteres."""
        return id_mc(self.id)

    @property
    def color_int(self) -> int:
        return int(self.color[1:], 16)

    @property
    def id_corto(self) -> str:
        """Cómo nombran los comandos de FTB al team: 'Los_mejores#ed2875a4' (cambia si cambia el nombre)."""
        return re.sub(r"[^A-Za-z0-9_]", "_", self.nombre) + "#" + self.id.replace("-", "")[:8]

    def puede_gestionar(self, uuid: str | None) -> bool:
        """El dueño y los oficiales del team de FTB."""
        return bool(uuid) and (uuid.lower() == self.lider or uuid.lower() in self.oficiales)


def id_mc(team_id: str) -> str:
    return "ftb_" + team_id.replace("-", "")[:12].lower()


def etiqueta_de(texto: str) -> str:
    """'[Los Dragones] ' -> 'Los Dragones' (lo de dentro de los corchetes, sin espacios de sobra)."""
    t = " ".join(str(texto).split())
    while t.startswith("[") and t.endswith("]"):
        t = t[1:-1].strip()
    return t.strip("[]").strip()[:24]


def prefijo_json(etiqueta: str, color: str) -> str:
    """Prefijo del team: '[Etiqueta] ' con los corchetes en gris y la etiqueta del color del team.
    Minecraft lo pone delante del nombre del jugador en el chat, en la lista de jugadores y encima de su cabeza."""
    return json.dumps({"text": "[", "color": "gray", "extra": [
        {"text": etiqueta_de(etiqueta), "color": color}, {"text": "] ", "color": "gray"}]}, ensure_ascii=False)


def _cadena(snbt: str, clave: str) -> str | None:
    m = re.search(r'"?' + re.escape(clave) + r'"?\s*:\s*"((?:[^"\\]|\\.)*)"', snbt)
    if not m:
        return None
    try:
        return json.loads(f'"{m.group(1)}"')
    except ValueError:
        return m.group(1)


def parsear_party(snbt: str) -> Team | None:
    """Lee un team de FTB Teams (archivo ftbteams/party/<uuid>.snbt). None si no es un team de jugadores."""
    if _cadena(snbt, "type") != "party":
        return None
    tid, lider = _cadena(snbt, "id"), _cadena(snbt, "owner") or ""
    if not tid or not re.fullmatch(RE_UUID, tid):
        return None
    m = re.search(r"\branks\s*:\s*\{(.*?)\}", snbt, re.S)
    # FTB escribe las claves sin comillas (901ff2cd-…: "owner"); se aceptan las dos formas
    rangos = re.findall(r'"?(' + RE_UUID + r')"?\s*:\s*"(\w+)"', m.group(1)) if m else []
    miembros = [u.lower() for u, r in rangos if r in RANGOS_MIEMBRO]
    lider = lider.lower()
    if re.fullmatch(RE_UUID, lider):
        miembros = [lider] + [u for u in miembros if u != lider]
    color = _cadena(snbt, "ftbteams:color") or ""
    nombre = " ".join((_cadena(snbt, "ftbteams:display_name") or "").split()) or "Team"
    oficiales = [u.lower() for u, r in rangos if r == "officer"]
    return Team(tid.lower(), nombre[:64], color.upper() if RE_HEX.match(color) else "#FFFFFF", lider,
                list(dict.fromkeys(miembros)), oficiales)


COLORES = {"rojo": "#FF5555", "rojo oscuro": "#AA0000", "naranja": "#FFAA00", "dorado": "#FFD700",
           "amarillo": "#FFFF55", "lima": "#AAFF00", "verde": "#55FF55", "verde oscuro": "#00AA00",
           "turquesa": "#00AAAA", "cian": "#55FFFF", "celeste": "#87CEEB", "azul": "#5555FF",
           "azul oscuro": "#0000AA", "morado": "#AA00AA", "lila": "#C8A2C8", "rosa": "#FF55FF",
           "fucsia": "#FF00AA", "marrón": "#8B4513", "blanco": "#FFFFFF", "gris": "#AAAAAA",
           "gris oscuro": "#555555", "negro": "#000000"}
# Cuadrado de color de Discord más parecido a cada color (para verlo en las listas)
EMOJI_COLOR = {"rojo": "🟥", "rojo oscuro": "🟥", "naranja": "🟧", "dorado": "🟨", "amarillo": "🟨", "lima": "🟩",
               "verde": "🟩", "verde oscuro": "🟩", "turquesa": "🟦", "cian": "🟦", "celeste": "🟦", "azul": "🟦",
               "azul oscuro": "🟦", "morado": "🟪", "lila": "🟪", "rosa": "🟪", "fucsia": "🟪", "marrón": "🟫",
               "blanco": "⬜", "gris": "⬜", "gris oscuro": "⬛", "negro": "⬛"}


def png_color(color: str, lado: int = 96) -> bytes:
    """Imagen PNG de un cuadrado del color dado ('#RRGGBB'), para enseñar el color en Discord."""
    import struct
    import zlib
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    fila = b"\x00" + bytes((r, g, b)) * lado
    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", lado, lado, 8, 2, 0, 0, 0))
            + trozo(b"IDAT", zlib.compress(fila * lado)) + trozo(b"IEND", b""))


def color_de(texto: str) -> str | None:
    """'#ff0000', 'ff0000' o 'rojo' -> '#FF0000'. None si no es un color."""
    t = " ".join(str(texto).lower().split())
    if t in COLORES:
        return COLORES[t]
    t = t if t.startswith("#") else "#" + t
    return t.upper() if RE_HEX.match(t) else None


def nombre_team_valido(texto: str) -> str:
    """Nombre seguro para mandarlo por consola a FTB: una línea, sin códigos de color, máximo 32 caracteres."""
    t = re.sub(r"§.?", "", re.sub(r"[\x00-\x1f\x7f]", " ", str(texto)))
    return " ".join(t.split())[:32]


def comando_ftb(t: Team, clave: str, valor: str) -> str:
    """Cambia un ajuste del team en FTB Teams (como si lo hiciera su dueño en la pantalla del juego)."""
    return f"ftbteams party settings_for {t.id_corto} ftbteams:{clave} {valor}"


def comandos_crear(t: Team) -> list[str]:
    """Comandos (idempotentes) para que el team de Minecraft exista con su nombre, color y [etiqueta]."""
    nombre = json.dumps({"text": t.nombre, "color": t.color}, ensure_ascii=False)
    prefijo = prefijo_json(t.etiqueta or t.nombre, t.color)
    return [
        f"team add {t.id_mc} {nombre}",                    # si ya existe, el servidor solo avisa
        f"team modify {t.id_mc} displayName {nombre}",
        f"team modify {t.id_mc} color reset",              # el color va solo en la [etiqueta], no en el nombre
        f"team modify {t.id_mc} prefix {prefijo}",
        f"team modify {t.id_mc} friendlyFire {'true' if t.fuego_amigo else 'false'}",
        f"team modify {t.id_mc} seeFriendlyInvisibles true",
    ]


def parsear_miembros(salida: str) -> list[str]:
    """'Team [Rocket] has 2 members: Ana, Luis' -> ['Ana', 'Luis']; 'There are no members…' -> []"""
    if ":" not in salida:
        return []
    return [n.strip() for n in salida.split(":", 1)[1].split(",") if n.strip()]


class Registro:
    """Vínculos Discord ↔ Minecraft y lo que el bot recuerda de cada team. Va en la carpeta de datos del BOT."""

    def __init__(self, carpeta: str | Path):
        self.ruta = Path(carpeta) / "teams.json"
        self.vinculos: dict[str, dict] = {}     # discord_id -> {"nombre": ..., "uuid": ...}
        self.extras: dict[str, dict] = {}       # id del team de FTB -> {"rol", "rol_creado", "etiqueta", ...}
        # Teams del sistema anterior (creados desde Discord): se quitan una vez del juego y de Discord
        self.antiguos: dict[str, dict] = {}     # id del team de Minecraft -> {"rol", "rol_creado"}
        self._codigos: dict[str, tuple[str, str, float]] = {}   # discord_id -> (código, jugador, caduca)
        # Invitaciones hechas desde Discord: {"team", "para", "de", "cuando"} (discord_id en texto)
        self.invitaciones: list[dict] = []
        # Lo pedido desde Discord que se hace en el juego cuando el jugador está conectado:
        # {"accion": "crear"|"unir"|"salir"|"echar", "discord", "jugador", "team", "nombre", "color", "cuando"}
        self.pendientes: list[dict] = []
        self.lock = asyncio.Lock()
        try:
            d = json.loads(self.ruta.read_text(encoding="utf-8"))
            self.vinculos = d.get("vinculos", {})
            self.extras = d.get("extras", {})
            self.antiguos = d.get("antiguos", {})
            self.invitaciones = d.get("invitaciones", [])
            self.pendientes = d.get("pendientes", [])
            for tid, t in (d.get("teams") or {}).items():      # formato anterior
                self.antiguos[tid] = {"rol": str(t.get("rol", "")), "rol_creado": bool(t.get("rol_creado"))}
        except (OSError, ValueError):
            pass

    def guardar(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"vinculos": self.vinculos, "extras": self.extras, "antiguos": self.antiguos,
                                   "invitaciones": self.invitaciones, "pendientes": self.pendientes},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.ruta)

    # ---- vínculos
    def jugador_de(self, discord_id: int) -> str | None:
        v = self.vinculos.get(str(discord_id))
        return v["nombre"] if v else None

    def uuid_de(self, discord_id) -> str | None:
        v = self.vinculos.get(str(discord_id))
        return (v.get("uuid") or "").lower() or None if v else None

    def discord_de(self, jugador: str) -> str | None:
        return next((d for d, v in self.vinculos.items() if v["nombre"].lower() == jugador.lower()), None)

    def nuevo_codigo(self, discord_id: int, jugador: str) -> str:
        codigo = f"{random.SystemRandom().randint(0, 999999):06d}"
        self._codigos[str(discord_id)] = (codigo, jugador, time.time() + CODIGO_SEGUNDOS)
        return codigo

    def comprobar_codigo(self, discord_id: int, codigo: str) -> str | None:
        c = self._codigos.get(str(discord_id))
        if not c or c[2] < time.time() or c[0] != codigo.strip():
            return None
        del self._codigos[str(discord_id)]
        return c[1]

    def vincular(self, discord_id: int, jugador: str, uuid: str | None = None):
        # Una cuenta de Minecraft solo puede estar vinculada a un usuario de Discord
        for d in [d for d, v in self.vinculos.items() if v["nombre"].lower() == jugador.lower()]:
            self.vinculos.pop(d, None)
        self.vinculos[str(discord_id)] = {"nombre": jugador, "uuid": uuid}
        self.guardar()

    def desvincular(self, discord_id: int) -> str | None:
        v = self.vinculos.pop(str(discord_id), None)
        self.guardar()
        return v["nombre"] if v else None

    # ---- lo que el bot recuerda de cada team de FTB
    def aplicar_extras(self, t: Team):
        for k, v in self.extras.get(t.id, {}).items():
            if k in EXTRAS:
                setattr(t, k, v)

    def guardar_extras(self, t: Team):
        self.extras[t.id] = {k: getattr(t, k) for k in EXTRAS}
        self.guardar()

    def olvidar_team(self, tid: str):
        if self.extras.pop(tid, None) is not None:
            self.guardar()

    def antiguo_hecho(self, tid: str):
        if self.antiguos.pop(tid, None) is not None:
            self.guardar()

    # ---- invitaciones desde Discord (caducan a los INVITACION_DIAS)
    def _limpiar_caducadas(self):
        limite = time.time() - INVITACION_DIAS * 86400
        self.invitaciones = [x for x in self.invitaciones if x.get("cuando", 0) >= limite]
        self.pendientes = [x for x in self.pendientes if x.get("cuando", 0) >= limite]

    def invitar(self, tid: str, de, para):
        self._limpiar_caducadas()
        self.invitaciones = [x for x in self.invitaciones if not (x["team"] == tid and x["para"] == str(para))]
        self.invitaciones.append({"team": tid, "para": str(para), "de": str(de), "cuando": time.time()})
        self.guardar()

    def invitaciones_para(self, para) -> list[dict]:
        self._limpiar_caducadas()
        return [x for x in self.invitaciones if x["para"] == str(para)]

    def invitaciones_de_team(self, tid: str) -> list[dict]:
        self._limpiar_caducadas()
        return [x for x in self.invitaciones if x["team"] == tid]

    def quitar_invitacion(self, tid: str, para) -> bool:
        antes = len(self.invitaciones)
        self.invitaciones = [x for x in self.invitaciones if not (x["team"] == tid and x["para"] == str(para))]
        if len(self.invitaciones) != antes:
            self.guardar()
            return True
        return False

    # ---- cosas pendientes de hacer en el juego (cuando el jugador se conecte)
    def encolar(self, accion: str, discord_id, jugador: str, **datos):
        """Una sola cosa pendiente por jugador: lo último que pidió."""
        self._limpiar_caducadas()
        self.pendientes = [x for x in self.pendientes if x["jugador"].lower() != jugador.lower()]
        self.pendientes.append({"accion": accion, "discord": str(discord_id), "jugador": jugador,
                                "cuando": time.time(), **datos})
        self.guardar()

    def pendiente_de(self, jugador: str) -> dict | None:
        return next((x for x in self.pendientes if x["jugador"].lower() == jugador.lower()), None)

    def quitar_pendiente(self, jugador: str):
        antes = len(self.pendientes)
        self.pendientes = [x for x in self.pendientes if x["jugador"].lower() != jugador.lower()]
        if len(self.pendientes) != antes:
            self.guardar()

    def olvidar_team_pendientes(self, tid: str):
        """Si un team desaparece, sus invitaciones y entradas pendientes ya no valen."""
        antes = (len(self.invitaciones), len(self.pendientes))
        self.invitaciones = [x for x in self.invitaciones if x["team"] != tid]
        self.pendientes = [x for x in self.pendientes if x.get("team") != tid or x["accion"] == "crear"]
        if antes != (len(self.invitaciones), len(self.pendientes)):
            self.guardar()
