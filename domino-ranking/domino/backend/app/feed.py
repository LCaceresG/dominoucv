"""Novedades públicas: últimas partidas (con ganador y perdedor), torneos y jugadores nuevos."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import competitions as comps
from .models import Competition, Match, Player
from .services import is_tiebreak_final, match_to_dict
from .social import list_posts, post_dict


def _stage_label(m: Match) -> str:
    if m.stage == "swiss":
        return f"Ronda {m.round}"
    if m.stage == "group":
        return f"Grupo {m.group}" if m.group else "Fase de grupos"
    if m.stage == "ko":
        if m.competition is not None and m.competition.format == "suizo_gran_final":
            return "Final (1.º vs 2.º)"
        return "Final" if m.bracket == "F" else "Fase final"
    return "Partida libre"


def _names(side: list[dict]) -> list[str]:
    return [p["name"] for p in side]


def feed(db: Session, limit: int = 60) -> list[dict]:
    items: list[dict] = []

    matches = db.scalars(
        select(Match).where(Match.status == "valid").order_by(Match.played_at.desc(), Match.id.desc()).limit(limit)
    ).all()
    for m in matches:
        d = match_to_dict(m)
        winner = "D" if m.points_a == m.points_b else ("A" if m.points_a > m.points_b else "B")
        items.append(
            {
                "type": "match",
                "date": d["played_at"],
                "sort": 1,
                "id": m.id,
                "competition_id": m.competition_id,
                "competition_name": d["competition_name"],
                "stage_label": _stage_label(m),
                "team_a": _names(d["team_a"]),
                "team_b": _names(d["team_b"]),
                "points_a": m.points_a,
                "points_b": m.points_b,
                "winner": winner,
                "final": bool(m.stage == "ko" and m.bracket == "F"),
                "tiebreak_final": is_tiebreak_final(m),
            }
        )

    for c in db.scalars(select(Competition).where(Competition.status != "draft")).all():
        if c.format == "libre":
            continue
        info = comps.detail(db, c)
        last = db.scalar(
            select(Match.played_at).where(Match.competition_id == c.id, Match.status == "valid").order_by(Match.played_at.desc())
        )
        finished = c.status == "finished"
        when = (last if finished and last else c.start_date) or date.today()
        items.append(
            {
                "type": "tournament_finished" if finished else "tournament_active",
                "date": when.isoformat(),
                "sort": 0,
                "id": c.id,
                "name": c.name,
                "format_label": comps.FORMAT_LABELS.get(c.format, c.format),
                "location": c.location,
                "podium": [{"place": p["place"], "name": p["name"]} for p in info["podium"]["places"]]
                if info.get("podium")
                else [],
                "provisional": not info["podium"]["final"] if info.get("podium") else True,
            }
        )

    for sp in list_posts(db, 10):
        d = post_dict(sp)
        items.append(
            {
                "type": "social", "date": d["posted_on"], "sort": 0.5, "id": sp.id, "title": d["title"],
                "summary": d["summary"][:220] + ("…" if len(d["summary"]) > 220 else ""),
                "image_url": d["image_url"], "link": d["link"],
            }
        )

    joined: dict[str, list[str]] = {}
    for p in db.scalars(select(Player).where(Player.active.is_(True)).order_by(Player.id.desc()).limit(200)).all():
        joined.setdefault(p.created_at.date().isoformat(), []).append(p.name)
    for day, names in list(joined.items())[:5]:
        items.append({"type": "players_joined", "date": day, "sort": 2, "count": len(names), "names": names[:6]})

    items.sort(key=lambda i: (i["date"] or "", -i["sort"], i.get("id") or 0), reverse=True)
    return items
