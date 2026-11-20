"""API de Dominó: jugadores con ID, ELO, ranking general, ligas y torneos.

Ejecutar:  uvicorn app.main:app --reload
Docs interactivas: http://localhost:8000/docs
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from . import competitions as comps
from . import feed as feed_mod
from . import services
from . import social
from .database import ensure_schema, engine, get_db
from .elo import PROVISIONAL_GAMES
from .models import Match, MatchPlayer, Player
from .universities import UNIVERSIDADES
from .schemas import (
    CATEGORIES,
    CompetitionCreate,
    CompetitionUpdate,
    EntryCreate,
    MatchCreate,
    MemberAdd,
    PlayerCreate,
    PlayerOut,
    PlayerUpdate,
    ResultUpdate,
    SocialPostCreate,
    SocialPostUpdate,
)


def _check_schema() -> None:
    insp = inspect(engine)
    if insp.has_table("players"):
        cols = {c["name"] for c in insp.get_columns("players")}
        if "category" not in cols:
            raise RuntimeError(
                "La base de datos es de una versión anterior. Cierra el servidor, borra el archivo "
                "'domino.db' (carpeta backend) y vuelve a iniciar. Para conservar datos reales haría "
                "falta una migración (Alembic)."
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _check_schema()
    # Crea tablas y agrega columnas nuevas sin borrar datos. Con datos reales, migra a Alembic.
    for col in ensure_schema():
        print(f"[base de datos] columna agregada: {col}")
    yield


app = FastAPI(title="Dominó Ranking", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Errores inesperados: la web muestra el motivo en vez de un 'Error 500' mudo.

    El detalle completo (traceback) sigue apareciendo en la ventana donde corre el servidor.
    Para ocultar el motivo en producción: SHOW_ERRORS=0.
    """
    if os.getenv("SHOW_ERRORS", "1") == "1":
        text = f"Error interno ({type(exc).__name__}): {exc}"
    else:
        text = "Error interno del servidor"
    return JSONResponse(status_code=500, content={"detail": text})


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Protege las escrituras. Si ADMIN_KEY no está definida (desarrollo), todo es abierto."""
    key = os.getenv("ADMIN_KEY")
    if key and x_admin_key != key:
        raise HTTPException(401, "Clave de administrador inválida")


admin = [Depends(require_admin)]


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    return {
        "categories": list(CATEGORIES),
        "formats": [{"value": k, "label": v} for k, v in comps.FORMAT_LABELS.items()],
        "universities": [{"value": v, "label": n} for v, n in UNIVERSIDADES],
        "instagram": {"handle": social.INSTAGRAM_HANDLE, "url": social.INSTAGRAM_URL},
    }


@app.get("/api/feed")
def feed(limit: int = Query(60, ge=1, le=200), db: Session = Depends(get_db)):
    """Novedades públicas: últimas partidas con ganador, torneos (en juego y terminados) y jugadores nuevos."""
    return feed_mod.feed(db, limit)


# ---------- Redes (Instagram @dominoucv) ----------

@app.get("/api/social")
def list_social(limit: int = Query(60, ge=1, le=200), db: Session = Depends(get_db)):
    return [social.post_dict(p) for p in social.list_posts(db, limit)]


@app.get("/api/social/{post_id}/image")
def social_image(post_id: int, db: Session = Depends(get_db)):
    p = social.get_post(db, post_id)
    if not p.image_type:
        raise HTTPException(404, "Esta publicación no tiene imagen")
    return Response(p.image, media_type=p.image_type, headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/social", status_code=201, dependencies=admin)
def create_social(data: SocialPostCreate, db: Session = Depends(get_db)):
    return social.post_dict(social.create_post(db, data))


@app.patch("/api/social/{post_id}", dependencies=admin)
def update_social(post_id: int, data: SocialPostUpdate, db: Session = Depends(get_db)):
    return social.post_dict(social.update_post(db, social.get_post(db, post_id), data))


@app.delete("/api/social/{post_id}", dependencies=admin)
def delete_social(post_id: int, db: Session = Depends(get_db)):
    db.delete(social.get_post(db, post_id))
    db.commit()
    return {"deleted": post_id}


# ---------- Jugadores ----------

@app.post("/api/players", response_model=PlayerOut, status_code=201, dependencies=admin)
def create_player(data: PlayerCreate, db: Session = Depends(get_db)):
    return services.create_player(db, data)


@app.get("/api/players", response_model=list[PlayerOut])
def list_players(
    search: str | None = Query(None, description="Nombre, apellido o ID; sin importar tildes"),
    limit: int = Query(50, ge=1, le=200),
    category: str | None = None,
    university: str | None = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
):
    return services.search_players(db, search, limit, category, university, include_inactive)


@app.get("/api/universities")
def universities(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Player.university)
        .where(Player.university.is_not(None), Player.active.is_(True))
        .distinct()
        .order_by(Player.university)
    ).all()
    return [r for r in rows if r]


@app.get("/api/players/{public_id}")
def player_profile(public_id: str, db: Session = Depends(get_db)):
    player = services.find_player(db, public_id)
    rank = None
    if player.active:
        better = db.scalar(
            select(func.count()).select_from(Player).where(Player.active.is_(True), Player.rating > player.rating)
        )
        rank = better + 1
    recent = db.scalars(
        select(Match)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .where(MatchPlayer.player_id == player.id, Match.status == "valid")
        .order_by(Match.played_at.desc(), Match.id.desc())
        .limit(20)
    ).all()
    return {
        **PlayerOut.model_validate(player).model_dump(),
        "rank": rank,
        "provisional": player.games_played < PROVISIONAL_GAMES,
        "competitions": comps.player_competitions(db, player),
        "recent_matches": [services.match_to_dict(m) for m in recent],
    }


@app.patch("/api/players/{public_id}", response_model=PlayerOut, dependencies=admin)
def update_player(public_id: str, data: PlayerUpdate, db: Session = Depends(get_db)):
    return services.update_player(db, services.find_player(db, public_id), data)


@app.delete("/api/players/{public_id}", dependencies=admin)
def delete_player(public_id: str, db: Session = Depends(get_db)):
    """Sin partidas ni inscripciones: se borra. Con historial: se archiva (deja de aparecer)."""
    return {"result": services.delete_player(db, services.find_player(db, public_id))}


# ---------- Ranking general ----------

@app.get("/api/ranking")
def ranking(
    min_games: int = Query(0, ge=0, description="Mínimo de partidas para aparecer"),
    limit: int = Query(100, ge=1, le=500),
    city: str | None = None,
    category: str | None = None,
    university: str | None = None,
    db: Session = Depends(get_db),
):
    q = select(Player).where(Player.active.is_(True), Player.games_played >= min_games)
    if city:
        q = q.where(func.lower(Player.city) == city.strip().lower())
    if category:
        q = q.where(Player.category == category)
    if university:
        q = q.where(Player.university == university)
    players = db.scalars(
        q.order_by(Player.rating.desc(), Player.games_played.desc(), Player.id).limit(limit)
    ).all()
    return [
        {
            "position": i,
            **PlayerOut.model_validate(p).model_dump(),
            "provisional": p.games_played < PROVISIONAL_GAMES,
        }
        for i, p in enumerate(players, 1)
    ]


# ---------- Ligas y torneos ----------

@app.post("/api/competitions", status_code=201, dependencies=admin)
def create_competition(data: CompetitionCreate, db: Session = Depends(get_db)):
    return comps.comp_dict(db, comps.create_competition(db, data))


@app.get("/api/competitions")
def list_competitions(db: Session = Depends(get_db)):
    return comps.list_competitions(db)


@app.get("/api/competitions/{competition_id}")
def competition_detail(competition_id: int, db: Session = Depends(get_db)):
    return comps.detail(db, comps.get_competition(db, competition_id))


@app.patch("/api/competitions/{competition_id}", dependencies=admin)
def update_competition(competition_id: int, data: CompetitionUpdate, db: Session = Depends(get_db)):
    comp = comps.update_competition(db, comps.get_competition(db, competition_id), data)
    return comps.comp_dict(db, comp)


@app.delete("/api/competitions/{competition_id}", dependencies=admin)
def delete_competition(competition_id: int, db: Session = Depends(get_db)):
    comps.delete_competition(db, comps.get_competition(db, competition_id))
    return {"deleted": competition_id}


@app.post("/api/competitions/{competition_id}/members", status_code=201, dependencies=admin)
def add_member(competition_id: int, data: MemberAdd, db: Session = Depends(get_db)):
    member = comps.add_member(db, comps.get_competition(db, competition_id), data.public_id)
    return {"competition_id": competition_id, "public_id": member.player.public_id}


@app.post("/api/competitions/{competition_id}/entries", status_code=201, dependencies=admin)
def add_entry(competition_id: int, data: EntryCreate, db: Session = Depends(get_db)):
    entry = comps.add_entry(db, comps.get_competition(db, competition_id), data)
    return {"id": entry.id, "name": entry.name}


@app.delete("/api/competitions/{competition_id}/entries/{entry_id}", dependencies=admin)
def remove_entry(competition_id: int, entry_id: int, db: Session = Depends(get_db)):
    comps.remove_entry(db, comps.get_competition(db, competition_id), entry_id)
    return {"deleted": entry_id}


@app.post("/api/competitions/{competition_id}/start", dependencies=admin)
def start_competition(competition_id: int, db: Session = Depends(get_db)):
    comp = comps.start_competition(db, comps.get_competition(db, competition_id))
    return comps.comp_dict(db, comp)


@app.post("/api/competitions/{competition_id}/next-round", dependencies=admin)
def next_round(competition_id: int, db: Session = Depends(get_db)):
    comp = comps.next_round(db, comps.get_competition(db, competition_id))
    return comps.comp_dict(db, comp)


@app.post("/api/competitions/{competition_id}/finish", dependencies=admin)
def finish_competition(competition_id: int, db: Session = Depends(get_db)):
    comp = comps.set_finished(db, comps.get_competition(db, competition_id), True)
    return comps.comp_dict(db, comp)


@app.post("/api/competitions/{competition_id}/reopen", dependencies=admin)
def reopen_competition(competition_id: int, db: Session = Depends(get_db)):
    comp = comps.set_finished(db, comps.get_competition(db, competition_id), False)
    return comps.comp_dict(db, comp)


# ---------- Partidas ----------

@app.post("/api/matches", status_code=201, dependencies=admin)
def create_match(data: MatchCreate, db: Session = Depends(get_db)):
    """Partida libre (amistosa o de una liga de formato libre). Se permiten empates."""
    return services.match_to_dict(services.register_match(db, data))


@app.get("/api/matches")
def list_matches(
    competition_id: int | None = None,
    player: str | None = Query(None, description="ID público del jugador"),
    free_only: bool = False,
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = select(Match).where(Match.status == "valid")
    if competition_id is not None:
        q = q.where(Match.competition_id == competition_id)
    if free_only:
        q = q.where(Match.stage == "free")
    if player:
        q = (
            q.join(MatchPlayer, MatchPlayer.match_id == Match.id)
            .join(Player, Player.id == MatchPlayer.player_id)
            .where(Player.public_id == player.strip().upper())
        )
    matches = db.scalars(q.order_by(Match.played_at.desc(), Match.id.desc()).limit(limit)).all()
    return [services.match_to_dict(m) for m in matches]


@app.put("/api/matches/{match_id}/result", dependencies=admin)
def set_result(match_id: int, data: ResultUpdate, db: Session = Depends(get_db)):
    """Registra o corrige el marcador y la fecha de una partida (libre o de torneo)."""
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(404, "Partida no encontrada")
    comps.set_result(db, match, data.points_a, data.points_b, data.played_at)
    return services.match_to_dict(match)


@app.delete("/api/matches/{match_id}", dependencies=admin)
def delete_match(match_id: int, db: Session = Depends(get_db)):
    """Elimina (anula) una partida libre y recalcula el ELO. Las de torneo se corrigen, no se borran."""
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(404, "Partida no encontrada")
    comps.void_free_match(db, match)
    return {"deleted": match_id}


@app.post("/api/admin/recalculate", dependencies=admin)
def recalculate(db: Session = Depends(get_db)):
    n = services.recalculate_all(db)
    db.commit()
    return {"matches_replayed": n}


# ---------- Web (frontend estático) ----------

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="web")
