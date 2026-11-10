"""Categorías para /top. Cada una: título, cómo se calcula, formato y si admite filtro de región."""
from dataclasses import dataclass
from typing import Callable

from .perfiles import NOMBRES_COBBLEMON, Perfil


def f_int(v) -> str:
    return f"{int(v):,}".replace(",", ".")


def f_dec(v, sufijo="") -> str:
    return f"{v:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + sufijo


@dataclass
class Categoria:
    titulo: str
    valor: Callable[[Perfil, str | None], float]
    formato: Callable[[float], str]
    por_region: bool = False


def construir(extra_cobblemon: dict[str, str], simbolo_dinero: str = "₽") -> dict[str, Categoria]:
    c = {
        "horas": Categoria("Horas jugadas", lambda p, r: p.horas, lambda v: f_dec(v, " h")),
        "dinero": Categoria("Dinero", lambda p, r: p.dinero or 0, lambda v: f"{f_int(v)} {simbolo_dinero}"),
        "medallas": Categoria("Medallas", lambda p, r: len(p.medallas_de(r)), f_int, por_region=True),
        "ligas": Categoria("Ligas completadas", lambda p, r: len(p.ligas_de(r)), f_int, por_region=True),
        "pokemon": Categoria("Pokémon en propiedad", lambda p, r: len(p.todos_pokemon), f_int),
        "shinies": Categoria("Shinies en propiedad", lambda p, r: p.shinies, f_int),
        "especies": Categoria("Especies distintas", lambda p, r: p.especies, f_int),
        "nivel_max": Categoria("Pokémon de mayor nivel", lambda p, r: p.nivel_max, lambda v: f"Nv. {int(v)}"),
        "logros": Categoria("Logros de COBBLEVERSE", lambda p, r: p.logros, f_int),
        "muertes": Categoria("Muertes", lambda p, r: p.valor("minecraft:deaths"), f_int),
        "distancia": Categoria("Distancia recorrida", lambda p, r: p.distancia_km, lambda v: f_dec(v, " km")),
        "mobs": Categoria("Mobs eliminados", lambda p, r: p.valor("minecraft:mob_kills"), f_int),
    }
    for id_cat, clave in extra_cobblemon.items():
        c[id_cat] = Categoria(
            NOMBRES_COBBLEMON.get(clave, clave),
            (lambda k: lambda p, r: p.valor(k))(clave),
            f_int,
        )
    return c
