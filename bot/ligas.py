"""Avisos y roles de Discord al completar una liga (vencer al campeón de una región).

- El bot compara las ligas de cada jugador (logros de COBBLEVERSE) con las que ya tenía apuntadas.
- Cada liga nueva se anuncia en el canal elegido y, si el jugador tiene su Discord vinculado
  (/team vincular), se le pone el rol de esa región.
- La primera vez solo apunta lo que ya hay (no anuncia ligas antiguas), pero sí pone los roles.

Todo se guarda en la carpeta de datos del BOT (datos/ligas.json), nunca en el servidor.
"""
import json
import os
from pathlib import Path


class EstadoLigas:
    def __init__(self, carpeta: str | Path):
        self.ruta = Path(carpeta) / "ligas.json"
        self.canal: int | None = None              # canal de Discord para los anuncios
        self.roles: dict[str, int] = {}            # región -> ID del rol de Discord
        self.vistas: dict[str, list[str]] = {}     # uuid del jugador -> ids de las ligas ya anunciadas
        self.iniciado = False                      # False hasta la primera lectura (no se anuncia lo antiguo)
        try:
            d = json.loads(self.ruta.read_text(encoding="utf-8"))
            self.canal = int(d["canal"]) if d.get("canal") else None
            self.roles = {str(r): int(i) for r, i in (d.get("roles") or {}).items()}
            self.vistas = {str(u): list(v) for u, v in (d.get("vistas") or {}).items()}
            self.iniciado = bool(d.get("iniciado"))
        except (OSError, ValueError, TypeError):
            pass

    def guardar(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"canal": self.canal, "roles": self.roles, "iniciado": self.iniciado,
                                   "vistas": self.vistas}, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.ruta)

    def poner_canal(self, canal: int | None):
        self.canal = canal
        self.guardar()

    def poner_rol(self, region: str, rol: int | None):
        if rol:
            self.roles[region] = rol
        else:
            self.roles.pop(region, None)
        self.guardar()

    def nuevas(self, perfiles) -> list[tuple]:
        """(perfil, liga) de las ligas que no estaban apuntadas. Solo añade: si una lectura falla y no
        trae a alguien, no se le borra nada. La primera vez apunta todo sin devolver nada."""
        res, cambio = [], False
        for p in perfiles:
            if not p.ligas:
                continue
            vistas = self.vistas.setdefault(p.uuid, [])
            for liga in p.ligas:
                if liga.id not in vistas:
                    vistas.append(liga.id)
                    cambio = True
                    if self.iniciado:
                        res.append((p, liga))
        if not self.iniciado:
            self.iniciado, cambio = True, True
        if cambio:
            self.guardar()
        return res
