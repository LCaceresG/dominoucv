"""Importación del 1er Torneo de Dominó UCV y formato 'suizo + final 1.º vs 2.º'."""
import json
import os
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp.name}")
os.environ.pop("ADMIN_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402

from app.database import Base, SessionLocal, engine, ensure_schema  # noqa: E402
from app.importer import ImportError_, importar_torneo  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Entry, Match, MatchPlayer, Player  # noqa: E402

DATOS = json.loads((Path(__file__).resolve().parents[1] / "datos" / "torneo_ucv_2026.json").read_text("utf-8"))


@pytest.fixture()
def c():
    with TestClient(app) as client:
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())
        yield client


@pytest.fixture()
def db(c):
    with SessionLocal() as s:
        yield s


def sin_final():
    d = json.loads(json.dumps(DATOS))
    d.pop("final")
    return d


def detalle(c, cid):
    return c.get(f"/api/competitions/{cid}").json()


# ------------------------------------------------------------------ importación del torneo UCV

def test_import_loads_players_pairs_and_matches(c, db):
    r = importar_torneo(db, DATOS)
    assert len(r["creados"]) == 34 and not r["reutilizados"]
    assert db.scalar(select(func.count()).select_from(Player)) == 34
    assert db.scalar(select(func.count()).select_from(Entry)) == 17
    assert db.scalar(select(func.count()).select_from(Match).where(Match.stage == "swiss")) == 40
    # cada partida del torneo tiene sus 4 jugadores (era la causa del error 500 al cargar resultados)
    assert db.scalar(select(func.count()).select_from(MatchPlayer)) == 41 * 4  # 40 del suizo + la final

    d = detalle(c, r["competencia"].id)
    assert d["competition"]["format"] == "suizo_gran_final"
    assert d["competition"]["status"] == "finished"  # la final ya viene cargada (100-30)
    todas = d["standings"]
    assert len(todas) == 17  # la pareja retirada sale al final de la lista
    rows = todas[:16]
    assert [x["name"] for x in rows[:3]] == [
        "Maira Navas / Ana Liria Zapata",
        "Juan Subero / Heiber González",
        "Uvencio Blanco / Ricardo Calderón",
    ]
    # Totales de la tabla oficial del PDF (puntos, PF, PC)
    esperado = {
        "Maira Navas / Ana Liria Zapata": (5, 501, 263),  # 4 del suizo + la victoria de la final (PF/PC no cambian)
        "Juan Subero / Heiber González": (4, 458, 275),  # la derrota de la final no se cuenta
        "Uvencio Blanco / Ricardo Calderón": (4, 419, 294),
        "Rafael Sosa / Christian Dulcey": (3, 370, 251),
        "Francisco Contreras / Alessain Contreras": (3, 387, 301),
        "Francisco Padilla / Santiago Alfonzo": (3, 376, 338),
        "Iván Montiel / Ángel Rodríguez": (3, 358, 362),
        "Marcos Ramos / José Castillo": (3, 256, 277),
        "Tilson Álvarez / Ronald Gutiérrez": (3, 298, 324),
        "Moisés Castro / Moisés Morales": (2, 317, 343),
        "Juan Morales / Manuel Landaes": (2, 325, 364),
        "Jesús Daniel / Francisco Suárez": (2, 309, 394),
        "Luis Davalillo / Luis Miguel García": (1, 188, 304),
        "Carlos Guillén / Paola Infante": (1, 262, 484),
        "Alejandro Marín / Josué Moscoso": (0, 209, 452),
    }
    por_nombre = {x["name"]: (x["points"], x["pf"], x["pa"]) for x in rows}
    for nombre, valores in esperado.items():
        assert por_nombre[nombre] == valores, nombre
    # Belkis/Valentina: solo cuentan sus 3 partidas (la tabla del PDF les sumaba el 45-52 de la pareja retirada)
    assert por_nombre["Belkis Rigual / Valentina Arévalo"] == (1, 135, 177)
    assert rows[12]["name"].startswith("Belkis")  # 13.º

    # Campeón 5-1 y subcampeón 4-1; la final no mueve el average
    maira, juan = rows[0], rows[1]
    assert (maira["wins"], maira["losses"], maira["diff"]) == (5, 1, 238)
    assert (juan["wins"], juan["losses"], juan["diff"]) == (4, 1, 183)

    # La pareja retirada aparece en la lista (sin puesto), con su 1-1, y marcada como retirada
    retirada = todas[-1]
    assert retirada["name"].startswith("Isael") and retirada["withdrawn"] is True and retirada["position"] is None
    assert (retirada["wins"], retirada["losses"], retirada["pf"], retirada["pa"]) == (1, 1, 122, 87)
    assert [x["withdrawn"] for x in todas].count(True) == 1
    assert [e["withdrawn"] for e in d["entries"]].count(True) == 1


