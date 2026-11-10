"""Teams de Minecraft (scoreboard teams): se crean y gestionan desde Discord por RCON y se leen del mundo.

- Los teams viven SOLO en el juego. Los crea un admin desde Discord (/admin team-crear) con el nombre,
  color y prefijo que quiera, o cualquiera con permisos dentro del juego con /team.
- El bot los lee de world/data/scoreboard.dat (solo lectura): nombre, color, prefijo, fuego amigo…
  Ese archivo se actualiza cuando el servidor guarda; tras crear/editar/borrar, el bot fuerza un guardado.
- Los miembros se preguntan en directo al servidor (/team list <team>).

El bot no guarda nada ni escribe en los archivos del servidor: todo va por comandos.
"""
import json
import re
import unicodedata
from dataclasses import dataclass

RE_ID_TEAM = re.compile(r"^[A-Za-z0-9_.+-]{1,16}$")
RUTA_SCOREBOARD = "data/scoreboard.dat"     # dentro de la carpeta del mundo


@dataclass
class Team:
    id: str              # nombre interno del team en Minecraft (sin espacios)
    nombre: str          # nombre que se ve
    color: str = "white"
    prefijo: str = ""    # '[Etiqueta] ' delante del nombre del jugador (chat, lista de jugadores, encima de la cabeza)
    fuego_amigo: bool = True
    miembros_guardados: tuple[str, ...] = ()   # los del último guardado del mundo (los de verdad: /team list)

    @property
    def titulo(self) -> str:
        return self.nombre or self.id

    @property
    def etiqueta(self) -> str:
        return etiqueta_de(self.prefijo) or self.titulo


def texto_de_json(valor) -> str:
    """Texto plano de un componente de texto de Minecraft ('{"text":"Hola","extra":[...]}', '"Hola"'…)."""
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return valor
    if isinstance(valor, str):
        return valor
    if isinstance(valor, list):
        return "".join(texto_de_json(v) for v in valor)
    if isinstance(valor, dict):
        return str(valor.get("text", "")) + "".join(texto_de_json(v) for v in valor.get("extra", []))
    return ""


def leer_scoreboard(datos: dict) -> dict[str, Team]:
    """Teams del scoreboard.dat ya parseado (NBT)."""
    teams = {}
    for t in (datos.get("data") or {}).get("Teams", []) or []:
        tid = str(t.get("Name", ""))
        if not tid:
            continue
        teams[tid] = Team(
            id=tid,
            nombre=texto_de_json(t.get("DisplayName", "")) or tid,
            color=str(t.get("TeamColor", "reset")),
            prefijo=texto_de_json(t.get("MemberNamePrefix", "")),
            fuego_amigo=bool(t.get("AllowFriendlyFire", 1)),
            miembros_guardados=tuple(t.get("Players", []) or []),
        )
    return dict(sorted(teams.items(), key=lambda kv: kv[1].titulo.lower()))


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


def id_para(nombre: str, existentes) -> str:
    """'Los Dragones Ñ' -> 'los_dragones_n' (válido para Minecraft y que no esté ya cogido)."""
    t = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "_", t).strip("_")[:14] or "team"
    tid, n = base, 2
    while tid in existentes:
        tid, n = f"{base[:13]}_{n}", n + 1
    return tid


def comandos_crear(tid: str, nombre: str, color: str, etiqueta: str, fuego_amigo: bool) -> list[str]:
    """Comandos para crear un team con su nombre, color, [etiqueta] y fuego amigo."""
    nombre_json = json.dumps({"text": nombre, "color": color}, ensure_ascii=False)
    return ([f"team add {tid} {nombre_json}"]
            + comandos_editar(tid, nombre=None, color=color, etiqueta=etiqueta or nombre, fuego_amigo=fuego_amigo)
            + [f"team modify {tid} seeFriendlyInvisibles true"])


def comandos_editar(tid: str, nombre: str | None = None, color: str | None = None, etiqueta: str | None = None,
                    fuego_amigo: bool | None = None, color_actual: str = "white") -> list[str]:
    """Solo los cambios pedidos (None = no tocar). La etiqueta es lo que sale entre [] delante del jugador."""
    c = color or color_actual
    cmds = []
    if nombre is not None:
        cmds.append(f"team modify {tid} displayName {json.dumps({'text': nombre, 'color': c}, ensure_ascii=False)}")
    if color is not None:
        cmds.append(f"team modify {tid} color {color}")
    if etiqueta is not None:
        cmds.append(f"team modify {tid} prefix {prefijo_json(etiqueta, c)}")
    if fuego_amigo is not None:
        cmds.append(f"team modify {tid} friendlyFire {'true' if fuego_amigo else 'false'}")
    return cmds


def parsear_miembros(salida: str) -> list[str]:
    """'Team [Rocket] has 2 member(s): Ana, Luis' -> ['Ana', 'Luis']; 'There are no members…' -> []"""
    if ":" not in salida:
        return []
    return [n.strip() for n in salida.split(":", 1)[1].split(",") if n.strip()]
