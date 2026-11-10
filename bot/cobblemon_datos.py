"""Datos de Cobblemon leídos del .jar del servidor (especies, habilidades, movimientos, formas, Poké Balls)
y construcción de las "Pokémon properties" que entiende /pokegiveother."""
import io
import json
import re
import zipfile
from dataclasses import dataclass, field

RE_JAR_COBBLEMON = re.compile(r"^cobblemon-(fabric|neoforge|forge)-.*\.jar$", re.I)
RE_ESPECIE = re.compile(r"^data/cobblemon/species/[^/]+/([a-z0-9_-]+)\.json$")
RE_BALL = re.compile(r"^assets/cobblemon/models/item/([a-z_]+_ball)\.json$")
NO_SON_BALLS = {"iron_ball", "light_ball", "smoke_ball"}  # objetos equipables con nombre de ball

# Orden en el que se escriben IVs/EVs: el mismo que en los juegos
STATS = ["hp", "atk", "def", "spa", "spd", "spe"]
NOMBRES_STATS = ["PS", "Ataque", "Defensa", "At. Esp.", "Def. Esp.", "Velocidad"]

NATURALEZAS = {
    "hardy": "Fuerte", "lonely": "Huraña", "brave": "Audaz", "adamant": "Firme", "naughty": "Pícara",
    "bold": "Osada", "docile": "Dócil", "relaxed": "Plácida", "impish": "Agitada", "lax": "Floja",
    "timid": "Miedosa", "hasty": "Activa", "serious": "Seria", "jolly": "Alegre", "naive": "Ingenua",
    "modest": "Modesta", "mild": "Afable", "quiet": "Mansa", "bashful": "Tímida", "rash": "Alocada",
    "calm": "Serena", "gentle": "Amable", "sassy": "Grosera", "careful": "Cauta", "quirky": "Rara",
}
TIPOS_TERA = {
    "normal": "Normal", "fire": "Fuego", "water": "Agua", "electric": "Eléctrico", "grass": "Planta",
    "ice": "Hielo", "fighting": "Lucha", "poison": "Veneno", "ground": "Tierra", "flying": "Volador",
    "psychic": "Psíquico", "bug": "Bicho", "rock": "Roca", "ghost": "Fantasma", "dragon": "Dragón",
    "dark": "Siniestro", "steel": "Acero", "fairy": "Hada", "stellar": "Astral",
}

RE_ID = re.compile(r"^([a-z0-9_.-]+:)?[a-z0-9_./-]+$")
RE_MOTE = re.compile(r"^[\w\-.!?¡¿]{1,20}$")
RE_EXTRA = re.compile(r"^[a-z0-9_]+=[a-z0-9_:.,\-]+$")


@dataclass
class Especie:
    id: str
    nombre: str
    habilidades: list[str] = field(default_factory=list)   # "h:xxx" = oculta
    movimientos: list[str] = field(default_factory=list)   # ids sin el prefijo "1:", "tm:"…
    formas: list[str] = field(default_factory=list)


@dataclass
class DatosCobblemon:
    especies: dict[str, Especie] = field(default_factory=dict)
    balls: list[str] = field(default_factory=list)

    @classmethod
    def desde_jar(cls, datos: bytes) -> "DatosCobblemon":
        res = cls()
        balls = set()
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            for n in z.namelist():
                if m := RE_BALL.match(n):
                    if m.group(1) not in NO_SON_BALLS:
                        balls.add(m.group(1))
                    continue
                if not (m := RE_ESPECIE.match(n)):
                    continue
                try:
                    d = json.loads(z.read(n))
                except ValueError:
                    continue
                i = m.group(1)
                habs = list(d.get("abilities", []))
                formas = []
                for f in d.get("forms", []) or []:
                    if f.get("name"):
                        formas.append(f["name"])
                    habs += [h for h in f.get("abilities", []) if h not in habs]
                movs = []
                for mv in d.get("moves", []):
                    mv = mv.split(":", 1)[-1]
                    if mv not in movs:
                        movs.append(mv)
                res.especies[i] = Especie(i, d.get("name") or i.capitalize(), habs, sorted(movs), formas)
        res.especies = dict(sorted(res.especies.items(), key=lambda kv: kv[1].nombre))
        res.balls = sorted(balls)
        return res


def parsear_stats(texto: str, maximo: int, total_max: int | None = None) -> dict[str, int]:
    """'31' / 'max' -> todas a ese valor; '31,0,31,31,31,31' -> una por stat (PS, Atq, Def, AtE, DfE, Vel);
    'atk=252 spe=252' -> solo esas. Lanza ValueError con un mensaje en español si algo no cuadra."""
    t = texto.strip().lower()
    if t in ("max", "máx", "maximo", "máximo", "perfectos"):
        valores = {s: maximo for s in STATS}
    elif "=" in t:
        valores = {}
        for parte in re.split(r"[\s,;]+", t):
            if not parte:
                continue
            k, _, v = parte.partition("=")
            if k not in STATS:
                raise ValueError(f"`{k}` no es una estadística. Usa: {', '.join(STATS)}")
            valores[k] = int(v)
    else:
        nums = [int(x) for x in re.split(r"[\s,;/]+", t) if x]
        if len(nums) == 1:
            nums *= 6
        if len(nums) != 6:
            raise ValueError("Pon 1 número (para todas) o 6 separados por comas: PS, Atq, Def, AtEsp, DefEsp, Vel")
        valores = dict(zip(STATS, nums))
    for k, v in valores.items():
        if not 0 <= v <= maximo:
            raise ValueError(f"`{k}` tiene que estar entre 0 y {maximo}")
    if total_max is not None and sum(valores.values()) > total_max:
        raise ValueError(f"La suma no puede pasar de {total_max} (ahora {sum(valores.values())})")
    return valores


def resumen_stats(valores: dict[str, int]) -> str:
    return " / ".join(str(valores.get(s, "-")) for s in STATS)