def test_import_without_final_leaves_it_pending_and_provisional_podium(c, db):
    cid = importar_torneo(db, sin_final())["competencia"].id
    d = detalle(c, cid)
    final = d["sections"][-1]["matches"][0]
    assert final["title"] == "Final (1.º vs 2.º)"
    assert final["status"] == "pending" and final["editable"]
    assert final["entry_a"]["name"].startswith("Maira") and final["entry_b"]["name"].startswith("Juan Subero")
    assert [p["place"] for p in d["podium"]["places"]] == [1, 2, 3]
    assert d["podium"]["final"] is False

    # Se registra la final (el marcador real no estaba en el PDF: aquí uno de prueba)
    r = c.put(f"/api/matches/{final['id']}/result", json={"points_a": 100, "points_b": 80})
    assert r.status_code == 200, r.text
    d = detalle(c, cid)
    assert d["competition"]["status"] == "finished"
    assert d["podium"]["final"] is True
    assert [p["name"][:5] for p in d["podium"]["places"]] == ["Maira", "Juan ", "Uvenc"]


def test_final_can_be_won_by_second_place(c, db):
    cid = importar_torneo(db, DATOS)["competencia"].id
    final = detalle(c, cid)["sections"][-1]["matches"][0]
    assert final["status"] == "valid" and (final["points_a"], final["points_b"]) == (100, 30)
    assert c.put(f"/api/matches/{final['id']}/result", json={"points_a": 70, "points_b": 100}).status_code == 200
    d = detalle(c, cid)
    places = d["podium"]["places"]
    assert places[0]["name"].startswith("Juan Subero") and places[1]["name"].startswith("Maira")
    assert places[2]["name"].startswith("Uvencio")
    # ahora el campeón es Juan: 5-1, y Maira queda 4-1
    assert (d["standings"][0]["name"][:4], d["standings"][0]["wins"], d["standings"][0]["losses"]) == ("Juan", 5, 1)
    assert (d["standings"][1]["name"][:5], d["standings"][1]["wins"], d["standings"][1]["losses"]) == ("Maira", 4, 1)


def test_final_counts_only_a_win_for_the_champion(c, db):
    importar_torneo(db, DATOS)
    p = {x.name: x for x in db.scalars(select(Player)).all()}
    for n in ("Maira Navas", "Ana Liria Zapata"):  # campeonas: 5-1, PF/PC sin la final
        assert (p[n].wins, p[n].losses, p[n].games_played) == (5, 1, 6)
        assert (p[n].points_for, p[n].points_against) == (501, 263)
    for n in ("Juan Subero", "Heiber González"):  # subcampeones: 4-1, sin derrota por la final
        assert (p[n].wins, p[n].losses, p[n].games_played) == (4, 1, 5)
        assert (p[n].points_for, p[n].points_against) == (458, 275)
    assert all(x.wins + x.draws + x.losses == x.games_played for x in p.values())
    # la final no mueve el ELO: sin ella los ratings serían iguales
    otro = importar_torneo  # noqa: F841
    from app.services import recalculate_all
    antes = {n: x.rating for n, x in p.items()}
    final = db.scalar(select(Match).where(Match.stage == "ko"))
    final.status = "pending"
    recalculate_all(db)
    assert {n: x.rating for n, x in p.items() if n not in ("Maira Navas", "Ana Liria Zapata", "Juan Subero", "Heiber González")} == {
        n: v for n, v in antes.items() if n not in ("Maira Navas", "Ana Liria Zapata", "Juan Subero", "Heiber González")
    }
    assert p["Maira Navas"].rating == antes["Maira Navas"] and p["Juan Subero"].rating == antes["Juan Subero"]
    db.rollback()


