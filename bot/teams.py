"""Teams: equipos de Minecraft (scoreboard teams, por RCON) sincronizados con roles de Discord.

- Cada team se define en config.toml ([[teams]]): id del team en Minecraft, nombre, rol de Discord, color…
- Cada jugador vincula su cuenta de Discord con la de Minecraft (/team vincular → código en el chat del juego).
- La primera vez cada uno elige su team (/team unirse); para cambiarse tiene que hacerlo un admin.
- Manda Discord: si un admin pone o quita el rol de un team a mano, el bot lo copia a Minecraft.

El bot NO escribe en los archivos del servidor: los teams de Minecraft se crean y cambian con comandos
(/team …) y lo que el bot necesita recordar (vínculos y teams) va en su propia carpeta de datos.
"""
import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

RE_ID_TEAM = re.compile(r"^[a-z0-9_.+-]{1,16}$")
CODIGO_SEGUNDOS = 300


@dataclass
class Team:
    id: str              # nombre del team dentro de Minecraft (sin espacios)
    nombre: str          # nombre bonito
    rol: str             # rol de Discord: nombre exacto o ID
    color: str = "white"
    etiqueta: str = ""   # sale como [Etiqueta] delante del jugador (chat, lista de jugadores, encima de la cabeza)
    fuego_amigo: bool = False
    emoji: str = ""

    @property
    def titulo(self) -> str:
        return f"{self.emoji} {self.nombre}".strip()


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


def cargar_teams(cfg: dict) -> dict[str, Team]:
    teams = {}
    for t in cfg.get("teams", []) or []:
        tid = str(t.get("id", "")).lower()
        if not RE_ID_TEAM.match(tid) or not t.get("rol"):
            continue
        teams[tid] = Team(tid, str(t.get("nombre") or tid), str(t["rol"]), str(t.get("color", "white")),
                          etiqueta_de(t.get("etiqueta") or t.get("prefijo") or t.get("nombre") or tid),
                          bool(t.get("fuego_amigo", False)), str(t.get("emoji", "")))
    return teams


def comandos_crear(t: Team) -> list[str]:
    """Comandos (idempotentes) para que el team exista en Minecraft con su color y su [etiqueta]."""
    nombre = json.dumps({"text": t.nombre, "color": t.color}, ensure_ascii=False)
    prefijo = prefijo_json(t.etiqueta or t.nombre, t.color)
    return [
        f"team add {t.id} {nombre}",                      # si ya existe, el servidor solo avisa
        f"team modify {t.id} displayName {nombre}",
        f"team modify {t.id} color reset",                # el color va solo en la [etiqueta], no en el nombre
        f"team modify {t.id} prefix {prefijo}",
        f"team modify {t.id} friendlyFire {'true' if t.fuego_amigo else 'false'}",
        f"team modify {t.id} seeFriendlyInvisibles true",
    ]


def parsear_miembros(salida: str) -> list[str]:
    """'Team [Rocket] has 2 members: Ana, Luis' -> ['Ana', 'Luis']; 'There are no members…' -> []"""
    if ":" not in salida:
        return []
    return [n.strip() for n in salida.split(":", 1)[1].split(",") if n.strip()]


class Registro:
    """Vínculos Discord ↔ Minecraft y team de cada uno. Se guarda en la carpeta de datos del BOT."""

    def __init__(self, carpeta: str | Path):
        self.ruta = Path(carpeta) / "teams.json"
        self.vinculos: dict[str, dict] = {}     # discord_id -> {"nombre": ..., "uuid": ...}
        self.equipos: dict[str, str] = {}       # discord_id -> id del team
        # Usuarios cuyo rol de Discord no se pudo poner/quitar (faltaban permisos): se reintenta al sincronizar
        self.pendientes: set[str] = set()
        self._codigos: dict[str, tuple[str, str, float]] = {}   # discord_id -> (código, jugador, caduca)
        self.lock = asyncio.Lock()
        try:
            d = json.loads(self.ruta.read_text(encoding="utf-8"))
            self.vinculos, self.equipos = d.get("vinculos", {}), d.get("equipos", {})
            self.pendientes = set(d.get("roles_pendientes", []))
        except (OSError, ValueError):
            pass

    def guardar(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"vinculos": self.vinculos, "equipos": self.equipos,
                                   "roles_pendientes": sorted(self.pendientes)}, indent=2,
                                  ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.ruta)

    # ---- vínculos
    def jugador_de(self, discord_id: int) -> str | None:
        v = self.vinculos.get(str(discord_id))
        return v["nombre"] if v else None

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
            self.equipos.pop(d, None)
        self.vinculos[str(discord_id)] = {"nombre": jugador, "uuid": uuid}
        self.guardar()

    def desvincular(self, discord_id: int) -> str | None:
        v = self.vinculos.pop(str(discord_id), None)
        self.equipos.pop(str(discord_id), None)
        self.guardar()
        return v["nombre"] if v else None

    # ---- teams
    def team_de(self, discord_id: int) -> str | None:
        return self.equipos.get(str(discord_id))

    def marcar_pendiente(self, discord_id: int, pendiente: bool):
        (self.pendientes.add if pendiente else self.pendientes.discard)(str(discord_id))
        self.guardar()

    def poner_team(self, discord_id: int, team: str | None):
        if team:
            self.equipos[str(discord_id)] = team
        else:
            self.equipos.pop(str(discord_id), None)
        self.guardar()

    def miembros(self, team: str) -> list[tuple[str, str]]:
        """(discord_id, jugador) de un team."""
        return [(d, self.vinculos[d]["nombre"]) for d, t in self.equipos.items() if t == team and d in self.vinculos]
