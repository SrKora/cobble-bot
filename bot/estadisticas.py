"""Lectura de estadísticas de jugadores desde la carpeta del mundo.

Minecraft guarda un JSON por jugador en <mundo>/stats/<uuid>.json.
Cobblemon añade sus propias estadísticas en ese mismo archivo, dentro de
"minecraft:custom", con claves que empiezan por "cobblemon:".
"""
import time
from dataclasses import dataclass, field

from .archivos import FuenteArchivos

TICKS_POR_HORA = 20 * 60 * 60

# Claves de distancia que suman "distancia recorrida" (en cm)
CLAVES_DISTANCIA = (
    "minecraft:walk_one_cm", "minecraft:sprint_one_cm", "minecraft:crouch_one_cm",
    "minecraft:swim_one_cm", "minecraft:walk_on_water_one_cm", "minecraft:walk_under_water_one_cm",
    "minecraft:fly_one_cm", "minecraft:aviate_one_cm", "minecraft:horse_one_cm",
    "minecraft:boat_one_cm", "minecraft:minecart_one_cm", "minecraft:pig_one_cm",
    "minecraft:strider_one_cm", "minecraft:climb_one_cm", "minecraft:fall_one_cm",
)

# Nombres en español para las estadísticas de Cobblemon más comunes.
# Las que no estén aquí se muestran con su clave tal cual.
NOMBRES_COBBLEMON = {
    "cobblemon:captured": "Pokémon capturados",
    "cobblemon:shinies_captured": "Shinies capturados",
    "cobblemon:battles_won": "Combates ganados",
    "cobblemon:battles_total": "Combates totales",
    "cobblemon:pvp_battles_won": "Combates PvP ganados",
    "cobblemon:pvp_battles_total": "Combates PvP totales",
    "cobblemon:pvw_battles_won": "Combates salvajes ganados",
    "cobblemon:pvw_battles_total": "Combates salvajes totales",
    "cobblemon:pvn_battles_won": "Combates contra NPC ganados",
    "cobblemon:pvn_battles_total": "Combates contra NPC totales",
    "cobblemon:evolved": "Evoluciones",
    "cobblemon:traded": "Intercambios",
    "cobblemon:hatched": "Huevos eclosionados",
    "cobblemon:released": "Pokémon liberados",
    "cobblemon:fossils_revived": "Fósiles revividos",
    "cobblemon:times_ridden": "Veces montado",
    "cobblemon:dex_entries": "Entradas de Pokédex",
}


@dataclass
class Jugador:
    uuid: str
    nombre: str
    custom: dict = field(default_factory=dict)  # contenido de "minecraft:custom"
    datos: dict = field(default_factory=dict)   # todas las categorías de stats

    def valor(self, clave: str) -> int:
        return int(self.custom.get(clave, 0))

    @property
    def horas(self) -> float:
        ticks = self.custom.get("minecraft:play_time", self.custom.get("minecraft:play_one_minute", 0))
        return ticks / TICKS_POR_HORA

    @property
    def distancia_km(self) -> float:
        return sum(self.custom.get(k, 0) for k in CLAVES_DISTANCIA) / 100_000

    def total_categoria(self, categoria: str) -> int:
        return int(sum(self.datos.get(categoria, {}).values()))

    @property
    def cobblemon(self) -> dict[str, int]:
        return {k: v for k, v in self.custom.items() if k.startswith("cobblemon:")}


class Estadisticas:
    def __init__(self, fuente: FuenteArchivos, carpeta_mundo: str = "world", cache_segundos: int = 300):
        self.fuente = fuente
        self.mundo = carpeta_mundo.strip("/")
        self.cache_segundos = cache_segundos
        self._cache: list[Jugador] | None = None
        self._cache_t = 0.0

    async def _nombres(self) -> dict[str, str]:
        """uuid -> nombre, a partir de usercache.json (en la raíz del servidor)."""
        datos = await self.fuente.leer_json("usercache.json") or []
        return {e["uuid"].lower(): e["name"] for e in datos if "uuid" in e and "name" in e}

    async def todos(self, forzar: bool = False) -> list[Jugador]:
        if not forzar and self._cache is not None and time.time() - self._cache_t < self.cache_segundos:
            return self._cache
        nombres = await self._nombres()
        jugadores = []
        for archivo in await self.fuente.listar(f"{self.mundo}/stats"):
            if not archivo.endswith(".json"):
                continue
            uuid = archivo[:-5].lower()
            datos = await self.fuente.leer_json(f"{self.mundo}/stats/{archivo}")
            if not datos:
                continue
            stats = datos.get("stats", {})
            jugadores.append(Jugador(
                uuid=uuid,
                nombre=nombres.get(uuid, uuid[:8]),
                custom=stats.get("minecraft:custom", {}),
                datos=stats,
            ))
        self._cache, self._cache_t = jugadores, time.time()
        return jugadores

    async def buscar(self, nombre: str) -> Jugador | None:
        nombre = nombre.lower()
        for j in await self.todos():
            if j.nombre.lower() == nombre:
                return j
        return None

    async def nombres(self) -> list[str]:
        return sorted(j.nombre for j in await self.todos())


# --- Rankings -------------------------------------------------------------
# clave -> (título, función que saca el valor, formato)
def _f_int(v): return f"{int(v):,}".replace(",", ".")
def _f_h(v): return f"{v:,.1f} h".replace(",", "X").replace(".", ",").replace("X", ".")
def _f_km(v): return f"{v:,.1f} km".replace(",", "X").replace(".", ",").replace("X", ".")


def construir_rankings(claves_cobblemon: dict[str, str]) -> dict[str, tuple[str, callable, callable]]:
    r = {
        "horas": ("Horas jugadas", lambda j: j.horas, _f_h),
        "muertes": ("Muertes", lambda j: j.valor("minecraft:deaths"), _f_int),
        "mobs": ("Mobs eliminados", lambda j: j.valor("minecraft:mob_kills"), _f_int),
        "distancia": ("Distancia recorrida", lambda j: j.distancia_km, _f_km),
        "minado": ("Bloques minados", lambda j: j.total_categoria("minecraft:mined"), _f_int),
    }
    for id_ranking, clave in claves_cobblemon.items():
        titulo = NOMBRES_COBBLEMON.get(clave, clave)
        r[id_ranking] = (titulo, (lambda c: lambda j: j.valor(c))(clave), _f_int)
    return r