def test_elo_after_import(c, db):
    importar_torneo(db, DATOS)
    jugadores = {p.name: p for p in db.scalars(select(Player)).all()}
    assert all(
        p.games_played == 5
        for n, p in jugadores.items()
        if n not in ("Isael Díaz", "Nayeth Brito", "Belkis Rigual", "Valentina Arévalo", "Maira Navas", "Ana Liria Zapata")
    )
    assert jugadores["Isael Díaz"].games_played == 2 and jugadores["Belkis Rigual"].games_played == 3
    # el ELO solo se mueve entre jugadores: la suma se conserva (34 x 1000, salvo redondeos)
    assert sum(p.rating for p in jugadores.values()) == pytest.approx(34000, abs=1.5)
    maira = jugadores["Maira Navas"]
    assert (maira.wins, maira.losses, maira.points_for, maira.points_against) == (5, 1, 501, 263)
    # PF/PC de cada jugador salen de las partidas, no de una copia aparte
    assert jugadores["Ana Liria Zapata"].points_for == 501


def test_import_affiliations_and_career(c, db):
    importar_torneo(db, DATOS)
    p = {x.name: x for x in db.scalars(select(Player)).all()}
    assert (p["Marcos Ramos"].university, p["Marcos Ramos"].career) == ("UCV", "Educación")
    assert p["Ronald Gutiérrez"].university == "USM"
    assert p["Moisés Castro"].university == "UAH"
    assert p["Rafael Sosa"].university == "UNEFA"
    assert p["Ricardo Calderón"].university == "ENAHP"
    assert (p["Carlos Guillén"].category, p["Carlos Guillén"].university) == ("externo", None)
    assert p["Carlos Guillén"].career == "Dominoceros Los Ruices"
    assert p["Uvencio Blanco"].category == "sin_definir" and p["Uvencio Blanco"].university is None
    assert p["Belkis Rigual"].university == "UCV"
    # los IDs públicos se asignan igual que al crear jugadores desde la web
    assert all(x.public_id == f"DOM-{x.id:06d}" for x in p.values())


def test_players_stay_editable_after_import(c, db):
    importar_torneo(db, DATOS)
    uvencio = next(x for x in db.scalars(select(Player)).all() if x.name == "Uvencio Blanco")
    pid = uvencio.public_id
    r = c.patch(f"/api/players/{pid}", json={"category": "estudiante", "university": "UCV", "career": "Derecho"})
    assert r.status_code == 200, r.text
    assert (r.json()["category"], r.json()["university"], r.json()["career"]) == ("estudiante", "UCV", "Derecho")
    r = c.patch(f"/api/players/{pid}", json={"name": "Uvencio Blanco Pérez"})
    assert r.status_code == 200 and r.json()["name"] == "Uvencio Blanco Pérez"
    assert c.get("/api/players", params={"search": "perez"}).json()[0]["public_id"] == pid
    # tiene historial: se archiva en vez de borrarse, y el ELO de los demás no cambia
    assert c.delete(f"/api/players/{pid}").json() == {"result": "archived"}
    # "sin definir" también se puede crear y editar desde la API
    r = c.post("/api/players", json={"name": "Jugador Nuevo", "category": "sin_definir"})
    assert r.status_code == 201 and r.json()["university"] is None


def test_import_twice_is_refused_and_changes_nothing(c, db):
    importar_torneo(db, DATOS)
    antes = (db.scalar(select(func.count()).select_from(Player)), db.scalar(select(func.count()).select_from(Match)))
    with pytest.raises(ImportError_, match="Ya existe"):
        importar_torneo(db, DATOS)
    despues = (db.scalar(select(func.count()).select_from(Player)), db.scalar(select(func.count()).select_from(Match)))
    assert antes == despues


