"""Lógica de negocio: jugadores, partidas libres, recálculo de ELO y serialización."""
import unicodedata
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .elo import INITIAL_RATING, Participant, compute_deltas
from .models import Competition, CompetitionMember, Entry, Match, MatchPlayer, Player
from .schemas import MatchCreate, PlayerCreate, PlayerUpdate


def norm(text: str | None) -> str:
    """Minúsculas y sin tildes: 'Pérez' -> 'perez'. Sirve para buscar sin preocuparse por acentos."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


# ---------- Jugadores ----------

def _affiliation(category: str, university: str | None) -> str | None:
    university = (university or "").strip() or None
    if category == "externo":
        return None  # un externo no pertenece a la universidad
    if category == "sin_definir":
        return university  # falta el dato: la universidad es opcional hasta que se complete
    if not university:
        raise HTTPException(422, "Indica la universidad del jugador (o elige la categoría 'externo')")
    return university


def _clean(text: str | None) -> str | None:
    return (text or "").strip() or None


def new_player(db: Session, data: PlayerCreate) -> Player:
    """Crea el jugador y le asigna su ID público, sin confirmar (commit) todavía."""
    player = Player(
        name=data.name,
        search_name=norm(data.name),
        category=data.category,
        university=_affiliation(data.category, data.university),
        career=_clean(data.career),
        city=_clean(data.city),
        country=data.country.upper() if data.country else None,
    )
    db.add(player)
    db.flush()  # para obtener player.id
    player.public_id = f"DOM-{player.id:06d}"
    return player


def create_player(db: Session, data: PlayerCreate) -> Player:
    player = new_player(db, data)
    db.commit()
    db.refresh(player)
    return player


def find_player(db: Session, public_id: str) -> Player:
    player = db.scalar(select(Player).where(Player.public_id == public_id.strip().upper()))
    if player is None:
        raise HTTPException(404, "Jugador no encontrado")
    return player


def update_player(db: Session, player: Player, data: PlayerUpdate) -> Player:
    fields = data.model_fields_set
    if "name" in fields and data.name:
        player.name = " ".join(data.name.split())
        player.search_name = norm(player.name)
    if "category" in fields and data.category:
        player.category = data.category
    if "university" in fields or "category" in fields:
        uni = data.university if "university" in fields else player.university
        player.university = _affiliation(player.category, uni)
    if "career" in fields:
        player.career = _clean(data.career)
    if "city" in fields:
        player.city = _clean(data.city)
    if "country" in fields:
        player.country = data.country.upper() if data.country else None
    if "active" in fields and data.active is not None:
        player.active = data.active
    db.commit()
    db.refresh(player)
    return player


def player_has_history(db: Session, player: Player) -> bool:
    pid = player.id
    return any(
        db.scalar(q) is not None
        for q in (
            select(MatchPlayer.id).where(MatchPlayer.player_id == pid).limit(1),
            select(Entry.id).where((Entry.player1_id == pid) | (Entry.player2_id == pid)).limit(1),
            select(CompetitionMember.id).where(CompetitionMember.player_id == pid).limit(1),
        )
    )


def delete_player(db: Session, player: Player) -> str:
    """Sin historial se borra del todo. Con historial se archiva para no alterar el ELO de los demás."""
    if player_has_history(db, player):
        player.active = False
        db.commit()
        return "archived"
    db.delete(player)
    db.commit()
    return "deleted"


def search_players(
    db: Session,
    text: str | None = None,
    limit: int = 50,
    category: str | None = None,
    university: str | None = None,
    include_inactive: bool = False,
) -> list[Player]:
    q = select(Player)
    if not include_inactive:
        q = q.where(Player.active.is_(True))
    if category:
        q = q.where(Player.category == category)
    if university:
        q = q.where(Player.university == university)
    for token in norm(text).split():
        q = q.where(Player.search_name.contains(token) | Player.public_id.ilike(f"%{token}%"))
    return list(db.scalars(q.order_by(Player.search_name).limit(limit)).all())


def get_players(db: Session, public_ids: list[str], only_active: bool = True) -> list[Player]:
    rows = db.scalars(select(Player).where(Player.public_id.in_(public_ids))).all()
    found = {p.public_id: p for p in rows}
    missing = [i for i in public_ids if i not in found]
    if missing:
        raise HTTPException(404, f"Jugadores no encontrados: {', '.join(missing)}")
    if only_active:
        gone = [i for i in public_ids if not found[i].active]
        if gone:
            raise HTTPException(400, f"Jugadores eliminados (archivados): {', '.join(gone)}")
    return [found[i] for i in public_ids]


# ---------- ELO y estadísticas sobre partidas ----------

def _record_stats(p: Player, result: str, pf: int, pa: int) -> None:
    p.games_played += 1
    p.points_for += pf
    p.points_against += pa
    if result == "W":
        p.wins += 1
    elif result == "D":
        p.draws += 1
    else:
        p.losses += 1


def _commit_player(mp: MatchPlayer, delta: float, result: str, pf: int, pa: int) -> None:
    """Partida suelta: el ELO se actualiza de inmediato."""
    p = mp.player
    mp.rating_before = p.rating
    mp.delta = delta
    mp.rating_after = round(p.rating + delta, 2)
    p.rating = mp.rating_after
    _record_stats(p, result, pf, pa)
    p.peak_rating = max(p.peak_rating, p.rating)


def is_tiebreak_final(match: Match) -> bool:
    """Final 1.º vs 2.º del formato 'suizo + final': solo da una victoria al campeón."""
    comp = match.competition
    return match.stage == "ko" and match.bracket == "F" and comp is not None and comp.format == "suizo_gran_final"


def _apply_tiebreak_final(team_a: list[MatchPlayer], team_b: list[MatchPlayer], pa: int, pb: int) -> None:
    """Cuenta solo la victoria del campeón: sin derrota para el perdedor, sin puntos a favor/en contra
    (no afecta el average) y sin cambio de ELO. games_played sube solo para el campeón, así sigue
    siendo ganadas + empatadas + perdidas."""
    for team in (team_a, team_b):
        for mp in team:
            mp.rating_before = mp.rating_after = mp.player.rating
            mp.delta = 0.0
    if pa == pb:
        return
    for mp in team_a if pa > pb else team_b:
        mp.player.wins += 1
        mp.player.games_played += 1


def apply_match(match: Match) -> None:
    """Calcula el ELO de la partida con los ratings actuales y los aplica."""
    team_a = [mp for mp in match.players if mp.team == "A"]
    team_b = [mp for mp in match.players if mp.team == "B"]
    if not team_a or not team_b:
        return  # partida sin jugadores asignados: no puede mover el ELO
    pa, pb = match.points_a, match.points_b
    if is_tiebreak_final(match):
        _apply_tiebreak_final(team_a, team_b, pa, pb)
        return

    deltas_a, deltas_b = compute_deltas(
        [Participant(mp.player.rating, mp.player.games_played) for mp in team_a],
        [Participant(mp.player.rating, mp.player.games_played) for mp in team_b],
        pa,
        pb,
        match.target_points,
    )
    res_a, res_b = ("D", "D") if pa == pb else (("W", "L") if pa > pb else ("L", "W"))
    for mp, d in zip(team_a, deltas_a):
        _commit_player(mp, d, res_a, pa, pb)
    for mp, d in zip(team_b, deltas_b):
        _commit_player(mp, d, res_b, pb, pa)


def _apply_tournament_match(match: Match, base: dict, pending: dict[int, float]) -> None:
    """Partida de torneo, estilo FIDE: el ELO de cada rival se toma al INICIAR el torneo (no cambia entre
    rondas). El cambio de cada partida se va acumulando como 'pendiente' y solo se suma al rating al terminar."""
    team_a = [mp for mp in match.players if mp.team == "A"]
    team_b = [mp for mp in match.players if mp.team == "B"]
    if not team_a or not team_b:
        return
    pa, pb = match.points_a, match.points_b
    if is_tiebreak_final(match):
        _apply_tiebreak_final(team_a, team_b, pa, pb)
        return
    for mp in team_a + team_b:
        base.setdefault((match.competition_id, mp.player_id), (mp.player.rating, mp.player.games_played))
    key = lambda mp: base[(match.competition_id, mp.player_id)]  # noqa: E731
    deltas_a, deltas_b = compute_deltas(
        [Participant(*key(mp)) for mp in team_a], [Participant(*key(mp)) for mp in team_b], pa, pb, match.target_points,
        use_margin=False,  # en torneos, como en FIDE, solo importa ganar/perder, no por cuánto
    )
    res_a, res_b = ("D", "D") if pa == pb else (("W", "L") if pa > pb else ("L", "W"))
    for team, deltas, res, pf, against in ((team_a, deltas_a, res_a, pa, pb), (team_b, deltas_b, res_b, pb, pa)):
        for mp, d in zip(team, deltas):
            start = key(mp)[0]
            before = pending.get(mp.player_id, 0.0)
            pending[mp.player_id] = before + d
            mp.rating_before = round(start + before, 2)  # ELO "corriendo" durante el torneo
            mp.delta = d
            mp.rating_after = round(start + pending[mp.player_id], 2)
            _record_stats(mp.player, res, pf, against)


def recalculate_all(db: Session) -> int:
    """Reinicia ratings y estadísticas, y repite las partidas válidas en orden cronológico.

    Partidas sueltas y de ligas libres: el ELO cambia al instante. Torneos con formato: estilo FIDE, el cambio
    se acumula durante el torneo y se suma recién cuando termina (ver _apply_tournament_match).
    Se llama al editar o anular un resultado, al registrar una partida con fecha pasada
    o al cambiar la fórmula del ELO.
    """
    for p in db.scalars(select(Player)).all():
        p.rating = p.peak_rating = INITIAL_RATING
        p.rating_pending = 0.0
        p.games_played = p.wins = p.draws = p.losses = p.points_for = p.points_against = 0

    comps = {c.id: c for c in db.scalars(select(Competition)).all()}
    matches = db.scalars(
        select(Match).where(Match.status == "valid").order_by(Match.played_at, Match.id)
    ).all()

    def in_tournament(m: Match) -> bool:
        c = comps.get(m.competition_id) if m.competition_id else None
        return c is not None and c.format != "libre" and m.stage != "free"

    last_of: dict[int, int] = {}  # id de la última partida de cada torneo (cuando termina, se suma el ELO)
    for m in matches:
        if in_tournament(m):
            last_of[m.competition_id] = m.id

    base: dict = {}
    pending: dict[int, dict[int, float]] = {}
    players = {p.id: p for p in db.scalars(select(Player)).all()}

    def settle(comp_id: int) -> None:
        for pid, delta in pending.pop(comp_id, {}).items():
            p = players[pid]
            p.rating = round(p.rating + delta, 2)
            p.peak_rating = max(p.peak_rating, p.rating)

    for m in matches:
        ensure_match_players(db, m)  # repara partidas de torneo guardadas sin jugadores
        if in_tournament(m):
            _apply_tournament_match(m, base, pending.setdefault(m.competition_id, {}))
            if last_of.get(m.competition_id) == m.id and comps[m.competition_id].status == "finished":
                settle(m.competition_id)
        else:
            apply_match(m)

    for deltas in pending.values():  # torneos en curso: el cambio queda pendiente
        for pid, delta in deltas.items():
            players[pid].rating_pending = round(players[pid].rating_pending + delta, 2)
    return len(matches)


def ensure_match_players(db: Session, match: Match) -> None:
    """Crea los jugadores de una partida de torneo a partir de sus parejas, si faltan.

    Importante: se agregan con db.add() de forma explícita. En SQLAlchemy 2.0, un objeto creado
    con `MatchPlayer(match=match)` NO entra solo a la sesión cuando `match` ya existe.
    """
    if match.players or match.entry_a is None or match.entry_b is None:
        return
    for team, entry in (("A", match.entry_a), ("B", match.entry_b)):
        for p in entry.players:
            db.add(MatchPlayer(match=match, player=p, team=team))
    db.flush()


def register_match(db: Session, data: MatchCreate) -> Match:
    """Partida libre: amistosa o de una liga de formato libre."""
    comp = None
    if data.competition_id is not None:
        comp = db.get(Competition, data.competition_id)
        if comp is None:
            raise HTTPException(404, "Liga no encontrada")
        if comp.format != "libre":
            raise HTTPException(400, "Las partidas de un torneo se registran dentro del propio torneo")

    players = get_players(db, data.team_a + data.team_b)
    by_id = {p.public_id: p for p in players}

    if comp is not None:
        member_ids = set(
            db.scalars(select(CompetitionMember.player_id).where(CompetitionMember.competition_id == comp.id))
        )
        outside = [p.public_id for p in players if p.id not in member_ids]
        if outside:
            raise HTTPException(400, f"No están inscritos en la liga: {', '.join(outside)}")

    match = Match(
        competition_id=comp.id if comp else None,
        stage="free",
        status="valid",
        points_a=data.points_a,
        points_b=data.points_b,
        played_at=data.played_at or date.today(),
        target_points=data.target_points or (comp.target_points if comp else 100),
    )
    for pid in data.team_a:
        MatchPlayer(match=match, player=by_id[pid], team="A")
    for pid in data.team_b:
        MatchPlayer(match=match, player=by_id[pid], team="B")
    db.add(match)
    db.flush()
    recalculate_all(db)  # la fecha puede ser anterior a otras partidas
    db.commit()
    db.refresh(match)
    return match


# ---------- Presentación ----------

def player_brief(p: Player) -> dict:
    return {"public_id": p.public_id, "name": p.name}


def match_to_dict(m: Match) -> dict:
    def side(team: str, entry: Entry | None) -> list[dict]:
        if m.players:
            return [
                {
                    "public_id": mp.player.public_id,
                    "name": mp.player.name,
                    "rating_before": mp.rating_before,
                    "rating_after": mp.rating_after,
                    "delta": mp.delta,
                }
                for mp in m.players
                if mp.team == team
            ]
        if entry is not None:
            return [{**player_brief(p), "rating_before": None, "rating_after": None, "delta": None} for p in entry.players]
        return []

    def entry_ref(e: Entry | None):
        return {"id": e.id, "name": e.name} if e else None

    return {
        "id": m.id,
        "competition_id": m.competition_id,
        "competition_name": m.competition.name if m.competition else None,
        "stage": m.stage,
        "bracket": m.bracket,
        "round": m.round,
        "slot": m.slot,
        "group": m.group,
        "status": m.status,
        "played_at": m.played_at.isoformat() if m.played_at else None,
        "target_points": m.target_points,
        "points_a": m.points_a,
        "points_b": m.points_b,
        "team_a": side("A", m.entry_a),
        "team_b": side("B", m.entry_b),
        "entry_a": entry_ref(m.entry_a),
        "entry_b": entry_ref(m.entry_b),
    }
