"""Perfil completo de cada jugador de COBBLEVERSE, leído de la carpeta del mundo.

Fuentes (todas dentro de <servidor>/<mundo>/):
- stats/<uuid>.json                      → horas, muertes, distancia… y stats "cobblemon:*"
- advancements/<uuid>.json               → medallas y ligas (COBBLEVERSE las da como logros)
- pokemon/playerpartystore/xx/<uuid>.dat → equipo actual (NBT; .json si storageFormat=json)
- pokemon/pcstore/xx/<uuid>.dat          → Pokémon del PC
- cobbledollarsplayerdata/               → dinero de CobbleDollars
Los nombres salen de usercache.json (raíz del servidor).

Todo se lee de forma tolerante: si un archivo no existe o tiene otro formato,
ese dato simplemente sale vacío. /diagnostico ayuda a ver qué se está leyendo.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field

from . import nbt
from .archivos import FuenteArchivos

log = logging.getLogger("cobblebot.perfiles")

TICKS_POR_HORA = 20 * 60 * 60
REGIONES = ["kanto", "johto", "hoenn", "sinnoh", "unova", "kalos", "alola", "galar", "hisui", "paldea"]
RE_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

CLAVES_DISTANCIA = (
    "minecraft:walk_one_cm", "minecraft:sprint_one_cm", "minecraft:crouch_one_cm",
    "minecraft:swim_one_cm", "minecraft:walk_on_water_one_cm", "minecraft:walk_under_water_one_cm",
    "minecraft:fly_one_cm", "minecraft:aviate_one_cm", "minecraft:horse_one_cm",
    "minecraft:boat_one_cm", "minecraft:minecart_one_cm", "minecraft:pig_one_cm",
    "minecraft:strider_one_cm", "minecraft:climb_one_cm", "minecraft:fall_one_cm",
)

NOMBRES_COBBLEMON = {
    "cobblemon:captured": "Pokémon capturados",
    "cobblemon:shinies_captured": "Shinies capturados",
    "cobblemon:battles_won": "Combates ganados",
    "cobblemon:battles_total": "Combates totales",
    "cobblemon:evolved": "Evoluciones",
    "cobblemon:traded": "Intercambios",
    "cobblemon:hatched": "Huevos eclosionados",
    "cobblemon:released": "Pokémon liberados",
    "cobblemon:fossils_revived": "Fósiles revividos",
    "cobblemon:dex_entries": "Entradas de Pokédex",
}


def bonito(texto: str) -> str:
    """'cobblemon:mr_mime' -> 'Mr Mime'; 'sinnoh/coal_badge' -> 'Coal Badge'"""
    t = texto.split(":")[-1].split("/")[-1]
    return t.replace("_", " ").replace("-", " ").strip().title()


# --------------------------------------------------------------------- Pokémon
@dataclass
class Pokemon:
    especie: str
    nivel: int = 0
    shiny: bool = False
    mote: str | None = None
    genero: str | None = None
    forma: str | None = None

    @property
    def nombre(self) -> str:
        return bonito(self.especie)

    def linea(self) -> str:
        sexo = {"MALE": " ♂", "FEMALE": " ♀"}.get((self.genero or "").upper(), "")
        brillo = " ✨" if self.shiny else ""
        forma = f" ({bonito(self.forma)})" if self.forma and self.forma.lower() not in ("normal", "") else ""
        base = f"**{self.nombre}**{forma}{sexo}{brillo} · Nv. {self.nivel}"
        if self.mote and self.mote.lower() != self.nombre.lower():
            base = f"*{self.mote}* — " + base
        return base


def _clave(d: dict, *nombres):
    """Busca una clave sin distinguir mayúsculas."""
    bajos = {k.lower(): k for k in d}
    for n in nombres:
        if n.lower() in bajos:
            return d[bajos[n.lower()]]
    return None


def _texto_mote(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, dict):
        return v.get("text") or v.get("translate")
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("{") or s.startswith('"'):
            try:
                return _texto_mote(json.loads(s))
            except json.JSONDecodeError:
                pass
        return s or None
    return None


def _orden_natural(k: str):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", k)]


def extraer_pokemon(arbol) -> list[Pokemon]:
    """Recorre el NBT/JSON y saca todo lo que parezca un Pokémon (tiene 'species')."""
    res: list[Pokemon] = []

    def visitar(n):
        if isinstance(n, dict):
            especie = _clave(n, "Species", "species")
            if isinstance(especie, str) and especie:
                nivel = _clave(n, "Level", "level")
                shiny = _clave(n, "Shiny", "shiny")
                forma = _clave(n, "FormId", "Form", "form")
                res.append(Pokemon(
                    especie=especie,
                    nivel=int(nivel) if isinstance(nivel, (int, float)) else 0,
                    shiny=bool(shiny),
                    mote=_texto_mote(_clave(n, "Nickname", "nickname")),
                    genero=_clave(n, "Gender", "gender") if isinstance(_clave(n, "Gender", "gender"), str) else None,
                    forma=forma if isinstance(forma, str) else None,
                ))
                return
            for k in sorted(n, key=_orden_natural):
                visitar(n[k])
        elif isinstance(n, list):
            for x in n:
                visitar(x)

    visitar(arbol)
    return res


# --------------------------------------------------------------------- Logros
@dataclass
class Logro:
    id: str
    region: str | None

    @property
    def nombre(self) -> str:
        # COBBLEVERSE: 'cobbleverse:trainer/kanto/defeat_brock' -> 'Brock',
        # 'defeat_champion_blue' -> 'Campeón Blue'
        t = self.id.split(":")[-1].split("/")[-1]
        if t.startswith("defeat_"):
            t = t[len("defeat_"):]
            for prefijo, titulo in (("champion_", "Campeón "), ("elite_", "Alto Mando "), ("boss_", "Jefe ")):
                if t.startswith(prefijo):
                    return titulo + NOMBRES_ENTRENADOR.get(t[len(prefijo):], bonito(t[len(prefijo):]))
            return NOMBRES_ENTRENADOR.get(t, bonito(t))
        return bonito(self.id)


NOMBRES_ENTRENADOR = {"ltsurge": "Lt. Surge"}


def region_de(id_logro: str) -> str | None:
    bajo = id_logro.lower()
    for r in REGIONES:
        if r in bajo:
            return r
    return None


@dataclass
class ReglasLogros:
    espacios: list[str]          # namespaces a mirar ([] = todos menos minecraft)
    palabras_medalla: list[str]
    palabras_liga: list[str]
    ids_medallas: list[str]      # si no están vacías, mandan sobre las palabras
    ids_ligas: list[str]
    excluir: list[str]

    @classmethod
    def desde_config(cls, c: dict) -> "ReglasLogros":
        return cls(
            espacios=[x.lower() for x in c.get("espacios", [])],
            palabras_medalla=[x.lower() for x in c.get("palabras_medalla", ["badge", "medalla"])],
            palabras_liga=[x.lower() for x in c.get("palabras_liga", ["champion", "trophy", "campeon"])],
            ids_medallas=c.get("ids_medallas", []),
            ids_ligas=c.get("ids_ligas", []),
            excluir=[x.lower() for x in c.get("excluir", ["recipes/", "/root"])],
        )

    def relevante(self, id_logro: str) -> bool:
        bajo = id_logro.lower()
        ns = bajo.split(":", 1)[0] if ":" in bajo else "minecraft"
        if any(e in bajo for e in self.excluir):
            return False
        return ns in self.espacios if self.espacios else ns != "minecraft"

    def es_medalla(self, i: str) -> bool:
        if self.ids_medallas:
            return i in self.ids_medallas
        return self.relevante(i) and any(p in i.lower() for p in self.palabras_medalla)

    def es_liga(self, i: str) -> bool:
        if self.ids_ligas:
            return i in self.ids_ligas
        return self.relevante(i) and any(p in i.lower() for p in self.palabras_liga)


# --------------------------------------------------------------------- Dinero
def _buscar_numero(obj, palabras=("dollar", "balance", "money", "amount", "dinero")) -> float | None:
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return float(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(p in k.lower() for p in palabras) and isinstance(v, (int, float, str)):
                try:
                    return float(v)
                except ValueError:
                    pass
        for v in obj.values():
            if isinstance(v, (dict, list)):
                n = _buscar_numero(v, palabras)
                if n is not None:
                    return n
    if isinstance(obj, list):
        for v in obj:
            n = _buscar_numero(v, palabras)
            if n is not None:
                return n
    return None


def _parsear(datos: bytes):
    try:
        return json.loads(datos.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            return nbt.cargar(datos)
        except Exception:
            return None


def dinero_de_estructura(obj) -> dict[str, float]:
    """De un archivo con varios jugadores saca {uuid: dinero}."""
    res: dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if RE_UUID.fullmatch(k):
                n = _buscar_numero(v)
                if n is not None:
                    res[k.lower()] = n
            elif isinstance(v, (dict, list)):
                res.update(dinero_de_estructura(v))
        if not res:
            uuid = _clave(obj, "uuid", "player", "playerUUID", "id")
            if isinstance(uuid, str) and RE_UUID.fullmatch(uuid):
                n = _buscar_numero({k: v for k, v in obj.items() if v is not uuid})
                if n is not None:
                    res[uuid.lower()] = n
    elif isinstance(obj, list):
        for x in obj:
            res.update(dinero_de_estructura(x))
    return res


# --------------------------------------------------------------------- Perfil
@dataclass
class Perfil:
    uuid: str
    nombre: str
    custom: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    equipo: list[Pokemon] = field(default_factory=list)
    pc: list[Pokemon] = field(default_factory=list)
    medallas: list[Logro] = field(default_factory=list)
    ligas: list[Logro] = field(default_factory=list)
    logros: int = 0
    dinero: float | None = None
    skin: str | None = None  # hash de la textura de Mojang (para la cara en los embeds)

    def valor(self, clave: str) -> int:
        return int(self.custom.get(clave, 0))

    @property
    def horas(self) -> float:
        return self.custom.get("minecraft:play_time", self.custom.get("minecraft:play_one_minute", 0)) / TICKS_POR_HORA

    @property
    def distancia_km(self) -> float:
        return sum(self.custom.get(k, 0) for k in CLAVES_DISTANCIA) / 100_000

    @property
    def cobblemon(self) -> dict[str, int]:
        return {k: v for k, v in self.custom.items() if k.startswith("cobblemon:")}

    @property
    def todos_pokemon(self) -> list[Pokemon]:
        return self.equipo + self.pc

    @property
    def shinies(self) -> int:
        return sum(p.shiny for p in self.todos_pokemon)

    @property
    def especies(self) -> int:
        return len({p.especie for p in self.todos_pokemon})

    @property
    def nivel_max(self) -> int:
        return max((p.nivel for p in self.todos_pokemon), default=0)

    def medallas_de(self, region: str | None) -> list[Logro]:
        return [m for m in self.medallas if region is None or m.region == region]

    def ligas_de(self, region: str | None) -> list[Logro]:
        return [l for l in self.ligas if region is None or l.region == region]


class Perfiles:
    def __init__(self, fuente: FuenteArchivos, cfg_archivos: dict, cfg_cobbleverse: dict):
        self.f = fuente
        self.mundo = cfg_archivos.get("carpeta_mundo", "world").strip("/")
        self.cache_segundos = int(cfg_archivos.get("cache_segundos", 300))
        self.reglas = ReglasLogros.desde_config(cfg_cobbleverse)
        self.carpeta_dinero = cfg_cobbleverse.get("carpeta_dinero", "cobbledollarsplayerdata")
        self._cache: list[Perfil] | None = None
        self._t = 0.0
        self._lock = None

    # ---------------- lectura de cada fuente
    async def _nombres(self) -> dict[str, str]:
        datos = await self.f.leer_json("usercache.json") or []
        return {e["uuid"].lower(): e["name"] for e in datos if "uuid" in e and "name" in e}

    async def _almacen(self, tipo: str, uuid: str) -> list[Pokemon]:
        base = f"{self.mundo}/pokemon/{tipo}"
        for ruta in (f"{base}/{uuid[:2]}/{uuid}.dat", f"{base}/{uuid[:2]}/{uuid}.json",
                     f"{base}/{uuid}.dat", f"{base}/{uuid}.json"):
            datos = await self.f.leer_bytes(ruta)
            if datos:
                arbol = _parsear(datos)
                return extraer_pokemon(arbol) if arbol is not None else []
        return []

    async def _logros(self, uuid: str) -> tuple[list[Logro], list[Logro], int]:
        datos = await self.f.leer_json(f"{self.mundo}/advancements/{uuid}.json") or {}
        hechos = [k for k, v in datos.items() if isinstance(v, dict) and v.get("done")]
        medallas = [Logro(i, region_de(i)) for i in hechos if self.reglas.es_medalla(i)]
        ligas = [Logro(i, region_de(i)) for i in hechos if self.reglas.es_liga(i) and not self.reglas.es_medalla(i)]
        total = sum(1 for i in hechos if self.reglas.relevante(i))
        return medallas, ligas, total

    async def _dinero(self) -> dict[str, float]:
        carpeta = f"{self.mundo}/{self.carpeta_dinero}"
        res: dict[str, float] = {}
        for archivo in await self.f.listar(carpeta):
            datos = await self.f.leer_bytes(f"{carpeta}/{archivo}")
            arbol = _parsear(datos) if datos else None
            if arbol is None:
                continue
            m = RE_UUID.search(archivo)
            if m:
                n = _buscar_numero(arbol)
                if n is not None:
                    res[m.group(0).lower()] = n
            else:
                res.update(dinero_de_estructura(arbol))
        return res

    # ---------------- API
    async def todos(self, forzar: bool = False) -> list[Perfil]:
        if not forzar and self._cache is not None and time.time() - self._t < self.cache_segundos:
            return self._cache
        nombres = await self._nombres()
        dinero = await self._dinero()
        perfiles = []
        # Jugadores: los que tienen stats guardadas + los de usercache.json (han entrado alguna vez)
        # + los que tienen dinero. Así sale alguien recién llegado aunque el servidor aún no haya
        # escrito su archivo de stats (eso pasa al autoguardado o cuando sale).
        uuids = {a[:-5].lower() for a in await self.f.listar(f"{self.mundo}/stats") if a.endswith(".json")}
        uuids |= set(nombres) | set(dinero)
        for uuid in sorted(uuids):
            datos = await self.f.leer_json(f"{self.mundo}/stats/{uuid}.json") or {}
            stats = datos.get("stats", {})
            medallas, ligas, total = await self._logros(uuid)
            perfiles.append(Perfil(
                uuid=uuid,
                nombre=nombres.get(uuid, uuid[:8]),
                custom=stats.get("minecraft:custom", {}),
                stats=stats,
                equipo=await self._almacen("playerpartystore", uuid),
                pc=await self._almacen("pcstore", uuid),
                medallas=medallas, ligas=ligas, logros=total,
                dinero=dinero.get(uuid),
            ))
        self._cache, self._t = perfiles, time.time()
        return perfiles

    async def buscar(self, nombre: str) -> Perfil | None:
        nombre = nombre.lower()
        # Si no está en la caché (p. ej. acaba de entrar por primera vez), se relee del disco
        for forzar in (False, True):
            for p in await self.todos(forzar=forzar):
                if p.nombre.lower() == nombre:
                    return p
        return None

    async def nombres(self) -> list[str]:
        return sorted(p.nombre for p in await self.todos())

    async def diagnostico(self, uuid: str) -> dict:
        """Qué hay en cada fuente para un jugador, para ajustar la config."""
        adv = await self.f.leer_json(f"{self.mundo}/advancements/{uuid}.json") or {}
        hechos = [k for k, v in adv.items() if isinstance(v, dict) and v.get("done")]
        rutas_pkm = {}
        for tipo in ("playerpartystore", "pcstore"):
            base = f"{self.mundo}/pokemon/{tipo}"
            encontrado = None
            for ruta in (f"{base}/{uuid[:2]}/{uuid}.dat", f"{base}/{uuid[:2]}/{uuid}.json",
                         f"{base}/{uuid}.dat", f"{base}/{uuid}.json"):
                if await self.f.leer_bytes(ruta):
                    encontrado = ruta
                    break
            rutas_pkm[tipo] = encontrado
        archivos_dinero = await self.f.listar(f"{self.mundo}/{self.carpeta_dinero}")
        return {
            "logros_mods": sorted(i for i in hechos if self.reglas.relevante(i)),
            "rutas_pokemon": rutas_pkm,
            "archivos_dinero": archivos_dinero[:10],
            "n_archivos_dinero": len(archivos_dinero),
        }