def test_replace_reuses_players_and_does_not_duplicate(c, db):
    importar_torneo(db, DATOS)
    ids = {p.name: p.public_id for p in db.scalars(select(Player)).all()}
    r = importar_torneo(db, DATOS, reemplazar=True, sede="Aula Magna")
    assert len(r["reutilizados"]) == 34 and not r["creados"]
    assert db.scalar(select(func.count()).select_from(Player)) == 34
    assert db.scalar(select(func.count()).select_from(Entry)) == 17
    assert {p.name: p.public_id for p in db.scalars(select(Player)).all()} == ids
    assert r["competencia"].location == "Aula Magna"
    assert db.scalar(select(func.count()).select_from(Match).where(Match.stage == "swiss")) == 40


def test_existing_player_is_reused_and_not_overwritten(c, db):
    c.post("/api/players", json={"name": "maira navas", "category": "profesor", "university": "Otra Universidad"})
    r = importar_torneo(db, DATOS)
    assert len(r["creados"]) == 33 and len(r["reutilizados"]) == 1
    maira = next(p for p in db.scalars(select(Player)).all() if p.search_name == "maira navas")
    assert (maira.category, maira.university) == ("profesor", "Otra Universidad")  # no se pisa
    assert maira.career == "Economía"  # solo se completa lo que estaba vacío
    assert db.scalar(select(func.count()).select_from(Player).where(Player.search_name == "maira navas")) == 1


def test_import_is_atomic_when_data_is_invalid(c, db):
    malo = json.loads(json.dumps(DATOS))
    malo["rondas"][2].append(["MA", "JS", 10, 5])  # MA ya juega en esa ronda
    with pytest.raises(ImportError_):
        importar_torneo(db, malo)
    assert db.scalar(select(func.count()).select_from(Player)) == 0
    assert db.scalar(select(func.count()).select_from(Match)) == 0
    desconocida = json.loads(json.dumps(DATOS))
    desconocida["rondas"][0][0][0] = "XX"
    with pytest.raises(ImportError_, match="desconocida"):
        importar_torneo(db, desconocida)
    assert db.scalar(select(func.count()).select_from(Player)) == 0


def test_edit_result_after_import_recomputes_standings(c, db):
    cid = importar_torneo(db, sin_final())["competencia"].id
    d = detalle(c, cid)
    # ronda 5, mesa 1: Juan Subero 117-77 Maira. Si en realidad ganó Maira, ella queda con 5 puntos.
    m = d["sections"][4]["matches"][0]
    assert m["locked"] is False  # la final aún no se jugó, así que el resultado se puede corregir
    r = c.put(f"/api/matches/{m['id']}/result", json={"points_a": 77, "points_b": 117})
    assert r.status_code == 200, r.text
    d = detalle(c, cid)
    assert d["standings"][0]["name"].startswith("Maira") and d["standings"][0]["points"] == 5
    assert d["standings"][1]["name"].startswith("Uvencio") and d["standings"][1]["points"] == 4
    juan = next(x for x in d["standings"] if x["name"].startswith("Juan Subero"))
    assert juan["points"] == 3
    # y la final pendiente se rearma con los nuevos 1.º y 2.º
    final = d["sections"][-1]["matches"][0]
    assert final["entry_a"]["name"].startswith("Maira") and final["entry_b"]["name"].startswith("Uvencio")


def test_cannot_edit_swiss_result_once_final_is_played(c, db):
    cid = importar_torneo(db, DATOS)["competencia"].id  # la final ya está cargada
    d = detalle(c, cid)
    m = detalle(c, cid)["sections"][4]["matches"][0]
    assert m["locked"] is True
    r = c.put(f"/api/matches/{m['id']}/result", json={"points_a": 77, "points_b": 117})
    assert r.status_code in (400, 409), r.text
    assert "final" in r.text.lower() or "depend" in r.text.lower() or "jug" in r.text.lower()


# ------------------------------------------------------------------ base de datos antigua

