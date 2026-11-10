"""Texto con formato de Minecraft (JSON text components) para tellraw y title.

Admite códigos con & dentro del texto, como en muchos servidores:
  &0-&9 &a-&f  colores        &l negrita   &o cursiva   &n subrayado
  &m tachado   &k ofuscado    &r quitar formato         &#RRGGBB color exacto
Ejemplo: "&6&lCobbleverse &rha abierto la &bliga de Kanto"
"""
import json
import re

COLORES = {
    "black": "Negro", "dark_blue": "Azul oscuro", "dark_green": "Verde oscuro", "dark_aqua": "Turquesa oscuro",
    "dark_red": "Rojo oscuro", "dark_purple": "Morado", "gold": "Dorado", "gray": "Gris", "dark_gray": "Gris oscuro",
    "blue": "Azul", "green": "Verde", "aqua": "Turquesa", "red": "Rojo", "light_purple": "Rosa",
    "yellow": "Amarillo", "white": "Blanco",
}
CODIGOS_COLOR = dict(zip("0123456789abcdef", COLORES))
CODIGOS_ESTILO = {"l": "bold", "o": "italic", "n": "underlined", "m": "strikethrough", "k": "obfuscated"}
RE_CODIGO = re.compile(r"&(#[0-9a-fA-F]{6}|[0-9a-fA-FlLoOnNmMkKrR])")


def componentes(texto: str, base: dict | None = None) -> list:
    """Convierte el texto (con códigos &) en una lista JSON para tellraw/title.
    'base' es el estilo por defecto (color, negrita…) que se aplica a todo el texto."""
    base = {k: v for k, v in (base or {}).items() if v not in (None, False)}
    partes: list = [""]          # el "" inicial evita que un trozo herede el estilo del primero
    estilo = dict(base)
    pos = 0
    for m in RE_CODIGO.finditer(texto):
        if m.start() > pos:
            partes.append({"text": texto[pos:m.start()], **estilo})
        c = m.group(1)
        if c.startswith("#"):
            estilo = {k: v for k, v in estilo.items() if k != "color"} | {"color": c.upper()}
        elif c.lower() in CODIGOS_COLOR:
            # Como en Minecraft: un color nuevo quita los estilos anteriores
            estilo = {"color": CODIGOS_COLOR[c.lower()]}
        elif c.lower() == "r":
            estilo = dict(base)
        else:
            estilo[CODIGOS_ESTILO[c.lower()]] = True
        pos = m.end()
    if pos < len(texto):
        partes.append({"text": texto[pos:], **estilo})
    return partes


def a_json(partes: list) -> str:
    return json.dumps(partes, ensure_ascii=False)


def texto_plano(texto: str) -> str:
    """Sin códigos &, para mostrarlo en Discord."""
    return RE_CODIGO.sub("", texto)
