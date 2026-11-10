"""Catálogo de objetos del servidor (Minecraft + todos los mods), para /dar objeto.

Sale de los .jar: los objetos de cada mod son sus modelos de objeto (assets/<mod>/models/item/<id>.json)
y el nombre bonito sale de sus traducciones (es_es si la hay, si no en_us). Los de Minecraft salen de
las traducciones del server.jar.
"""
import json
import re
import zipfile
from dataclasses import dataclass, field

RE_MODELO = re.compile(r"^assets/([a-z0-9_.-]+)/models/item/([a-z0-9_.-]+)\.json$")
RE_CLAVE_LANG = re.compile(r"^(item|block)\.([a-z0-9_.-]+)\.([a-z0-9_]+)$")
IDIOMAS = ("es_es", "es_mx", "en_us")


@dataclass
class Mod:
    id: str                                   # namespace: cobblemon, minecraft…
    nombre: str                               # nombre del mod: Cobblemon, Minecraft…
    objetos: dict[str, str] = field(default_factory=dict)   # id completo -> nombre bonito


def _bonito(t: str) -> str:
    return t.replace("_", " ").strip().title()


def _leer_lang(z: zipfile.ZipFile, ns: str) -> dict[str, str]:
    """Traducciones de un namespace: primero las inglesas y encima las españolas."""
    res: dict[str, str] = {}
    for idioma in reversed(IDIOMAS):
        try:
            res.update(json.loads(z.read(f"assets/{ns}/lang/{idioma}.json").decode("utf-8", "replace")))
        except (KeyError, ValueError):
            pass
    return res


def _nombres_lang(lang: dict[str, str], ns: str) -> dict[str, str]:
    """id -> nombre, a partir de las claves item.<ns>.<id> y block.<ns>.<id> (la de item manda)."""
    bloques, objetos = {}, {}
    for k, v in lang.items():
        m = RE_CLAVE_LANG.match(k)
        if m and m.group(2) == ns and isinstance(v, str):
            (objetos if m.group(1) == "item" else bloques)[m.group(3)] = v
    return {**bloques, **objetos}


def catalogo_de_jars(jars_mods: list, jar_minecraft=None) -> dict[str, Mod]:
    """jars_mods / jar_minecraft: rutas o archivos abribles por zipfile. Devuelve namespace -> Mod."""
    mods: dict[str, Mod] = {}

    if jar_minecraft is not None:
        try:
            with zipfile.ZipFile(jar_minecraft) as z:
                nombres = _nombres_lang(_leer_lang(z, "minecraft"), "minecraft")
            mods["minecraft"] = Mod("minecraft", "Minecraft",
                                    {f"minecraft:{i}": n for i, n in nombres.items()})
        except (OSError, zipfile.BadZipFile):
            pass

    for jar in jars_mods:
        try:
            z = zipfile.ZipFile(jar)
        except (OSError, zipfile.BadZipFile):
            continue
        with z:
            nombre_mod = None
            try:
                meta = json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"), strict=False)
                nombre_mod = (meta.get("id"), meta.get("name"))
            except (KeyError, ValueError):
                pass
            modelos: dict[str, set[str]] = {}
            for n in z.namelist():
                if m := RE_MODELO.match(n):
                    modelos.setdefault(m.group(1), set()).add(m.group(2))
            for ns, ids in modelos.items():
                if ns == "minecraft":
                    continue    # son retoques de modelos vanilla, no objetos nuevos
                nombres = _nombres_lang(_leer_lang(z, ns), ns)
                # Si el mod tiene traducciones, solo cuentan los modelos con nombre (el resto son piezas)
                validos = [i for i in ids if i in nombres] if nombres else sorted(ids)
                if not validos:
                    continue
                titulo = nombre_mod[1] if nombre_mod and nombre_mod[0] == ns and nombre_mod[1] else _bonito(ns)
                mod = mods.setdefault(ns, Mod(ns, titulo))
                for i in validos:
                    mod.objetos[f"{ns}:{i}"] = nombres.get(i) or _bonito(i)

    for mod in mods.values():
        mod.objetos = dict(sorted(mod.objetos.items(), key=lambda kv: kv[1].lower()))
    return dict(sorted(mods.items(), key=lambda kv: (kv[0] != "minecraft", kv[1].nombre.lower())))


def buscar(mods: dict[str, Mod], texto: str, mod: str | None = None, limite: int = 25) -> list[tuple[str, str, str]]:
    """(id, nombre, mod) de los objetos que contienen el texto en el nombre o en el id.
    Primero los que empiezan por el texto."""
    t = texto.strip().lower()
    t_id = t.replace(" ", "_")
    fuentes = [mods[mod]] if mod and mod in mods else mods.values()
    empiezan, contienen = [], []
    for m in fuentes:
        for i, n in m.objetos.items():
            nb, ib = n.lower(), i.split(":", 1)[1]
            if not t or nb.startswith(t) or ib.startswith(t_id):
                empiezan.append((i, n, m.nombre))
            elif t in nb or t_id in i:
                contienen.append((i, n, m.nombre))
            if len(empiezan) >= limite:
                return empiezan
    return (empiezan + contienen)[:limite]