def test_ensure_schema_adds_new_columns_without_losing_data(c):
    with engine.begin() as conn:
        for col in ("players.career", "players.rating_pending", "competitions.final_only_on_tie", "entries.withdrawn"):
            table, name = col.split(".")
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {name}"))
        conn.execute(text("INSERT INTO players (public_id, name, search_name, category, active, rating, peak_rating, games_played, wins, draws, losses, points_for, points_against, created_at) VALUES ('DOM-000001','Viejo Jugador','viejo jugador','externo',1,1000,1000,0,0,0,0,0,0,'2026-01-01')"))
    assert sorted(ensure_schema()) == [
        "competitions.final_only_on_tie", "entries.withdrawn", "players.career", "players.rating_pending",
    ]
    assert ensure_schema() == []  # idempotente
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM players WHERE public_id='DOM-000001'")).scalar() == "Viejo Jugador"
    assert c.get("/api/players/DOM-000001").json()["career"] is None
    assert c.get("/api/players/DOM-000001").json()["rating_pending"] == 0.0


# ------------------------------------------------------------------ formato suizo + final 1.º vs 2.º

def _torneo(c, n, **kw):
    ids = [c.post("/api/players", json={"name": f"Jugador {i:02d}", "category": "estudiante", "university": "UCV"}).json()["public_id"] for i in range(1, n + 1)]
    cid = c.post(
        "/api/competitions",
        json={"name": "Copa final", "kind": "torneo", "format": "suizo_gran_final", "team_size": 1,
              "points_per_win": 1, "points_per_draw": 0, **kw},
    ).json()["id"]
    for i in ids:
        assert c.post(f"/api/competitions/{cid}/entries", json={"players": [i]}).status_code == 201
    assert c.post(f"/api/competitions/{cid}/start").status_code == 200
    return cid


def _jugar_suizo(c, cid, ganador):
    """Juega todas las rondas suizas; `ganador(a_id, b_id)` devuelve el id que gana."""
    while True:
        d = detalle(c, cid)
        pend = [m for s in d["sections"] if s["phase"] == "swiss" for m in s["matches"] if m["status"] == "pending"]
        if pend:
            m = pend[0]
            a, b = m["entry_a"]["id"], m["entry_b"]["id"]
            win = ganador(a, b)
            body = {"points_a": 100, "points_b": 50} if win == a else {"points_a": 50, "points_b": 100}
            assert c.put(f"/api/matches/{m['id']}/result", json=body).status_code == 200
        elif d["swiss"]["can_next"]:
            assert c.post(f"/api/competitions/{cid}/next-round").status_code == 200
        else:
            return d


def test_gran_final_always_played_by_default(c):
    cid = _torneo(c, 4, swiss_rounds=2)
    d = _jugar_suizo(c, cid, lambda a, b: min(a, b))
    final = d["sections"][-1]["matches"][0]
    assert final["title"] == "Final (1.º vs 2.º)" and final["status"] == "pending" and not final["skipped"]
    assert final["entry_a"] and final["entry_b"]
    assert d["competition"]["status"] == "active"
    assert c.put(f"/api/matches/{final['id']}/result", json={"points_a": 100, "points_b": 90}).status_code == 200
    d = detalle(c, cid)
    assert d["competition"]["status"] == "finished" and d["podium"]["final"] is True


def test_gran_final_only_on_tie_is_skipped_when_there_is_no_tie(c):
    # 4 jugadores y 3 rondas: si siempre gana el id menor, hay un único líder (sin empate por el primero)
    cid = _torneo(c, 4, swiss_rounds=3, final_only_on_tie=True)
    assert detalle(c, cid)["competition"]["final_only_on_tie"] is True
    d = _jugar_suizo(c, cid, lambda a, b: min(a, b))
    rows = d["standings"]
    assert rows[0]["points"] > rows[1]["points"]  # sin empate por el primer lugar
    final = d["sections"][-1]["matches"][0]
    assert final["title"] == "Final de desempate (1.º vs 2.º)"
    assert final["skipped"] is True and final["status"] == "bye" and final["editable"] is False
    assert d["competition"]["status"] == "finished"
    assert d["podium"]["places"][0]["name"] == rows[0]["name"]


