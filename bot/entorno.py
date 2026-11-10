"""Variables de entorno y archivo .env.

Las claves (token, contraseña RCON) y lo que cambia de una instalación a otra (IDs de
Discord, rutas) van en variables de entorno. config.toml queda solo para el ajuste fino
(comandos permitidos, medallas, rankings…) y se puede compartir sin peligro.

Prioridad: variable de entorno real > archivo .env > config.toml.
"""
import os
from pathlib import Path

# variable -> (sección, clave, tipo)
VARIABLES: dict[str, tuple[str, str, str]] = {
    "DISCORD_TOKEN":          ("discord", "token", "str"),
    "DISCORD_GUILD_ID":       ("discord", "guild_id", "int"),
    "DISCORD_CANAL_AVISOS":   ("discord", "canal_avisos", "int"),
    "DISCORD_CANAL_REGISTRO": ("discord", "canal_registro", "int"),
    "DISCORD_CANAL_ESTADO":   ("discord", "canal_estado", "int"),
    "DISCORD_RESPUESTAS_PUBLICAS": ("discord", "respuestas_publicas", "bool"),
    "DISCORD_ROLES_ADMIN":    ("discord", "roles_admin", "lista_int"),
    "DISCORD_ROLES_REGALOS":  ("discord", "roles_regalos", "lista_int"),
    "RCON_HOST":              ("rcon", "host", "str"),
    "RCON_PORT":              ("rcon", "puerto", "int"),
    "RCON_PASSWORD":          ("rcon", "password", "str"),
    "MC_CARPETA_SERVIDOR":    ("archivos", "carpeta_servidor", "str"),
    "MC_CARPETA_MUNDO":       ("archivos", "carpeta_mundo", "str"),
    "ACTUALIZAR_MINUTOS":     ("archivos", "actualizar_minutos", "float"),
    "MONITOR_SEGUNDOS":       ("monitor", "intervalo_segundos", "int"),
    "AVISAR_ENTRADAS":        ("monitor", "avisar_entradas", "bool"),
    "MENSAJES_NOMBRE":        ("mensajes", "nombre", "str"),
    "MENSAJES_COLOR":         ("mensajes", "color", "str"),
}


def cargar_dotenv(ruta: Path) -> None:
    """Lee un .env sencillo (CLAVE=valor). No pisa variables que ya existan."""
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave, valor = clave.strip(), valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        elif " #" in valor:
            valor = valor.split(" #", 1)[0].rstrip()
        os.environ.setdefault(clave, valor)


def _convertir(valor: str, tipo: str):
    if tipo == "int":
        return int(valor) if valor else 0
    if tipo == "bool":
        return valor.lower() in ("1", "true", "si", "sí", "yes", "on")
    if tipo == "float":
        return float(valor) if valor else 0.0
    if tipo == "lista_int":
        return [int(x) for x in valor.replace(";", ",").split(",") if x.strip()]
    return valor


def aplicar_entorno(cfg: dict) -> dict:
    """Mete en cfg los valores de las variables de entorno que estén definidas."""
    for var, (seccion, clave, tipo) in VARIABLES.items():
        valor = os.environ.get(var)
        if valor is None:
            continue
        try:
            cfg.setdefault(seccion, {})[clave] = _convertir(valor.strip(), tipo)
        except ValueError:
            raise SystemExit(f"La variable {var} tiene un valor no válido: {valor!r}")
    return cfg
