"""Mensaje fijo con el estado del servidor: el bot lo publica una vez en un canal y lo va editando solo.

Muestra si el servidor está online, quién está conectado (con la [etiqueta] de su team) y desde cuándo está
encendido. Se cambia cuando cambia algo (alguien entra o sale, se cae, vuelve) y, como mucho, cada pocos minutos
para que se vea que está al día. Se guarda en la carpeta de datos del BOT (datos/estado_fijo.json).
"""
import json
import os
import time
from pathlib import Path

import discord

REFRESCO_SEGUNDOS = 300      # aunque no cambie nada, se reescribe cada 5 min ("actualizado hace…")


class EstadoFijo:
    def __init__(self, carpeta: str | Path):
        self.ruta = Path(carpeta) / "estado_fijo.json"
        self.canal: int | None = None
        self.mensaje: int | None = None
        self.online_desde: float | None = None     # desde cuándo lo ve encendido el bot
        self.caido_desde: float | None = None
        self._firma: tuple | None = None
        self._ultima = 0.0
        try:
            d = json.loads(self.ruta.read_text(encoding="utf-8"))
            self.canal = int(d["canal"]) if d.get("canal") else None
            self.mensaje = int(d["mensaje"]) if d.get("mensaje") else None
            self.online_desde = d.get("online_desde")
            self.caido_desde = d.get("caido_desde")
        except (OSError, ValueError, TypeError):
            pass

    def guardar(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"canal": self.canal, "mensaje": self.mensaje, "online_desde": self.online_desde,
                                   "caido_desde": self.caido_desde}, indent=2), encoding="utf-8")
        os.replace(tmp, self.ruta)

    def poner(self, canal: int | None, mensaje: int | None):
        self.canal, self.mensaje, self._firma = canal, mensaje, None
        self.guardar()

    def marcar(self, online: bool, ahora: float | None = None):
        """Apunta desde cuándo está encendido (o caído). Solo cambia al pasar de un estado al otro."""
        ahora = ahora or time.time()
        if online and not self.online_desde:
            self.online_desde, self.caido_desde = ahora, None
            self.guardar()
        elif not online and not self.caido_desde:
            self.online_desde, self.caido_desde = None, ahora
            self.guardar()

    def toca(self, firma: tuple, ahora: float | None = None) -> bool:
        """¿Hay que editar el mensaje? Si ha cambiado algo, o hace rato que no se toca."""
        ahora = ahora or time.time()
        if firma != self._firma or ahora - self._ultima >= REFRESCO_SEGUNDOS:
            self._firma, self._ultima = firma, ahora
            return True
        return False


def embed_estado(online: bool, conectados: list[tuple[str, str]], maximo: int, desde: float | None,
                 reiniciando: str | None = None, direccion: str = "") -> discord.Embed:
    """conectados: [(jugador, etiqueta del team o '')]."""
    if reiniciando and not online:
        e = discord.Embed(title="🟡 Reiniciando…", color=0xE5A50A,
                          description=f"Reinicio pedido por **{reiniciando}**. Vuelve en un momento.")
    elif not online:
        e = discord.Embed(title="🔴 Servidor offline", color=0x8B0000,
                          description="No responde ahora mismo. Este mensaje cambia en cuanto vuelva.")
    else:
        e = discord.Embed(title="🟢 Servidor online", color=0x2E8B57,
                          description="¡Se puede entrar!" if len(conectados) < maximo else "Está lleno ahora mismo.")
    if direccion:
        e.add_field(name="🌐 Dirección", value=f"`{direccion}`", inline=False)
    if online:
        e.add_field(name="👥 Jugadores", value=f"**{len(conectados)}**/{maximo}")
    if desde:
        e.add_field(name="⏱️ Encendido" if online else "⏱️ Caído", value=f"<t:{int(desde)}:R>")
    if online:
        filas = [f"• {discord.utils.escape_markdown(n)}" + (f" `[{t}]`" if t else "") for n, t in conectados]
        e.add_field(name="🎮 Conectados", value="\n".join(filas)[:1024] or "Nadie ahora mismo", inline=False)
    e.set_footer(text="Se actualiza solo · última vez")
    e.timestamp = discord.utils.utcnow()
    return e