def test_gran_final_only_on_tie_is_played_when_there_is_a_tie(c):
    # 4 jugadores, 1 ronda: dos ganadores empatan con 1 punto -> hay final de desempate
    cid = _torneo(c, 4, swiss_rounds=1, final_only_on_tie=True)
    d = _jugar_suizo(c, cid, lambda a, b: min(a, b))
    rows = d["standings"]
    assert rows[0]["points"] == rows[1]["points"] == 1
    final = d["sections"][-1]["matches"][0]
    assert final["skipped"] is False and final["status"] == "pending"
    assert {final["entry_a"]["id"], final["entry_b"]["id"]} == {rows[0]["entry_id"], rows[1]["entry_id"]}
    assert d["competition"]["status"] == "active"
    # gana el segundo de la tabla: pasa a ser campeón
    segundo = rows[1]["entry_id"]
    a_is_second = final["entry_a"]["id"] == segundo
    body = {"points_a": 100, "points_b": 60} if a_is_second else {"points_a": 60, "points_b": 100}
    assert c.put(f"/api/matches/{final['id']}/result", json=body).status_code == 200
    d = detalle(c, cid)
    assert d["podium"]["places"][0]["name"] == rows[1]["name"]
    assert d["podium"]["places"][1]["name"] == rows[0]["name"]
    assert d["competition"]["status"] == "finished"


def test_final_only_on_tie_ignored_for_other_formats(c):
    r = c.post("/api/competitions", json={"name": "Suizo normal", "kind": "torneo", "format": "suizo",
                                           "final_only_on_tie": True, "team_size": 1})
    assert r.status_code == 201 and r.json()["final_only_on_tie"] is False


# ------------------------------------------------------------------ ELO de torneo estilo FIDE

def test_tournament_elo_is_pending_until_the_tournament_ends(c, db):
    cid = importar_torneo(db, sin_final())["competencia"].id  # torneo en curso (falta la final)
    jug = {p.name: p for p in db.scalars(select(Player)).all()}
    # nadie cambia de ELO todavía, pero el cambio acumulado queda como pendiente
    assert all(p.rating == 1000.0 for p in jug.values())
    assert jug["Maira Navas"].rating_pending > 0 and jug["Alejandro Marín"].rating_pending < 0
    pendiente = {n: p.rating_pending for n, p in jug.items()}
    maira_pend = pendiente["Maira Navas"]
    assert sum(p.rating_pending for p in jug.values()) == pytest.approx(0, abs=1.5)
    # todas las rondas se calculan con el ELO de INICIO (1000 para todos) y sin margen de puntos:
    # ganar vale exactamente +20 y perder -20, sin importar el marcador
    for m in db.scalars(select(Match).where(Match.stage == "swiss")).all():
        for mp in m.players:
            ganador = (m.points_a > m.points_b) == (mp.team == "A")
            assert mp.delta == (20.0 if ganador else -20.0)
    # la partida muestra el ELO "corriendo": antes y después de cada partida
    mp_r2 = db.scalars(
        select(MatchPlayer).join(Match).where(Match.round == 2, Match.stage == "swiss", MatchPlayer.player_id == jug["Maira Navas"].id)
    ).one()
    assert mp_r2.rating_before > 1000 and mp_r2.rating_after == pytest.approx(mp_r2.rating_before + mp_r2.delta, abs=0.02)
    # la API del perfil y del ranking lo informan
    perfil = c.get(f"/api/players/{jug['Maira Navas'].public_id}").json()
    assert perfil["rating"] == 1000.0 and perfil["rating_pending"] == maira_pend
    # el torneo muestra el cambio acumulado por pareja
    d = detalle(c, cid)
    assert d["standings"][0]["elo_delta"] > 0 and d["standings"][15]["elo_delta"] < 0
    assert d["competition"]["status"] == "active"

    # al registrar la final el torneo termina y el ELO se suma completo
    final = d["sections"][-1]["matches"][0]
    assert c.put(f"/api/matches/{final['id']}/result", json={"points_a": 100, "points_b": 30}).status_code == 200
    db.expire_all()
    jug2 = {p.name: p for p in db.scalars(select(Player)).all()}
    for n, p in jug2.items():
        assert p.rating_pending == 0.0
        assert p.rating == pytest.approx(1000 + pendiente[n], abs=0.02), n
    assert jug2["Maira Navas"].rating > 1000 > jug2["Alejandro Marín"].rating
    assert jug2["Maira Navas"].peak_rating == jug2["Maira Navas"].rating


