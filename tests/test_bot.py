"""Pruebas sin Discord: RCON contra un servidor falso y un mundo de COBBLEVERSE falso."""
import asyncio
import json
import struct
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import nbt
from bot.archivos import ArchivosLocales
from bot.main import CobbleBot, embed_equipo, embed_medallas, embed_perfil, nombre_jugador, parsear_list
from bot.perfiles import Perfiles, dinero_de_estructura, extraer_pokemon
from bot.rcon import RconClient, RconError
from bot.teams import (comandos_crear, comandos_editar, etiqueta_de, id_para, leer_scoreboard, parsear_miembros,
                       texto_de_json)

PASS = "secreta"
ANA = "11111111-1111-1111-1111-111111111111"
LUIS = "22222222-2222-2222-2222-222222222222"


async def servidor_rcon_falso(reader, writer):
    async def leer():
        (n,) = struct.unpack("<i", await reader.readexactly(4))
        d = await reader.readexactly(n)
        rid, tipo = struct.unpack("<ii", d[:8])
        return rid, tipo, d[8:-2].decode()

    def enviar(rid, tipo, txt):
        cuerpo = struct.pack("<ii", rid, tipo) + txt.encode() + b"\x00\x00"
        writer.write(struct.pack("<i", len(cuerpo)) + cuerpo)

    rid, _, pw = await leer()
    if pw != PASS:
        enviar(-1, 2, "")
        await writer.drain()
        writer.close()
        return
    enviar(rid, 2, "")
    rid, _, cmd = await leer()
    enviar(rid, 0, "There are 2 of a max of 20 players online: §aAna, Luis" if cmd in ("list", "list uuids")
             else f"ok: {cmd}")
    await writer.drain()
    writer.close()


def pkm(especie, nivel, shiny=False, mote=None, genero="MALE"):
    d = {"Species": f"cobblemon:{especie}", "Level": nivel, "Shiny": shiny, "Gender": genero}
    if mote:
        d["Nickname"] = json.dumps({"text": mote})
    return d


def crear_mundo(base: Path):
    w = base / "world"
    for c in ("stats", "advancements", "cobbledollarsplayerdata",
              f"pokemon/playerpartystore/{ANA[:2]}", f"pokemon/pcstore/{ANA[:2]}",
              f"pokemon/playerpartystore/{LUIS[:2]}"):
        (w / c).mkdir(parents=True, exist_ok=True)
    (base / "usercache.json").write_text(json.dumps([{"name": "Ana", "uuid": ANA}, {"name": "Luis", "uuid": LUIS}]))

    for uuid, custom in {
        ANA: {"minecraft:play_time": 72000 * 5, "minecraft:deaths": 3, "minecraft:walk_one_cm": 250_000,
              "cobblemon:captured": 40, "cobblemon:shinies_captured": 2},
        LUIS: {"minecraft:play_time": 72000 * 9, "minecraft:deaths": 10, "cobblemon:captured": 12},
    }.items():
        (w / f"stats/{uuid}.json").write_text(json.dumps({"stats": {"minecraft:custom": custom}}))

    # Medallas/ligas como logros
    hecho = {"criteria": {"x": "2026-01-01"}, "done": True}
    (w / f"advancements/{ANA}.json").write_text(json.dumps({
        **{f"cobbleverse:trainer/kanto/defeat_{n}": hecho
           for n in ("brock", "misty", "ltsurge", "erika", "koga", "sabrina", "blaine", "giovanni")},
        "cobbleverse:trainer/kanto/defeat_elite_lorelei": hecho,   # Alto Mando: ni medalla ni liga
        "cobbleverse:trainer/kanto/defeat_champion_blue": hecho,
        "cobbleverse:trainer/johto/defeat_valerio": hecho,
        "cobbleverse:trainer/johto/defeat_chiara": {"criteria": {}, "done": False},
        "cobbleverse:kanto/root": hecho,
        "minecraft:story/mine_stone": hecho,
        "minecraft:recipes/misc/foo": hecho,
        "DataVersion": 3955,
    }))
    (w / f"advancements/{LUIS}.json").write_text(json.dumps({
        "cobbleverse:trainer/kanto/defeat_brock": hecho, "cobbleverse:trainer/kanto/defeat_misty": hecho}))

    # Equipo (NBT gzip, como lo guarda Cobblemon) y PC
    (w / f"pokemon/playerpartystore/{ANA[:2]}/{ANA}.dat").write_bytes(nbt.guardar({
        "Slot0": pkm("charizard", 62, mote="Chari"), "Slot1": pkm("gengar", 58, shiny=True, genero="FEMALE"),
        "Slot2": pkm("lapras", 55), "SlotCount": 6}))
    (w / f"pokemon/pcstore/{ANA[:2]}/{ANA}.dat").write_bytes(nbt.guardar({
        "Box0": {"Slot0": pkm("pidgey", 5), "Slot1": pkm("pidgey", 7, shiny=True)},
        "Box1": {"Slot0": pkm("magikarp", 10)}, "BoxCount": 30}))
    (w / f"pokemon/playerpartystore/{LUIS[:2]}/{LUIS}.json").write_text(json.dumps({
        "Slot0": {"species": "cobblemon:pikachu", "level": 30, "shiny": False}}))

    # Dinero: un archivo por jugador + uno combinado
    (w / f"cobbledollarsplayerdata/{ANA}.json").write_text(json.dumps({"name": "Ana", "cobbledollars": 15400}))
    (w / "cobbledollarsplayerdata/leaderboard.json").write_text(json.dumps({LUIS: {"balance": 820}}))


