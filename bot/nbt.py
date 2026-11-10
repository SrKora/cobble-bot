"""Lector mínimo de NBT (formato binario de Minecraft Java) -> tipos de Python.

Sirve para los archivos .dat de Cobblemon (equipo y PC de cada jugador).
Acepta archivos comprimidos con gzip/zlib o sin comprimir.
"""
import gzip
import struct
import zlib


class _Lector:
    def __init__(self, datos: bytes):
        self.d = datos
        self.i = 0

    def leer(self, n: int) -> bytes:
        b = self.d[self.i:self.i + n]
        if len(b) < n:
            raise ValueError("NBT truncado")
        self.i += n
        return b

    def u(self, fmt: str):
        tam = struct.calcsize(fmt)
        return struct.unpack(">" + fmt, self.leer(tam))[0]

    def texto(self) -> str:
        n = self.u("H")
        return self.leer(n).decode("utf-8", errors="replace")

    def payload(self, tipo: int):
        if tipo == 1: return self.u("b")
        if tipo == 2: return self.u("h")
        if tipo == 3: return self.u("i")
        if tipo == 4: return self.u("q")
        if tipo == 5: return self.u("f")
        if tipo == 6: return self.u("d")
        if tipo == 7:
            n = self.u("i")
            return list(self.leer(n))
        if tipo == 8: return self.texto()
        if tipo == 9:
            sub = self.u("b")
            n = self.u("i")
            return [self.payload(sub) for _ in range(max(n, 0))]
        if tipo == 10:
            res = {}
            while True:
                t = self.u("b")
                if t == 0:
                    return res
                nombre = self.texto()
                res[nombre] = self.payload(t)
        if tipo == 11:
            n = self.u("i")
            return list(struct.unpack(f">{n}i", self.leer(4 * n)))
        if tipo == 12:
            n = self.u("i")
            return list(struct.unpack(f">{n}q", self.leer(8 * n)))
        raise ValueError(f"Tipo NBT desconocido: {tipo}")


def cargar(datos: bytes) -> dict:
    if datos[:2] == b"\x1f\x8b":
        datos = gzip.decompress(datos)
    elif datos[:1] == b"\x78":
        try:
            datos = zlib.decompress(datos)
        except zlib.error:
            pass
    r = _Lector(datos)
    tipo = r.u("b")
    if tipo != 10:
        raise ValueError("El NBT no empieza por un compound")
    r.texto()  # nombre raíz (suele estar vacío)
    return r.payload(10)


# --- Escritura (solo para las pruebas) -----------------------------------------
def _w_texto(s: str) -> bytes:
    b = s.encode()
    return struct.pack(">H", len(b)) + b


def _tipo(v) -> int:
    if isinstance(v, bool): return 1
    if isinstance(v, int): return 3
    if isinstance(v, float): return 6
    if isinstance(v, str): return 8
    if isinstance(v, list): return 9
    if isinstance(v, dict): return 10
    raise TypeError(type(v))


def _w(v) -> bytes:
    t = _tipo(v)
    if t == 1: return struct.pack(">b", int(v))
    if t == 3: return struct.pack(">i", v)
    if t == 6: return struct.pack(">d", v)
    if t == 8: return _w_texto(v)
    if t == 9:
        sub = _tipo(v[0]) if v else 0
        return struct.pack(">bi", sub, len(v)) + b"".join(_w(x) for x in v)
    out = b""
    for k, x in v.items():
        out += struct.pack(">b", _tipo(x)) + _w_texto(k) + _w(x)
    return out + b"\x00"


def guardar(datos: dict) -> bytes:
    return gzip.compress(b"\x0a" + _w_texto("") + _w(datos))