def test_reopening_tournament_by_editing_final_keeps_elo_consistent(c, db):
    importar_torneo(db, DATOS)
    jug = {p.name: p for p in db.scalars(select(Player)).all()}
    total = sum(p.rating for p in jug.values())
    assert total == pytest.approx(34000, abs=1.5)
    assert all(p.rating_pending == 0.0 for p in jug.values())
    # un jugador con rating alto en un torneo posterior parte de su ELO ya actualizado (no del pendiente)
    assert jug["Maira Navas"].rating != 1000.0


def test_free_matches_still_update_elo_immediately(c):
    a = c.post("/api/players", json={"name": "Libre Uno", "category": "externo"}).json()["public_id"]
    b = c.post("/api/players", json={"name": "Libre Dos", "category": "externo"}).json()["public_id"]
    r = c.post("/api/matches", json={"team_a": [a], "team_b": [b], "points_a": 100, "points_b": 40})
    assert r.status_code == 201
    pa = c.get(f"/api/players/{a}").json()
    assert pa["rating"] > 1000 and pa["rating_pending"] == 0.0


# ------------------------------------------------------------------ novedades y universidades

def test_feed_lists_matches_tournaments_and_new_players(c, db):
    importar_torneo(db, DATOS)
    items = c.get("/api/feed", params={"limit": 10}).json()
    tipos = [i["type"] for i in items]
    assert "tournament_finished" in tipos and "match" in tipos and "players_joined" in tipos
    torneo = next(i for i in items if i["type"] == "tournament_finished")
    assert torneo["name"] == "1er Torneo de Dominó UCV" and torneo["provisional"] is False
    assert [p["place"] for p in torneo["podium"]] == [1, 2, 3] and torneo["podium"][0]["name"].startswith("Maira")
    final = next(i for i in items if i["type"] == "match" and i["final"])
    assert final["stage_label"] == "Final (1.º vs 2.º)" and final["winner"] == "A" and final["tiebreak_final"] is True
    assert (final["points_a"], final["points_b"]) == (100, 30) and final["team_a"] == ["Maira Navas", "Ana Liria Zapata"]
    assert sum(1 for i in items if i["type"] == "match") == 10
    jugadores = next(i for i in items if i["type"] == "players_joined")
    assert jugadores["count"] == 34


def test_feed_works_on_empty_database_and_with_free_matches(c):
    assert c.get("/api/feed").json() == []
    a = c.post("/api/players", json={"name": "Libre Uno", "category": "externo"}).json()["public_id"]
    b = c.post("/api/players", json={"name": "Libre Dos", "category": "externo"}).json()["public_id"]
    c.post("/api/matches", json={"team_a": [a], "team_b": [b], "points_a": 50, "points_b": 50})
    m = next(i for i in c.get("/api/feed").json() if i["type"] == "match")
    assert m["winner"] == "D" and m["stage_label"] == "Partida libre" and m["competition_name"] is None


def test_meta_offers_top_20_universities(c):
    unis = c.get("/api/meta").json()["universities"]
    assert len(unis) == 20 and unis[0] == {"value": "UCV", "label": "Universidad Central de Venezuela"}
    assert len({u["value"] for u in unis}) == 20
    assert {"USB", "ULA", "LUZ", "UCAB", "UNIMET", "USM", "UNEFA", "UAH"} <= {u["value"] for u in unis}


