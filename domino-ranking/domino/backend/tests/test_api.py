"""Pruebas de la API completa (jugadores, partidas libres y los cinco formatos de torneo)."""
import os
import tempfile
from datetime import date

import pytest

# La base de datos de prueba debe definirse ANTES de importar la app.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ.pop("ADMIN_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def c():
    with TestClient(app) as client:
        with engine.begin() as conn:  # base limpia en cada prueba
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())
        yield client


def mk(c, name, category="externo", university=None, city="Caracas"):
    r = c.post("/api/players", json={"name": name, "category": category, "university": university, "city": city})
    assert r.status_code == 201, r.text
    return r.json()["public_id"]


def many(c, n):
    return [mk(c, f"Jugador {i:02d}", "estudiante", "UCV") for i in range(1, n + 1)]


def new_comp(c, **kw):
    body = {"name": "Copa", "kind": "torneo", "location": "Caracas", "team_size": 1, **kw}
    r = c.post("/api/competitions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def enroll(c, cid, ids, team_size=1):
    step = team_size
    for i in range(0, len(ids), step):
        r = c.post(f"/api/competitions/{cid}/entries", json={"players": ids[i : i + step]})
        assert r.status_code == 201, r.text


def play_all(c, cid, limit=300):
    """Juega todas las partidas disponibles (gana el participante de id menor) y genera rondas suizas."""
    for _ in range(limit):
        d = c.get(f"/api/competitions/{cid}").json()
        pending = [m for s in d["sections"] for m in s["matches"] if m["status"] == "pending" and m["editable"]]
        if pending:
            m = pending[0]
            a_wins = m["entry_a"]["id"] < m["entry_b"]["id"]
            body = {"points_a": 100 if a_wins else 40, "points_b": 40 if a_wins else 100}
            r = c.put(f"/api/matches/{m['id']}/result", json=body)
            assert r.status_code == 200, r.text
            continue
        if d["swiss"] and d["swiss"]["can_next"]:
            assert c.post(f"/api/competitions/{cid}/next-round").status_code == 200
            continue
        return d
    raise AssertionError("El torneo no termina")


# ---------------------------------------------------------------- jugadores

def test_player_affiliation_rules(c):
    r = c.post("/api/players", json={"name": "Ana Gómez", "category": "estudiante"})
    assert r.status_code == 422  # falta universidad
    ok = c.post("/api/players", json={"name": "Ana Gómez", "category": "estudiante", "university": "UCV"})
    assert ok.status_code == 201 and ok.json()["university"] == "UCV"
    ext = c.post("/api/players", json={"name": "Luis Paz", "category": "externo", "university": "UCV"})
    assert ext.json()["university"] is None  # un externo no pertenece a la universidad
    pid = ok.json()["public_id"]
    r = c.patch(f"/api/players/{pid}", json={"category": "externo"})
    assert r.status_code == 200 and r.json()["university"] is None
    r = c.patch(f"/api/players/{pid}", json={"category": "profesor", "university": "USB"})
    assert r.json()["category"] == "profesor" and r.json()["university"] == "USB"
    assert "USB" in c.get("/api/universities").json()


def test_search_by_name_or_surname_without_accents(c):
    a = mk(c, "José Pérez")
    mk(c, "María Rodríguez")
    assert [p["public_id"] for p in c.get("/api/players?search=perez").json()] == [a]
    assert [p["public_id"] for p in c.get("/api/players?search=jose per").json()] == [a]
    assert len(c.get("/api/players?search=ez").json()) == 2
    assert [p["public_id"] for p in c.get(f"/api/players?search={a}").json()] == [a]


def test_delete_player_without_and_with_history(c):
    lonely = mk(c, "Sin Historia")
    assert c.delete(f"/api/players/{lonely}").json() == {"result": "deleted"}
    assert c.get(f"/api/players/{lonely}").status_code == 404

    a, b = mk(c, "Ana"), mk(c, "Beto")
    c.post("/api/matches", json={"team_a": [a], "team_b": [b], "points_a": 100, "points_b": 30})
    assert c.delete(f"/api/players/{a}").json() == {"result": "archived"}
    assert a not in [p["public_id"] for p in c.get("/api/ranking").json()]
    assert a not in [p["public_id"] for p in c.get("/api/players").json()]
    assert a in [p["public_id"] for p in c.get("/api/players?include_inactive=true").json()]
    assert c.patch(f"/api/players/{a}", json={"active": True}).json()["active"] is True


# ---------------------------------------------------------------- partidas libres

def test_free_match_draw_edit_and_delete(c):
    a, b = mk(c, "Ana"), mk(c, "Beto")
    m = c.post("/api/matches", json={"team_a": [a], "team_b": [b], "points_a": 50, "points_b": 50})
    assert m.status_code == 201, m.text  # los empates se permiten
    assert m.json()["played_at"] == date.today().isoformat()  # fecha de hoy por defecto
    pa = c.get(f"/api/players/{a}").json()
    assert (pa["wins"], pa["draws"], pa["losses"]) == (0, 1, 0) and pa["rating"] == 1000

    mid = m.json()["id"]
    r = c.put(f"/api/matches/{mid}/result", json={"points_a": 100, "points_b": 30, "played_at": "2026-01-15"})
    assert r.status_code == 200 and r.json()["played_at"] == "2026-01-15"
    pa = c.get(f"/api/players/{a}").json()
    assert (pa["wins"], pa["draws"], pa["losses"], pa["points_for"], pa["points_against"]) == (1, 0, 0, 100, 30)
    assert pa["rating"] > 1000 > c.get(f"/api/players/{b}").json()["rating"]

    assert c.delete(f"/api/matches/{mid}").status_code == 200
    pa = c.get(f"/api/players/{a}").json()
    assert pa["rating"] == 1000 and pa["games_played"] == 0


def test_free_match_validations(c):
    a, b = mk(c, "Ana"), mk(c, "Beto")
    same = c.post("/api/matches", json={"team_a": [a], "team_b": [a], "points_a": 1, "points_b": 0})
    assert same.status_code == 422
    ghost = c.post("/api/matches", json={"team_a": [a], "team_b": ["DOM-999999"], "points_a": 1, "points_b": 0})
    assert ghost.status_code == 404
    uneven = c.post("/api/matches", json={"team_a": [a, b], "team_b": ["DOM-000009"], "points_a": 1, "points_b": 0})
    assert uneven.status_code == 422


def test_liga_libre_with_standings_and_top3(c):
    ids = many(c, 4)
    cid = new_comp(c, kind="liga", format="libre", team_size=2)
    for pid in ids:
        assert c.post(f"/api/competitions/{cid}/members", json={"public_id": pid}).status_code == 201
    body = {"team_a": ids[:2], "team_b": ids[2:], "points_a": 100, "points_b": 60, "competition_id": cid}
    assert c.post("/api/matches", json=body).status_code == 201
    outsider = mk(c, "Intruso")
    bad = c.post("/api/matches", json={**body, "team_b": [ids[2], outsider]})
    assert bad.status_code == 400
    d = c.get(f"/api/competitions/{cid}").json()
    assert d["standings"][0]["points"] == 3 and d["standings"][-1]["points"] == 0
    assert len(d["podium"]["places"]) == 3
    assert c.post(f"/api/competitions/{cid}/finish").json()["status"] == "finished"
    assert c.get(f"/api/competitions/{cid}").json()["podium"]["final"] is True


# ---------------------------------------------------------------- torneos

def test_single_elimination_with_podium_and_profile(c):
    ids = many(c, 6)
    cid = new_comp(c, format="eliminacion", name="Copa Rectores", location="UCV, Caracas")
    enroll(c, cid, ids)
    assert c.post(f"/api/competitions/{cid}/start").status_code == 200
    d = play_all(c, cid)
    assert d["competition"]["status"] == "finished"
    assert d["podium"]["final"] and [p["place"] for p in d["podium"]["places"]] == [1, 2, 3]
    champion = d["podium"]["places"][0]["players"][0]["public_id"]
    prof = c.get(f"/api/players/{champion}").json()
    row = next(x for x in prof["competitions"] if x["id"] == cid)
    assert row["place"] == 1 and row["location"] == "UCV, Caracas"
    assert prof["wins"] >= 2 and prof["points_for"] > 0


def test_double_elimination(c):
    ids = many(c, 5)
    cid = new_comp(c, format="doble_eliminacion")
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    d = play_all(c, cid)
    assert d["competition"]["status"] == "finished"
    assert [p["place"] for p in d["podium"]["places"]] == [1, 2, 3]
    assert {s["title"] for s in d["sections"]} >= {"Gran final"}


def test_groups_then_knockout(c):
    ids = many(c, 8)
    cid = new_comp(c, format="grupos_eliminacion", groups_count=2, qualifiers=2)
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    d = c.get(f"/api/competitions/{cid}").json()
    assert [g["label"] for g in d["groups"]] == ["A", "B"]
    d = play_all(c, cid)
    assert d["competition"]["status"] == "finished"
    assert [p["place"] for p in d["podium"]["places"]] == [1, 2, 3]


def test_swiss_only(c):
    ids = many(c, 7)
    cid = new_comp(c, format="suizo", swiss_rounds=3)
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    d = play_all(c, cid)
    assert d["swiss"]["generated"] == 3 and d["competition"]["status"] == "finished"
    assert len(d["podium"]["places"]) == 3
    assert sum(s["wins"] + s["draws"] + s["losses"] + s["byes"] for s in d["standings"]) >= 7 * 3 - 3


def test_swiss_then_final_four(c):
    ids = many(c, 8)
    cid = new_comp(c, format="suizo_final")
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    d = play_all(c, cid)
    assert d["competition"]["status"] == "finished"
    titles = [s["title"] for s in d["sections"]]
    assert "Semifinal" in titles and "Final" in titles and "Tercer puesto" in titles
    assert [p["place"] for p in d["podium"]["places"]] == [1, 2, 3]


def test_pairs_tournament_uses_two_players_per_entry(c):
    ids = many(c, 8)
    cid = new_comp(c, format="eliminacion", team_size=2)
    enroll(c, cid, ids, team_size=2)
    c.post(f"/api/competitions/{cid}/start")
    d = play_all(c, cid)
    assert d["competition"]["status"] == "finished"
    assert len(d["podium"]["places"][0]["players"]) == 2


def test_editing_results_in_tournament(c):
    ids = many(c, 4)
    cid = new_comp(c, format="eliminacion")
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    d = play_all(c, cid)
    semi = next(m for s in d["sections"] if s["title"] == "Semifinal" for m in s["matches"])
    final = next(m for s in d["sections"] if s["title"] == "Final" for m in s["matches"])
    assert semi["locked"] is True
    r = c.put(f"/api/matches/{semi['id']}/result", json={"points_a": 1, "points_b": 100})
    assert r.status_code == 409  # la final ya se jugó
    r = c.put(f"/api/matches/{final['id']}/result", json={"points_a": final["points_b"], "points_b": final["points_a"]})
    assert r.status_code == 200  # la final sí se puede corregir
    tie = c.put(f"/api/matches/{final['id']}/result", json={"points_a": 50, "points_b": 50})
    assert tie.status_code == 400  # en eliminación no hay empate
    assert c.delete(f"/api/matches/{final['id']}").status_code == 400  # las de torneo no se borran


def test_group_stage_allows_draws_and_dates(c):
    ids = many(c, 4)
    cid = new_comp(c, format="grupos_eliminacion", groups_count=2, qualifiers=1)
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    d = c.get(f"/api/competitions/{cid}").json()
    m = d["sections"][0]["matches"][0]
    r = c.put(f"/api/matches/{m['id']}/result", json={"points_a": 70, "points_b": 70, "played_at": "2026-03-01"})
    assert r.status_code == 200 and r.json()["played_at"] == "2026-03-01"
    row = next(x for g in c.get(f"/api/competitions/{cid}").json()["groups"] for x in g["rows"] if x["draws"] == 1)
    assert row["points"] == 1


def test_start_and_enroll_validations(c):
    ids = many(c, 3)
    cid = new_comp(c, format="doble_eliminacion")
    enroll(c, cid, ids)
    assert c.post(f"/api/competitions/{cid}/start").status_code == 400  # faltan participantes
    dup = c.post(f"/api/competitions/{cid}/entries", json={"players": [ids[0]]})
    assert dup.status_code == 409
    wrong = c.post(f"/api/competitions/{cid}/entries", json={"players": [ids[0], ids[1]]})
    assert wrong.status_code == 400  # este torneo es de jugadores individuales


def test_delete_competition_recalculates_elo(c):
    ids = many(c, 4)
    cid = new_comp(c, format="eliminacion")
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    play_all(c, cid)
    assert any(p["rating"] != 1000 for p in c.get("/api/ranking").json())
    assert c.delete(f"/api/competitions/{cid}").status_code == 200
    assert all(p["rating"] == 1000 and p["games_played"] == 0 for p in c.get("/api/ranking").json())


# ---------------------------------------------------------------- regresión: error 500 al subir el 2.º resultado

def test_tournament_results_in_a_row_save_players_and_stats(c):
    ids = many(c, 4)
    cid = new_comp(c, format="eliminacion")
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    d = c.get(f"/api/competitions/{cid}").json()
    first_round = [m for s in d["sections"] for m in s["matches"] if m["status"] == "pending" and m["editable"]]
    assert len(first_round) == 2
    for m in first_round:  # varios resultados seguidos: antes el segundo daba error 500
        r = c.put(f"/api/matches/{m['id']}/result", json={"points_a": 100, "points_b": 40})
        assert r.status_code == 200, r.text
    ranking = c.get("/api/ranking").json()
    assert sum(p["games_played"] for p in ranking) == 4
    assert sum(p["points_for"] for p in ranking) == 280 and sum(p["points_against"] for p in ranking) == 280  # 2 x (100 + 40)
    assert sum(p["wins"] for p in ranking) == 2 and sum(p["losses"] for p in ranking) == 2


def test_old_tournament_matches_without_players_are_repaired(c):
    from app.models import MatchPlayer

    ids = many(c, 4)
    cid = new_comp(c, format="eliminacion")
    enroll(c, cid, ids)
    c.post(f"/api/competitions/{cid}/start")
    play_all(c, cid)
    before = {p["public_id"]: (p["rating"], p["games_played"]) for p in c.get("/api/ranking").json()}
    with engine.begin() as conn:  # simula la base creada con el error: partidas sin jugadores
        conn.execute(MatchPlayer.__table__.delete())
    assert c.post("/api/admin/recalculate").status_code == 200
    after = {p["public_id"]: (p["rating"], p["games_played"]) for p in c.get("/api/ranking").json()}
    assert after == before and any(g > 0 for _, g in after.values())
