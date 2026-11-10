"""Teams de Minecraft (scoreboard teams) gestionados por RCON y consultables desde Discord.

- Cada team se define en config.toml ([[teams]]): id del team en Minecraft, nombre, color, prefijo…
- Los teams viven SOLO en el juego: el bot los crea con comandos y mete o saca jugadores con
  /team join|leave (desde Discord, /admin team; también vale hacerlo dentro del juego con /team).
- Discord solo muestra la información (miembros, ranking), leída del propio servidor.

El bot no guarda nada ni escribe en los archivos del servidor: todo va por comandos.
"""
import json
import re
from dataclasses import dataclass

RE_ID_TEAM = re.compile(r"^[a-z0-9_.+-]{1,16}$")


@dataclass
class Team:
    id: str              # nombre del team dentro de Minecraft (sin espacios)
    nombre: str          # nombre bonito
    color: str = "white"
    prefijo: str = ""    # delante del nombre en el chat y la lista de jugadores
    fuego_amigo: bool = False
    emoji: str = ""

    @property
    def titulo(self) -> str:
        return f"{self.emoji} {self.nombre}".strip()


def cargar_teams(cfg: dict) -> dict[str, Team]:
    teams = {}
    for t in cfg.get("teams", []) or []:
        tid = str(t.get("id", "")).lower()
        if not RE_ID_TEAM.match(tid):
            continue
        teams[tid] = Team(tid, str(t.get("nombre") or tid), str(t.get("color", "white")),
                          str(t.get("prefijo", "")), bool(t.get("fuego_amigo", False)), str(t.get("emoji", "")))
    return teams


def comandos_crear(t: Team) -> list[str]:
    """Comandos (idempotentes) para que el team exista en Minecraft con su color y prefijo."""
    nombre = json.dumps({"text": t.nombre, "color": t.color}, ensure_ascii=False)
    prefijo = json.dumps({"text": t.prefijo, "color": t.color}, ensure_ascii=False)
    return [
        f"team add {t.id} {nombre}",                      # si ya existe, el servidor solo avisa
        f"team modify {t.id} displayName {nombre}",
        f"team modify {t.id} color {t.color}",
        f"team modify {t.id} prefix {prefijo}",
        f"team modify {t.id} friendlyFire {'true' if t.fuego_amigo else 'false'}",
        f"team modify {t.id} seeFriendlyInvisibles true",
    ]


def parsear_miembros(salida: str) -> list[str]:
    """'Team [Rocket] has 2 member(s): Ana, Luis' -> ['Ana', 'Luis']; 'There are no members…' -> []"""
    if ":" not in salida:
        return []
    return [n.strip() for n in salida.split(":", 1)[1].split(",") if n.strip()]
