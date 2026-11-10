"""Cliente RCON asíncrono mínimo para servidores de Minecraft.

Protocolo: https://wiki.vg/RCON
Cada paquete: longitud (int32 LE) | id (int32 LE) | tipo (int32 LE) | payload | \x00\x00
"""
import asyncio
import struct

TIPO_LOGIN = 3
TIPO_COMANDO = 2


class RconError(Exception):
    pass


class RconClient:
    def __init__(self, host: str, puerto: int, password: str, timeout: float = 8.0):
        self.host = host
        self.puerto = puerto
        self.password = password
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._id = 0

    def _siguiente_id(self) -> int:
        self._id = (self._id + 1) % 2_000_000_000
        return self._id

    @staticmethod
    def _paquete(req_id: int, tipo: int, texto: str) -> bytes:
        cuerpo = struct.pack("<ii", req_id, tipo) + texto.encode("utf-8") + b"\x00\x00"
        return struct.pack("<i", len(cuerpo)) + cuerpo

    @staticmethod
    async def _leer(reader: asyncio.StreamReader) -> tuple[int, int, str]:
        cab = await reader.readexactly(4)
        (longitud,) = struct.unpack("<i", cab)
        if longitud < 10 or longitud > 1_000_000:
            raise RconError(f"Paquete RCON con longitud inválida: {longitud}")
        datos = await reader.readexactly(longitud)
        req_id, tipo = struct.unpack("<ii", datos[:8])
        texto = datos[8:-2].decode("utf-8", errors="replace")
        return req_id, tipo, texto

    async def comando(self, cmd: str) -> str:
        """Envía un comando y devuelve la respuesta del servidor (sin la barra inicial)."""
        cmd = cmd.strip().lstrip("/")
        async with self._lock:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.puerto), self.timeout
                )
            except (OSError, asyncio.TimeoutError) as e:
                raise RconError(f"No se pudo conectar por RCON a {self.host}:{self.puerto} ({e})") from e
            try:
                login_id = self._siguiente_id()
                writer.write(self._paquete(login_id, TIPO_LOGIN, self.password))
                await writer.drain()
                resp_id, _, _ = await asyncio.wait_for(self._leer(reader), self.timeout)
                if resp_id == -1:
                    raise RconError("Contraseña RCON incorrecta")

                cmd_id = self._siguiente_id()
                writer.write(self._paquete(cmd_id, TIPO_COMANDO, cmd))
                await writer.drain()
                _, _, texto = await asyncio.wait_for(self._leer(reader), self.timeout)
                return texto
            except asyncio.TimeoutError as e:
                raise RconError("El servidor no respondió a tiempo por RCON") from e
            except asyncio.IncompleteReadError as e:
                raise RconError("El servidor cerró la conexión RCON") from e
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