def test_same_record_means_same_elo_change_in_a_tournament(c, db):
    """Mismas victorias y derrotas => mismo cambio de ELO (sin influencia del marcador)."""
    importar_torneo(db, sin_final())
    jug = {p.name: p for p in db.scalars(select(Player)).all()}
    # los tres empatados en la cima (4-1) y Ricardo/Uvencio comparten exactamente +60
    for n in ("Maira Navas", "Ana Liria Zapata", "Juan Subero", "Heiber González", "Uvencio Blanco", "Ricardo Calderón"):
        assert jug[n].rating_pending == 60.0, n
    # y en general: el cambio depende solo del récord (4 victorias = +60, 3 = +20, 2 = -20, 1 = -60, 0 = -100)
    por_record: dict[tuple[int, int], set[float]] = {}
    for p in jug.values():
        por_record.setdefault((p.wins, p.losses), set()).add(p.rating_pending)
    assert all(len(v) == 1 for v in por_record.values()), por_record
    assert por_record[(0, 5)] == {-100.0} and por_record[(1, 1)] == {0.0}  # Alejandro/Josué; Isael/Nayeth


# ------------------------------------------------------------------ redes (Instagram @dominoucv)

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def test_meta_has_instagram_link(c):
    ig = c.get("/api/meta").json()["instagram"]
    assert ig == {"handle": "dominoucv", "url": "https://www.instagram.com/dominoucv/"}


def test_social_post_lifecycle_with_image(c):
    r = c.post("/api/social", json={"posted_on": "2026-11-08", "title": "Gran jornada", "summary": "Resumen del día",
                                    "link": "https://www.instagram.com/p/ABC123/", "image_data": PNG})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["has_image"] and p["image_url"].endswith("?v=1") and p["posted_on"] == "2026-11-08"
    img = c.get(f"/api/social/{p['id']}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content[:4] == b"\x89PNG"

    # sin imagen y con fecha por defecto (hoy)
    q = c.post("/api/social", json={"summary": "Solo texto"}).json()
    assert q["has_image"] is False and q["image_url"] is None
    assert c.get(f"/api/social/{q['id']}/image").status_code == 404

    lista = c.get("/api/social").json()
    assert lista[0]["id"] == q["id"]  # la más reciente primero (hoy > 08/11)
    assert {x["id"] for x in lista} == {p["id"], q["id"]}

    # editar: cambiar texto y reemplazar imagen (sube la versión), luego quitarla
    u = c.patch(f"/api/social/{p['id']}", json={"title": "Jornada final", "summary": "Nuevo resumen", "image_data": PNG}).json()
    assert u["title"] == "Jornada final" and u["summary"] == "Nuevo resumen" and u["image_url"].endswith("?v=2")
    u = c.patch(f"/api/social/{p['id']}", json={"remove_image": True}).json()
    assert u["has_image"] is False
    assert c.delete(f"/api/social/{q['id']}").json() == {"deleted": q["id"]}
    assert c.delete(f"/api/social/{q['id']}").status_code == 404
    assert [x["id"] for x in c.get("/api/social").json()] == [p["id"]]


def test_social_validation(c):
    assert c.post("/api/social", json={"summary": "  "}).status_code == 422
    assert c.post("/api/social", json={"summary": "x", "link": "javascript:alert(1)"}).status_code == 422
    assert c.post("/api/social", json={"summary": "x", "image_data": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="}).status_code == 422
    falso = "data:image/png;base64,SGVsbG8gd29ybGQ="  # dice PNG pero no lo es
    assert c.post("/api/social", json={"summary": "x", "image_data": falso}).status_code == 422
    import base64
    grande = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"0" * 3_100_000).decode()
    assert c.post("/api/social", json={"summary": "x", "image_data": grande}).status_code == 413
    assert c.get("/api/social").json() == []


def test_social_posts_appear_in_news_feed(c):
    c.post("/api/social", json={"posted_on": "2026-11-08", "title": "Aviso", "summary": "A" * 300, "image_data": PNG})
    items = c.get("/api/feed").json()
    s = next(i for i in items if i["type"] == "social")
    assert s["title"] == "Aviso" and s["summary"].endswith("…") and len(s["summary"]) == 221 and s["image_url"]
