"""Acceso a los archivos del servidor.

Dos modos:
- "local": el bot está en la misma máquina (o tiene la carpeta del servidor montada).
- "pterodactyl": el servidor está en un hosting con panel Pterodactyl
  (BisectHosting, Apex, etc.) y se leen los archivos por la API del panel.
"""
import json
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import aiohttp


class FuenteArchivos:
    async def leer_texto(self, ruta: str) -> str | None:
        raise NotImplementedError

    async def leer_bytes(self, ruta: str) -> bytes | None:
        raise NotImplementedError

    async def listar(self, carpeta: str) -> list[str]:
        """Devuelve los nombres de archivo de una carpeta (sin subcarpetas)."""
        raise NotImplementedError

    def ruta_local(self, ruta: str) -> Path | None:
        """Ruta en disco si los archivos están en esta máquina (para abrir .jar sin cargarlos enteros)."""
        return None

    async def leer_json(self, ruta: str):
        texto = await self.leer_texto(ruta)
        if texto is None:
            return None
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            return None

    async def cerrar(self):
        pass


class ArchivosLocales(FuenteArchivos):
    def __init__(self, carpeta_servidor: str):
        self.base = Path(carpeta_servidor).expanduser().resolve()

    def _ruta(self, ruta: str) -> Path:
        p = (self.base / ruta).resolve()
        if self.base not in p.parents and p != self.base:
            raise ValueError("Ruta fuera de la carpeta del servidor")
        return p

    def ruta_local(self, ruta: str) -> Path | None:
        return self._ruta(ruta)

    async def leer_texto(self, ruta: str) -> str | None:
        p = self._ruta(ruta)
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")

    async def leer_bytes(self, ruta: str) -> bytes | None:
        p = self._ruta(ruta)
        return p.read_bytes() if p.is_file() else None

    async def listar(self, carpeta: str) -> list[str]:
        p = self._ruta(carpeta)
        if not p.is_dir():
            return []
        return sorted(x.name for x in p.iterdir() if x.is_file())


class ArchivosPterodactyl(FuenteArchivos):
    """Usa la API de cliente de Pterodactyl: /api/client/servers/<id>/files/..."""

    def __init__(self, url_panel: str, api_key: str, id_servidor: str):
        self.url = url_panel.rstrip("/")
        self.id = id_servidor
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        self._sesion: aiohttp.ClientSession | None = None

    async def _s(self) -> aiohttp.ClientSession:
        if self._sesion is None or self._sesion.closed:
            self._sesion = aiohttp.ClientSession(
                headers=self.headers, timeout=aiohttp.ClientTimeout(total=20)
            )
        return self._sesion

    @staticmethod
    def _normalizar(ruta: str) -> str:
        return "/" + str(PurePosixPath(ruta)).lstrip("/")

    async def leer_texto(self, ruta: str) -> str | None:
        s = await self._s()
        url = f"{self.url}/api/client/servers/{self.id}/files/contents?file={quote(self._normalizar(ruta))}"
        async with s.get(url) as r:
            if r.status == 404:
                return None
            r.raise_for_status()
            return await r.text()

    async def leer_bytes(self, ruta: str) -> bytes | None:
        s = await self._s()
        url = f"{self.url}/api/client/servers/{self.id}/files/contents?file={quote(self._normalizar(ruta))}"
        async with s.get(url) as r:
            if r.status == 404:
                return None
            r.raise_for_status()
            return await r.read()

    async def listar(self, carpeta: str) -> list[str]:
        s = await self._s()
        url = f"{self.url}/api/client/servers/{self.id}/files/list?directory={quote(self._normalizar(carpeta))}"
        async with s.get(url) as r:
            if r.status == 404:
                return []
            r.raise_for_status()
            datos = await r.json()
        return sorted(
            item["attributes"]["name"]
            for item in datos.get("data", [])
            if item.get("attributes", {}).get("is_file")
        )

    async def cerrar(self):
        if self._sesion and not self._sesion.closed:
            await self._sesion.close()


def crear_fuente(cfg: dict) -> FuenteArchivos | None:
    modo = cfg.get("modo", "local").lower()
    if modo == "local":
        if not cfg.get("carpeta_servidor"):
            return None
        return ArchivosLocales(cfg["carpeta_servidor"])
    if modo == "pterodactyl":
        p = cfg["pterodactyl"]
        return ArchivosPterodactyl(p["url_panel"], p["api_key"], p["id_servidor"])
    if modo == "ninguno":
        return None
    raise ValueError(f"Modo de archivos desconocido: {modo}")
