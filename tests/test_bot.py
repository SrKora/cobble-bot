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
from bot.teams import (COLORES, EMOJI_COLOR, Registro, Team, color_de, png_color, comando_ftb, comandos_crear, id_mc, nombre_team_valido,
                       parsear_miembros, parsear_party)

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
        # /help dentro de los límites de Discord, con todo a la vista (admin y regalos)
        from bot.main import embed_ayuda
        for adm, reg in ((False, False), (True, True)):
            e = embed_ayuda(bot, adm, reg)
            assert len(e) <= 6000 and all(len(f.value) <= 1024 for f in e.fields) and len(e.fields) <= 25
        assert any("Alto mando" in f.name for f in embed_ayuda(bot, True, True).fields)
        assert not any("Alto mando" in f.name or "Regalos" in f.name for f in embed_ayuda(bot, False).fields)
        from bot.main import paginas_ayuda
        todas = paginas_ayuda(bot, True, True)
        assert len(todas) + 1 <= 25 and all(len(e) <= 6000 for *_, e in todas.values())   # caben en la lista y en Discord
        assert "admin" not in paginas_ayuda(bot, False) and "regalos" not in paginas_ayuda(bot, False)
        print("Embeds y diagnóstico OK")

        # --- Teams de FTB: archivo del mundo, comandos de Minecraft y registro de vínculos
        party = """{
	id: "4301f31b-f823-4976-b497-0dfebd5c4a38"
	type: "party"
	ranks: {
		"%s": "member"
		"%s": "owner"
		"33333333-3333-3333-3333-333333333333": "invited"
	}
	properties: {
		"ftbteams:display_name": "Los \\"Dragones\\""
		"ftbteams:description": ""
		"ftbteams:color": "#ff5555"
	}
	owner: "%s"
	message_history: [{ sender: "%s", text: "type: \\"server\\"" }]
}""" % (LUIS, ANA, ANA, LUIS)
        t = parsear_party(party)
        assert t.id == "4301f31b-f823-4976-b497-0dfebd5c4a38" and t.nombre == 'Los "Dragones"'
        assert t.color == "#FF5555" and t.color_int == 0xFF5555
        assert t.miembros == [ANA, LUIS] and t.lider == ANA              # el dueño primero; invitados fuera
        assert t.id_mc == "ftb_4301f31bf823" == id_mc(t.id) and len(t.id_mc) <= 16
        assert parsear_party(party.replace('"party"', '"server"')) is None   # teams de servidor: no
        assert parsear_party(party.replace("#ff5555", "rojo")).color == "#FFFFFF"
        # Como lo escribe FTB de verdad: claves de ranks sin comillas, y un oficial
        real = party.replace(f'"{LUIS}": "member"', f'{LUIS}: "officer"').replace(f'"{ANA}": "owner"', f'{ANA}: "owner"')
        r = parsear_party(real)
        assert r.miembros == [ANA, LUIS] and r.oficiales == [LUIS], (r.miembros, r.oficiales)
        assert r.puede_gestionar(ANA) and r.puede_gestionar(LUIS.upper()) and not r.puede_gestionar(None)
        assert not t.puede_gestionar(LUIS)                                    # miembro normal: no
        assert Team("ed2875a4-c064-4623-8b31-f17f120aa75a", "Los mejores").id_corto == "Los_mejores#ed2875a4"
        assert Team("ed2875a4-c064-4623-8b31-f17f120aa75a", "Señor X!").id_corto == "Se_or_X_#ed2875a4"
        import zlib as _z
        png = png_color("#FF0080", 4)
        assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")
        idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
        assert _z.decompress(idat) == (b"\x00" + b"\xff\x00\x80" * 4) * 4                   # 4x4 del color pedido
        assert set(EMOJI_COLOR) == set(COLORES) and len(COLORES) <= 25                       # cabe en una lista de Discord
        assert color_de("rojo") == "#FF5555" and color_de("ff00aa") == "#FF00AA" and color_de("#12345") is None
        assert nombre_team_valido("  Los\nmejores §cX ") == "Los mejores X" and len(nombre_team_valido("x" * 50)) == 32
        assert comando_ftb(Team("ed2875a4-c064-4623-8b31-f17f120aa75a", "Los mejores"), "color", "#FF0000") == \
            "ftbteams party settings_for Los_mejores#ed2875a4 ftbteams:color #FF0000"
        cmds = comandos_crear(t)
        assert cmds[0].startswith("team add ftb_4301f31bf823 ") and '"color": "#FF5555"' in cmds[0]
        assert cmds[2] == "team modify ftb_4301f31bf823 color reset"          # el nombre del jugador no se pinta
        assert '{"text": "] ", "color": "gray"}' in cmds[3] and "friendlyFire false" in cmds[4]
        t.etiqueta = "DRG"
        assert '{"text": "DRG", "color": "#FF5555"}' in comandos_crear(t)[3]
        assert parsear_miembros("Team [Equipo Rojo] has 2 member(s): Ana, Luis") == ["Ana", "Luis"]
        assert parsear_miembros("There are no members on team [Equipo Rojo]") == []
        datos = Path(tmp) / "datos"
        datos.mkdir(parents=True, exist_ok=True)
        (datos / "teams.json").write_text(json.dumps({          # formato anterior (teams creados desde Discord)
            "teams": {"losmejores": {"id": "losmejores", "rol": "123", "rol_creado": True}},
            "vinculos": {"1": {"nombre": "Ana", "uuid": ANA}}, "equipos": {"1": "losmejores"}}))
        reg = Registro(datos)
        assert reg.antiguos == {"losmejores": {"rol": "123", "rol_creado": True}} and reg.uuid_de(1) == ANA
        reg.antiguo_hecho("losmejores")
        assert Registro(datos).antiguos == {}
        codigo = reg.nuevo_codigo(2, "Luis")
        assert reg.comprobar_codigo(2, "x") is None and reg.comprobar_codigo(2, codigo) == "Luis"
        assert reg.comprobar_codigo(2, codigo) is None           # un código solo vale una vez
        reg.vincular(2, "ana")                                    # la cuenta pasa a otro usuario de Discord
        assert reg.jugador_de(1) is None and reg.jugador_de(2) == "ana" and reg.uuid_de(2) is None
        assert Registro(datos).vinculos == reg.vinculos   # se guarda en disco
        # Lo que se cambia desde Discord (rol, etiqueta, emoji…) se recuerda por team
        t.rol, t.rol_creado, t.emoji = "999", True, "🐉"
        reg.guardar_extras(t)
        otro = parsear_party(party)
        Registro(datos).aplicar_extras(otro)
        assert (otro.rol, otro.rol_creado, otro.emoji, otro.etiqueta) == ("999", True, "🐉", "DRG")
        assert otro.titulo == '🐉 Los "Dragones"'
        reg.olvidar_team(t.id)
        assert t.id not in Registro(datos).extras
        # --- Ligas: la primera vez solo apunta; luego avisa de las nuevas una sola vez
        from types import SimpleNamespace as NS
        from bot.ligas import EstadoLigas
        from bot.perfiles import Logro
        kanto = Logro("cobbleverse:trainer/kanto/defeat_champion_blue", "kanto")
        johto = Logro("cobbleverse:trainer/johto/defeat_champion_lance", "johto")
        el = EstadoLigas(Path(tmp) / "datos")
        assert el.nuevas([NS(uuid="u1", nombre="Ana", ligas=[kanto]), NS(uuid="u2", nombre="Luis", ligas=[])]) == []
        assert "u2" not in el.vistas                                     # sin ligas no se apunta nada
        nuevas = el.nuevas([NS(uuid="u1", nombre="Ana", ligas=[kanto, johto])])
        assert [(p.nombre, l.region) for p, l in nuevas] == [("Ana", "johto")]
        assert el.nuevas([NS(uuid="u1", nombre="Ana", ligas=[kanto, johto])]) == []   # no se repite
        el.nuevas([])                                                     # una lectura vacía no borra nada
        el.poner_canal(123); el.poner_rol("kanto", 456)
        otra = EstadoLigas(Path(tmp) / "datos")
        assert otra.canal == 123 and otra.roles == {"kanto": 456} and len(otra.vistas["u1"]) == 2
        print("Ligas OK")
        # Mensaje fijo de estado: solo se edita si cambia algo (o cada 5 min) y recuerda desde cuándo está encendido
        from bot.estado_fijo import EstadoFijo, embed_estado
        ef = EstadoFijo(Path(tmp) / "ef")
        ef.poner(10, 20); ef.marcar(True, 1000.0); ef.marcar(True, 2000.0)
        assert ef.online_desde == 1000.0                                     # no se reinicia mientras sigue online
        assert ef.toca(("a",), 0.0 + 1e6) and not ef.toca(("a",), 1e6 + 10) and ef.toca(("b",), 1e6 + 20)
        assert ef.toca(("b",), 1e6 + 20 + 300)                                # refresco cada 5 min
        otro = EstadoFijo(Path(tmp) / "ef")
        assert (otro.canal, otro.mensaje, otro.online_desde) == (10, 20, 1000.0)
        otro.marcar(False, 3000.0)
        assert otro.online_desde is None and otro.caido_desde == 3000.0
        e = embed_estado(True, [("Ana", "Rocket"), ("Luis_", "")], 10, 1000.0, None, "mc.ejemplo.com")
        assert "online" in e.title and "`[Rocket]`" in e.fields[-1].value and "Luis\\_" in e.fields[-1].value
        assert "offline" in embed_estado(False, [], 10, 3000.0).title
        assert "Reiniciando" in embed_estado(False, [], 10, 3000.0, "Kora").title
        print("Estado fijo OK")
        # Invitaciones y cosas pendientes desde Discord: se guardan y sobreviven a un reinicio
        reg = Registro(Path(tmp) / "inv")
        reg.invitar("t1", 1, 2); reg.invitar("t1", 1, 2); reg.invitar("t2", 3, 2)
        assert [x["team"] for x in reg.invitaciones_para(2)] == ["t1", "t2"]      # sin repetidas
        assert reg.quitar_invitacion("t1", 2) and not reg.quitar_invitacion("t1", 2)
        reg.encolar("crear", 2, "Ana", nombre="Los X", color="#FF0000")
        reg.encolar("unir", 2, "ana", team="t2")                                  # solo lo último por jugador
        assert len(reg.pendientes) == 1 and reg.pendiente_de("ANA")["accion"] == "unir"
        otra = Registro(Path(tmp) / "inv")
        assert [x["team"] for x in otra.invitaciones_para("2")] == ["t2"] and otra.pendiente_de("Ana")["team"] == "t2"
        otra.olvidar_team_pendientes("t2")
        assert not otra.invitaciones and not otra.pendientes
        otra.invitaciones.append({"team": "t3", "para": "9", "de": "1", "cuando": 0})   # caducada
        assert otra.invitaciones_para(9) == []
        print("Teams OK")

    srv.close()
    await srv.wait_closed()


asyncio.run(main())
print("TODO OK")