async def main():
    srv = await asyncio.start_server(servidor_rcon_falso, "127.0.0.1", 0)
    puerto = srv.sockets[0].getsockname()[1]

    # --- RCON
    r = RconClient("127.0.0.1", puerto, PASS)
    assert parsear_list(await r.comando("list")) == (2, 20, ["Ana", "Luis"])
    # Los nombres salen a secas aunque el servidor les ponga la [etiqueta] del team o el uuid
    assert parsear_list("There are 2 of a max of 10 players online: [MVP] Ash_K, §7[WGA] §fAna (00000000-0000-0000-0000-000000000001)")         == (2, 10, ["Ash_K", "Ana"])
    assert [nombre_jugador(t) for t in ("🟢 Ash_K (conectado)", "[Los Dragones] Pepe_1", "Luis", "@a")]         == ["Ash_K", "Pepe_1", "Luis", "@a"]
    assert await r.comando("/say hola") == "ok: say hola"
    try:
        await RconClient("127.0.0.1", puerto, "mala").comando("list")
        raise AssertionError
    except RconError as e:
        assert "Contraseña" in str(e)
    print("RCON OK")

    # --- NBT ida y vuelta
    arbol = {"a": 1, "b": "x", "c": [{"Species": "cobblemon:eevee", "Level": 3}], "d": True}
    assert nbt.cargar(nbt.guardar(arbol))["c"][0]["Species"] == "cobblemon:eevee"
    assert [p.especie for p in extraer_pokemon({"Slot10": pkm("b", 1), "Slot2": pkm("a", 1)})] == ["cobblemon:a", "cobblemon:b"]
    assert dinero_de_estructura([{"uuid": LUIS, "money": 5}]) == {LUIS: 5.0}
    print("NBT OK")

    with tempfile.TemporaryDirectory() as tmp:
        crear_mundo(Path(tmp))
        cfg = tomllib.loads((Path(__file__).parents[1] / "config.ejemplo.toml").read_text(encoding="utf-8"))
        cfg["archivos"]["carpeta_servidor"] = tmp
        bot = CobbleBot(cfg)
        perf: Perfiles = bot.perfiles

        ana = await perf.buscar("ana")
        assert ana.horas == 5.0 and ana.distancia_km == 2.5
        assert [p.nombre for p in ana.equipo] == ["Charizard", "Gengar", "Lapras"]
        assert ana.equipo[0].mote == "Chari" and ana.equipo[1].shiny
        assert len(ana.pc) == 3 and ana.shinies == 2 and ana.especies == 5 and ana.nivel_max == 62
        assert len(ana.medallas) == 9, [m.id for m in ana.medallas]
        assert len(ana.medallas_de("kanto")) == 8 and len(ana.medallas_de("johto")) == 1
        assert [l.id for l in ana.ligas] == ["cobbleverse:trainer/kanto/defeat_champion_blue"]
        assert ana.ligas[0].nombre == "Campeón Blue" and "Lt. Surge" in [m.nombre for m in ana.medallas]
        assert ana.logros == 11  # 8+1+1+1 (no cuenta root, recetas, vanilla ni el no completado)
        assert ana.dinero == 15400

        luis = await perf.buscar("Luis")
        assert luis.equipo[0].nombre == "Pikachu" and luis.dinero == 820 and luis.pc == []
        print("Perfiles OK")

        # --- Rankings con filtros
        todos = await perf.todos()
        def top(cat, region=None):
            c = bot.categorias[cat]
            return [p.nombre for p in sorted(todos, key=lambda p: c.valor(p, region), reverse=True) if c.valor(p, region) > 0]
        assert top("horas") == ["Luis", "Ana"]
        assert top("medallas") == ["Ana", "Luis"]
        assert top("medallas", "johto") == ["Ana"]
        assert top("ligas") == ["Ana"]
        assert top("dinero") == ["Ana", "Luis"]
        assert top("capturas") == ["Ana", "Luis"]
        assert bot.categorias["dinero"].formato(15400) == "15.400 ₽"
        print("Rankings OK")

        # --- Los embeds se generan dentro de los límites de Discord
        for e in (embed_perfil(bot, ana), embed_perfil(bot, luis), embed_equipo(ana), embed_equipo(luis),
                  embed_medallas(ana, None), embed_medallas(ana, "johto"), embed_medallas(luis, "sinnoh")):
            assert len(e) <= 6000 and all(len(f.value) <= 1024 for f in e.fields)
        print("\n".join(f"{f.name}: {f.value}" for f in embed_perfil(bot, ana).fields))

        d = await perf.diagnostico(ANA)
        assert d["rutas_pokemon"]["playerpartystore"].endswith(".dat") and d["n_archivos_dinero"] == 2
        assert bot.comando_permitido("/whitelist add Pepe") and not bot.comando_permitido("stop")
        print("Embeds y diagnóstico OK")

        # --- Teams: lectura del scoreboard del mundo, nombres internos, comandos y miembros
        sb = nbt.cargar(nbt.guardar({"DataVersion": 3955, "data": {"Teams": [
            {"Name": "rojo", "DisplayName": '{"text":"Equipo Rojo","color":"red"}', "TeamColor": "red",
             "MemberNamePrefix": '{"text":"[R] ","color":"red"}', "AllowFriendlyFire": 0, "Players": ["Ana"]},
            {"Name": "azul", "DisplayName": '["",{"text":"Los "},{"text":"Azules","bold":true}]', "TeamColor": "blue",
             "MemberNamePrefix": '""', "AllowFriendlyFire": 1, "Players": []},
        ]}}))
        teams = leer_scoreboard(sb)
        pre_json_drg = ('{"text":"[","color":"gray","extra":[{"text":"DRG","color":"red"},'
                        '{"text":"] ","color":"gray"}]}')
        assert [t.titulo for t in teams.values()] == ["Equipo Rojo", "Los Azules"]          # orden por nombre
        assert teams["rojo"].prefijo == "[R] " and not teams["rojo"].fuego_amigo and teams["azul"].fuego_amigo
        assert teams["rojo"].miembros_guardados == ("Ana",)
        assert id_para("Los Dragones Ñ!", teams) == "los_dragones_n"
        assert id_para("Rojo", {"rojo": 1}) == "rojo_2"                                       # no pisa uno existente
        cmds = comandos_crear("rojo", "Equipo Rojo", "red", "[R] ", False)
        assert cmds[0] == 'team add rojo {"text": "Equipo Rojo", "color": "red"}'
        assert "team modify rojo friendlyFire false" in cmds and "team modify rojo color reset" in cmds
        assert comandos_editar("rojo", fuego_amigo=True) == ["team modify rojo friendlyFire true"]
        # La etiqueta siempre sale entre [] (sin duplicar corchetes) y se relee bien del mundo
        assert etiqueta_de("[R] ") == "R" and etiqueta_de("[[Los  Dragones]]") == "Los Dragones"
        assert teams["rojo"].etiqueta == "R" and teams["azul"].etiqueta == "Los Azules"
        assert teams["rojo"].color == "red" and teams["azul"].color == "blue"
        # Team sin color (nombre del jugador normal): el color se saca de la [etiqueta]
        sb2 = {"data": {"Teams": [{"Name": "drg", "DisplayName": '{"text":"Dragones","color":"red"}',
                                   "TeamColor": "reset", "MemberNamePrefix": pre_json_drg}]}}
        assert leer_scoreboard(sb2)["drg"].color == "red" and leer_scoreboard(sb2)["drg"].etiqueta == "DRG"
        pre = next(c for c in cmds if " prefix " in c).split(" prefix ", 1)[1]
        assert texto_de_json(pre) == "[R] " and '"color": "red"' in pre and '"color": "gray"' in pre
        assert any(c.startswith("team modify rojo prefix ") for c in comandos_crear("rojo", "Rojo", "red", "", False))
        assert parsear_miembros("Team [Equipo Rojo] has 2 member(s): Ana, Luis") == ["Ana", "Luis"]
        assert parsear_miembros("There are no members on team [Equipo Rojo]") == []
        print("Teams OK")

    srv.close()
    await srv.wait_closed()


asyncio.run(main())
print("TODO OK")
