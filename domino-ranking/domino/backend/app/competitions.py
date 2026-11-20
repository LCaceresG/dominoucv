"""Ligas y torneos: crear, inscribir, iniciar, rondas, resultados, tablas y podio (top 3)."""
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import tournament as T
from .models import Competition, CompetitionMember, Entry, Match, MatchPlayer
from .schemas import CompetitionCreate, CompetitionUpdate, EntryCreate
from .services import ensure_match_players, find_player, get_players, match_to_dict, player_brief, recalculate_all

FORMAT_LABELS = {
    "libre": "Libre (partidas manuales)",
    "suizo": "Sistema suizo",
    "eliminacion": "Eliminación directa",
    "grupos_eliminacion": "Fase de grupos + eliminación directa",
    "doble_eliminacion": "Doble eliminación",
    "suizo_final": "Suizo + fase final (semifinal y final)",
    "suizo_gran_final": "Suizo + final 1.º vs 2.º",
}
SWISS_FORMATS = ("suizo", "suizo_final", "suizo_gran_final")


# ---------------------------------------------------------------- acceso a datos

def get_competition(db: Session, competition_id: int) -> Competition:
    comp = db.get(Competition, competition_id)
    if comp is None:
        raise HTTPException(404, "Liga o torneo no encontrado")
    return comp


def _entries(db: Session, comp: Competition) -> list[Entry]:
    return list(
        db.scalars(select(Entry).where(Entry.competition_id == comp.id).order_by(Entry.seed, Entry.id)).all()
    )


def _matches(db: Session, comp: Competition) -> list[Match]:
    return list(db.scalars(select(Match).where(Match.competition_id == comp.id).order_by(Match.id)).all())


def _load(db: Session, comp: Competition):
    matches = _matches(db, comp)
    entries = _entries(db, comp)
    seeds = [e.id for e in entries if e.seed and not e.withdrawn]
    only_tie = comp.format == "suizo_gran_final" and bool(comp.final_only_on_tie)
    ctx = T.build_ctx(
        matches, seeds, comp.points_per_win, comp.points_per_draw, comp.swiss_rounds or 0,
        final_only_on_tie=only_tie,
    )
    return matches, entries, seeds, ctx


# ---------------------------------------------------------------- crear / editar

def create_competition(db: Session, data: CompetitionCreate) -> Competition:
    comp = Competition(**data.model_dump())
    comp.final_only_on_tie = bool(data.final_only_on_tie) and data.format == "suizo_gran_final"
    comp.start_date = data.start_date or date.today()
    comp.status = "active" if data.format == "libre" else "draft"
    if data.format == "grupos_eliminacion":
        comp.groups_count = data.groups_count or 2
        comp.qualifiers = data.qualifiers or 2
    elif data.format == "suizo_final":
        comp.qualifiers = data.qualifiers or 4
    db.add(comp)
    db.commit()
    db.refresh(comp)
    return comp


def update_competition(db: Session, comp: Competition, data: CompetitionUpdate) -> Competition:
    fields = data.model_fields_set
    if "name" in fields and data.name:
        comp.name = " ".join(data.name.split())
    if "location" in fields:
        comp.location = (data.location or "").strip() or None
    if "season" in fields:
        comp.season = (data.season or "").strip() or None
    if "start_date" in fields and data.start_date:
        comp.start_date = data.start_date
    db.commit()
    db.refresh(comp)
    return comp


def delete_competition(db: Session, comp: Competition) -> None:
    for m in _matches(db, comp):
        db.delete(m)
    db.flush()
    for e in _entries(db, comp):
        db.delete(e)
    for mem in db.scalars(select(CompetitionMember).where(CompetitionMember.competition_id == comp.id)).all():
        db.delete(mem)
    db.delete(comp)
    db.flush()
    recalculate_all(db)
    db.commit()


def set_finished(db: Session, comp: Competition, finished: bool) -> Competition:
    if comp.format != "libre":
        raise HTTPException(400, "Los torneos con formato se cierran solos al jugarse todas las partidas")
    comp.status = "finished" if finished else "active"
    db.commit()
    db.refresh(comp)
    return comp


# ---------------------------------------------------------------- inscripción

def add_member(db: Session, comp: Competition, public_id: str) -> CompetitionMember:
    if comp.format != "libre":
        raise HTTPException(400, "En este formato se inscriben parejas (participantes), no jugadores sueltos")
    player = find_player(db, public_id)
    if not player.active:
        raise HTTPException(400, "El jugador está eliminado (archivado)")
    exists = db.scalar(
        select(CompetitionMember).where(
            CompetitionMember.competition_id == comp.id, CompetitionMember.player_id == player.id
        )
    )
    if exists:
        raise HTTPException(409, "El jugador ya está en la liga")
    member = CompetitionMember(competition_id=comp.id, player_id=player.id)
    db.add(member)
    db.commit()
    return member


def add_entry(db: Session, comp: Competition, data: EntryCreate) -> Entry:
    if comp.format == "libre":
        raise HTTPException(400, "Esta liga es de formato libre: inscribe jugadores, no parejas")
    if comp.status != "draft":
        raise HTTPException(400, "El torneo ya comenzó: no se pueden agregar participantes")
    ids = [i.strip().upper() for i in data.players]
    if len(ids) != comp.team_size:
        raise HTTPException(400, f"Cada participante debe tener {comp.team_size} jugador(es)")
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "Los dos jugadores de una pareja deben ser distintos")
    players = get_players(db, ids)
    taken = {pid for e in _entries(db, comp) for pid in (e.player1_id, e.player2_id) if pid}
    clash = [p.name for p in players if p.id in taken]
    if clash:
        raise HTTPException(409, f"Ya están inscritos en este torneo: {', '.join(clash)}")
    name = (data.name or "").strip() or " / ".join(p.name for p in players)
    entry = Entry(
        competition_id=comp.id,
        name=name[:160],
        player1_id=players[0].id,
        player2_id=players[1].id if len(players) > 1 else None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def remove_entry(db: Session, comp: Competition, entry_id: int) -> None:
    if comp.status != "draft":
        raise HTTPException(400, "El torneo ya comenzó: no se pueden quitar participantes")
    entry = db.get(Entry, entry_id)
    if entry is None or entry.competition_id != comp.id:
        raise HTTPException(404, "Participante no encontrado")
    db.delete(entry)
    db.commit()


# ---------------------------------------------------------------- iniciar y rondas

def _insert_specs(db: Session, comp: Competition, specs: list[T.Spec]) -> None:
    ids: dict[int, int] = {}

    def fix(src):
        if src and src[:2] in ("W:", "L:"):
            kind, key = src.split(":")
            return f"{kind}:{ids[int(key)]}"
        return src

    for s in specs:
        m = Match(
            competition_id=comp.id,
            stage=s.stage,
            bracket=s.bracket,
            round=s.round,
            slot=s.slot,
            group=s.group,
            src_a=fix(s.src_a),
            src_b=fix(s.src_b),
            status="pending",
            target_points=comp.target_points,
        )
        if s.stage == "group":  # los grupos conocen a sus participantes desde el inicio
            m.entry_a_id = int(s.src_a[2:])
            m.entry_b_id = int(s.src_b[2:])
        db.add(m)
        db.flush()
        ids[s.key] = m.id


def _create_swiss_round(db: Session, comp: Competition, seeds: list[int], matches: list[Match], round_no: int) -> None:
    pairs, bye = T.plan_swiss_round(seeds, matches, round_no, comp.points_per_win, comp.points_per_draw)
    slot = 1
    for a, b in pairs:
        db.add(
            Match(
                competition_id=comp.id, stage="swiss", round=round_no, slot=slot,
                entry_a_id=a, entry_b_id=b, status="pending", target_points=comp.target_points,
            )
        )
        slot += 1
    if bye is not None:
        db.add(
            Match(
                competition_id=comp.id, stage="swiss", round=round_no, slot=slot,
                entry_a_id=bye, entry_b_id=None, status="bye", target_points=comp.target_points,
            )
        )
    db.flush()


def start_competition(db: Session, comp: Competition) -> Competition:
    if comp.format == "libre":
        raise HTTPException(400, "Las ligas de formato libre no se 'inician': registra partidas cuando quieras")
    if comp.status != "draft":
        raise HTTPException(400, "El torneo ya fue iniciado")
    entries = _entries(db, comp)
    n = len(entries)
    fmt = comp.format

    minimum = {"doble_eliminacion": 4, "suizo_final": 4, "suizo_gran_final": 3}.get(fmt, 2)
    if n < minimum:
        raise HTTPException(400, f"Se necesitan al menos {minimum} participantes (hay {n})")

    g = q = 0
    if fmt == "grupos_eliminacion":
        g, q = comp.groups_count or 2, comp.qualifiers or 2
        if n // g < max(2, q):
            raise HTTPException(
                400, f"Con {g} grupos y {q} clasificados por grupo se necesitan al menos {g * max(2, q)} participantes"
            )
    if fmt == "suizo_final":
        q = comp.qualifiers or 4
        if n < q:
            raise HTTPException(400, f"Se necesitan al menos {q} participantes para la fase final")

    # Siembra: de mayor a menor ELO promedio
    def strength(e: Entry) -> float:
        return sum(p.rating for p in e.players) / len(e.players)

    for i, e in enumerate(sorted(entries, key=lambda e: (-strength(e), e.id)), 1):
        e.seed = i
    db.flush()
    seeds = [e.id for e in sorted(entries, key=lambda e: e.seed)]

    if fmt == "eliminacion":
        _insert_specs(db, comp, T.build_single_elim([f"E:{i}" for i in seeds]))
    elif fmt == "doble_eliminacion":
        _insert_specs(db, comp, T.build_double_elim([f"E:{i}" for i in seeds]))
    elif fmt == "grupos_eliminacion":
        groups = T.snake_groups(seeds, g)
        labels = [T.group_label(i) for i in range(g)]
        by_id = {e.id: e for e in entries}
        for label, ids in zip(labels, groups):
            for eid in ids:
                by_id[eid].group_label = label
        _insert_specs(db, comp, T.build_groups(dict(zip(labels, groups))))
        _insert_specs(db, comp, T.build_single_elim(T.group_qualifier_sources(labels, q)))
        comp.groups_count, comp.qualifiers = g, q
    elif fmt in SWISS_FORMATS:
        total = min(comp.swiss_rounds or T.default_swiss_rounds(n), max(n - 1, 1))
        comp.swiss_rounds = total
        _create_swiss_round(db, comp, seeds, [], 1)
        if fmt == "suizo_final":
            _insert_specs(db, comp, T.build_single_elim([f"R:{i}" for i in range(1, q + 1)]))
        elif fmt == "suizo_gran_final":
            # Una sola final: 1.º vs 2.º del suizo (sin partido por el tercer puesto)
            _insert_specs(db, comp, T.build_single_elim(["R:1", "R:2"], third_place=False))
        comp.qualifiers = q if fmt == "suizo_final" else comp.qualifiers

    comp.status = "active"
    db.flush()
    refresh(db, comp)
    db.commit()
    db.refresh(comp)
    return comp


def next_round(db: Session, comp: Competition) -> Competition:
    if comp.format not in SWISS_FORMATS:
        raise HTTPException(400, "Solo el sistema suizo se juega por rondas")
    if comp.status != "active":
        raise HTTPException(400, "El torneo no está en curso")
    matches, entries, seeds, _ = _load(db, comp)
    swiss = [m for m in matches if m.stage == "swiss"]
    last = max(m.round for m in swiss)
    total = comp.swiss_rounds or 0
    if last >= total:
        raise HTTPException(400, "Ya se generaron todas las rondas del sistema suizo")
    if not T.all_decided([m for m in swiss if m.round == last]):
        raise HTTPException(400, f"Registra todos los resultados de la ronda {last} antes de generar la siguiente")
    _create_swiss_round(db, comp, seeds, matches, last + 1)
    refresh(db, comp)
    db.commit()
    db.refresh(comp)
    return comp


# ---------------------------------------------------------------- sincronizar y resultados

def is_complete(comp: Competition, matches: list[Match]) -> bool:
    if not T.all_decided(matches):
        return False
    if comp.format == "suizo":
        return max((m.round for m in matches if m.stage == "swiss"), default=0) >= (comp.swiss_rounds or 0)
    return True


def refresh(db: Session, comp: Competition):
    """Recalcula quién juega contra quién y actualiza el estado del torneo."""
    matches, entries, seeds, ctx = _load(db, comp)
    T.sync(matches, ctx)
    db.flush()
    if comp.format != "libre" and comp.status in ("active", "finished"):
        comp.status = "finished" if is_complete(comp, matches) else "active"
    return matches, entries, seeds, ctx


def set_result(db: Session, match: Match, points_a: int, points_b: int, played_at: date | None) -> Match:
    """Registra o corrige el resultado de una partida (libre o de torneo)."""
    if match.status == "void":
        raise HTTPException(400, "La partida está anulada")
    if match.status == "bye":
        raise HTTPException(400, "Un pase libre no tiene resultado")

    comp = db.get(Competition, match.competition_id) if match.competition_id else None
    in_tournament = comp is not None and match.stage != "free"

    if in_tournament:
        if match.entry_a_id is None or match.entry_b_id is None:
            raise HTTPException(400, "Esta partida todavía no tiene sus dos participantes definidos")
        if match.stage == "ko" and points_a == points_b:
            raise HTTPException(400, "En eliminación no puede haber empate: ajusta el marcador para que haya ganador")
        if match.status == "valid":
            blockers = T.dependents_with_results(match, _matches(db, comp))
            if blockers:
                raise HTTPException(
                    409,
                    "No se puede cambiar este resultado porque ya se jugaron partidas que dependen de él. "
                    "Corrige primero las partidas posteriores.",
                )

    match.points_a, match.points_b = points_a, points_b
    match.played_at = played_at or match.played_at or date.today()
    match.status = "valid"

    if in_tournament:
        ensure_match_players(db, match)
    db.flush()

    if in_tournament:
        refresh(db, comp)
    recalculate_all(db)
    db.commit()
    db.refresh(match)
    return match


def void_free_match(db: Session, match: Match) -> None:
    if match.stage != "free":
        raise HTTPException(400, "Las partidas de un torneo no se anulan: corrige su resultado")
    match.status = "void"
    db.flush()
    recalculate_all(db)
    db.commit()


# ---------------------------------------------------------------- presentación

def _entry_dict(e: Entry) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "seed": e.seed,
        "group": e.group_label,
        "withdrawn": bool(e.withdrawn),
        "players": [player_brief(p) for p in e.players],
    }


def comp_dict(db: Session, comp: Competition) -> dict:
    if comp.format == "libre":
        count = len(db.scalars(select(CompetitionMember.id).where(CompetitionMember.competition_id == comp.id)).all())
    else:
        count = len(db.scalars(select(Entry.id).where(Entry.competition_id == comp.id)).all())
    return {
        "id": comp.id,
        "name": comp.name,
        "kind": comp.kind,
        "format": comp.format,
        "format_label": FORMAT_LABELS.get(comp.format, comp.format),
        "final_only_on_tie": bool(comp.final_only_on_tie),
        "location": comp.location,
        "season": comp.season,
        "start_date": comp.start_date.isoformat() if comp.start_date else None,
        "status": comp.status,
        "team_size": comp.team_size,
        "points_per_win": comp.points_per_win,
        "points_per_draw": comp.points_per_draw,
        "target_points": comp.target_points,
        "swiss_rounds": comp.swiss_rounds,
        "groups_count": comp.groups_count,
        "qualifiers": comp.qualifiers,
        "participants": count,
    }


def list_competitions(db: Session) -> list[dict]:
    comps = db.scalars(select(Competition).order_by(Competition.start_date.desc(), Competition.id.desc())).all()
    return [comp_dict(db, c) for c in comps]


def _libre_table(db: Session, comp: Competition, matches: list[Match]) -> list[dict]:
    members = db.scalars(select(CompetitionMember).where(CompetitionMember.competition_id == comp.id)).all()
    table = {
        m.player_id: {
            "public_id": m.player.public_id, "name": m.player.name, "elo": round(m.player.rating, 1),
            "played": 0, "wins": 0, "draws": 0, "losses": 0, "pf": 0, "pa": 0, "diff": 0, "points": 0,
        }
        for m in members
    }
    for m in matches:
        if m.status != "valid":
            continue
        for mp in m.players:
            row = table.get(mp.player_id)
            if row is None:
                continue
            mine, theirs = (m.points_a, m.points_b) if mp.team == "A" else (m.points_b, m.points_a)
            row["played"] += 1
            row["pf"] += mine
            row["pa"] += theirs
            if mine > theirs:
                row["wins"] += 1
                row["points"] += comp.points_per_win
            elif mine == theirs:
                row["draws"] += 1
                row["points"] += comp.points_per_draw
            else:
                row["losses"] += 1
    rows = sorted(table.values(), key=lambda r: (-r["points"], -(r["pf"] - r["pa"]), -r["elo"], r["name"]))
    for i, r in enumerate(rows, 1):
        r["diff"] = r["pf"] - r["pa"]
        r["position"] = i
    return rows


def _elo_by_player(matches: list[Match]) -> dict[int, float]:
    """Cambio de ELO acumulado de cada jugador en las partidas válidas de este torneo."""
    acc: dict[int, float] = {}
    for m in matches:
        if m.status == "valid":
            for mp in m.players:
                acc[mp.player_id] = acc.get(mp.player_id, 0.0) + (mp.delta or 0.0)
    return acc


def _entry_rows(rows: list[dict], by_id: dict[int, Entry], elo: dict[int, float] | None = None) -> list[dict]:
    out = []
    for r in rows:
        e = by_id[r["id"]]
        deltas = [elo.get(p.id, 0.0) for p in e.players] if elo is not None else []
        elo_delta = round(sum(deltas) / len(deltas), 1) if deltas else None
        out.append(
            {
                "position": r["position"], "entry_id": e.id, "name": e.name,
                "players": [player_brief(p) for p in e.players],
                "played": r["played"], "wins": r["wins"], "draws": r["draws"], "losses": r["losses"],
                "pf": r["pf"], "pa": r["pa"], "diff": r["diff"],
                "buchholz": r["buchholz"], "points": r["points"], "byes": r["byes"],
                "withdrawn": bool(e.withdrawn),
                "elo_delta": elo_delta,
            }
        )
    return out


def _ko_titles(matches: list[Match], final_title: str | None = None):
    ko = [m for m in matches if m.stage == "ko"]
    counts: dict[tuple, int] = {}
    for m in ko:
        counts[(m.bracket, m.round)] = counts.get((m.bracket, m.round), 0) + 1
    double = any(m.bracket in ("W", "L") for m in ko)
    round_names = {2: "Semifinal", 4: "Cuartos de final", 8: "Octavos de final", 16: "Dieciseisavos de final"}

    def title(m: Match) -> str:
        b = m.bracket
        if b == "F":
            return final_title or ("Gran final" if double else "Final")
        if b == "T":
            return "Tercer puesto"
        if b == "W":
            return f"Ganadores · ronda {m.round}"
        if b == "L":
            return f"Perdedores · ronda {m.round}"
        return round_names.get(counts.get((b, m.round), 0), f"Ronda {m.round}")

    def short(m: Match) -> str:
        t = title(m)
        if counts.get((m.bracket, m.round), 0) > 1:
            return f"{t} {m.slot}" if m.bracket == "K" else f"{t}, partida {m.slot}"
        return t

    return title, short


def _src_label(src: str | None, shorts: dict[int, str]) -> str | None:
    if not src:
        return None
    kind, *rest = src.split(":")
    if kind == "W":
        return f"Ganador de {shorts.get(int(rest[0]), 'otra partida')}"
    if kind == "L":
        return f"Perdedor de {shorts.get(int(rest[0]), 'otra partida')}"
    if kind == "R":
        return f"Puesto {rest[0]} de la fase suiza"
    if kind == "G":
        return f"Puesto {rest[1]} del grupo {rest[0]}"
    if kind == "BYE":
        return "Pase libre"
    return None


def _sections(matches: list[Match], final_title: str | None = None) -> list[dict]:
    title, short = _ko_titles(matches, final_title)
    shorts = {m.id: short(m) for m in matches if m.stage == "ko"}

    def mdict(m: Match) -> dict:
        d = match_to_dict(m)
        d["a_label"] = _src_label(m.src_a, shorts) if m.entry_a_id is None else None
        d["b_label"] = _src_label(m.src_b, shorts) if m.entry_b_id is None else None
        d["title"] = shorts.get(m.id)
        # Final omitida: no hubo empate por el primer lugar, así que el 1.º del suizo es campeón directo
        d["skipped"] = m.stage == "ko" and m.bracket == "F" and m.status == "bye"
        d["editable"] = m.status in ("pending", "valid") and m.entry_a_id is not None and m.entry_b_id is not None
        d["locked"] = m.status == "valid" and bool(T.dependents_with_results(m, matches))
        return d

    out: list[dict] = []
    swiss = [m for m in matches if m.stage == "swiss"]
    for r in sorted({m.round for m in swiss}):
        ms = sorted((m for m in swiss if m.round == r), key=lambda m: m.slot)
        out.append({"phase": "swiss", "key": f"swiss-{r}", "title": f"Ronda {r}", "matches": [mdict(m) for m in ms]})

    groups = [m for m in matches if m.stage == "group"]
    for label in sorted({m.group for m in groups}):
        ms = sorted((m for m in groups if m.group == label), key=lambda m: (m.round, m.slot))
        out.append({"phase": "group", "key": f"group-{label}", "title": f"Grupo {label}", "matches": [mdict(m) for m in ms]})

    rank = {"K": 0, "W": 0, "L": 1, "F": 2, "T": 3}
    ko = [m for m in matches if m.stage == "ko"]
    for bracket, r in sorted({(m.bracket, m.round) for m in ko}, key=lambda x: (rank[x[0]], x[1])):
        ms = sorted((m for m in ko if m.bracket == bracket and m.round == r), key=lambda m: m.slot)
        out.append(
            {"phase": "ko", "key": f"ko-{bracket}-{r}", "title": title(ms[0]), "matches": [mdict(m) for m in ms]}
        )
    return out


def _podium(comp: Competition, matches, entries, ctx, libre_rows, swiss_rows) -> dict:
    by_id = {e.id: e for e in entries}
    complete = comp.status == "finished"
    places: list[dict] = []

    def from_entry(place: int, eid: int):
        e = by_id[eid]
        places.append({"place": place, "name": e.name, "players": [player_brief(p) for p in e.players]})

    if comp.format == "libre":
        for r in libre_rows[:3]:
            if r["played"]:
                places.append(
                    {"place": r["position"], "name": r["name"], "players": [{"public_id": r["public_id"], "name": r["name"]}]}
                )
    elif comp.format == "suizo":
        order = ctx.swiss_order or [r["entry_id"] for r in swiss_rows if r["played"] or r["byes"]]
        for i, eid in enumerate(order[:3], 1):
            from_entry(i, eid)
    elif comp.format == "suizo_gran_final":
        order = ctx.swiss_order or [
            r["entry_id"] for r in swiss_rows if (r["played"] or r["byes"]) and not r.get("withdrawn")
        ]
        final = next((m for m in matches if m.stage == "ko" and m.bracket == "F"), None)
        played = T.outcome(final) if final is not None and final.status == "valid" else None
        if played:  # la final ya se jugó: campeón y subcampeón salen de ella; el 3.º es el 3.º del suizo
            winner, loser = played
            from_entry(1, winner)
            from_entry(2, loser)
            rest = [e for e in order if e not in (winner, loser)]
            if rest:
                from_entry(3, rest[0])
        else:  # final pendiente (podio provisional) u omitida por no haber empate
            for i, eid in enumerate(order[:3], 1):
                from_entry(i, eid)
    else:
        for place, eid in T.ko_podium(matches, double=comp.format == "doble_eliminacion"):
            from_entry(place, eid)
    return {"final": complete, "places": places}


def detail(db: Session, comp: Competition) -> dict:
    matches, entries, seeds, ctx = _load(db, comp)
    by_id = {e.id: e for e in entries}
    out: dict = {
        "competition": comp_dict(db, comp),
        "entries": [_entry_dict(e) for e in entries],
        "members": [],
        "standings": [],
        "groups": [],
        "sections": [],
        "swiss": None,
        "recent_matches": [],
    }
    libre_rows: list[dict] = []
    swiss_rows: list[dict] = []
    elo = _elo_by_player(matches)

    if comp.format == "libre":
        members = db.scalars(select(CompetitionMember).where(CompetitionMember.competition_id == comp.id)).all()
        out["members"] = [player_brief(m.player) for m in members]
        libre_rows = _libre_table(db, comp, matches)
        out["standings"] = libre_rows
        valid = [m for m in matches if m.status == "valid"]
        valid.sort(key=lambda m: (m.played_at or date.min, m.id), reverse=True)
        out["recent_matches"] = [match_to_dict(m) for m in valid[:30]]
    else:
        final_title = "Final de desempate (1.º vs 2.º)" if comp.final_only_on_tie else (
            "Final (1.º vs 2.º)" if comp.format == "suizo_gran_final" else None
        )
        out["sections"] = _sections(matches, final_title)
        if comp.format in SWISS_FORMATS and any(m.stage == "swiss" for m in matches):
            table = T.swiss_table(matches, seeds, comp.points_per_win, comp.points_per_draw)
            if comp.format == "suizo_gran_final":
                table = T.final_win_bonus(matches, table, comp.points_per_win)
            swiss_rows = _entry_rows(table, by_id, elo)
            sw = [m for m in matches if m.stage == "swiss"]
            # Parejas retiradas: salen al final de la lista, sin puesto, con lo que jugaron antes de irse
            retiradas = [e.id for e in entries if e.withdrawn]
            if retiradas:
                rows_w = sorted(T.compute_table(retiradas, T.results_of(sw), comp.points_per_win, comp.points_per_draw).values(),
                                key=lambda r: (-r["points"], -r["diff"], r["id"]))
                for r in rows_w:
                    r["position"] = None
                swiss_rows += _entry_rows(rows_w, by_id, elo)
            out["standings"] = swiss_rows
            generated = max(m.round for m in sw)
            total = comp.swiss_rounds or 0
            out["swiss"] = {
                "total": total,
                "generated": generated,
                "can_next": comp.status == "active"
                and generated < total
                and T.all_decided([m for m in sw if m.round == generated]),
            }
        labels = sorted({m.group for m in matches if m.stage == "group"})
        for label in labels:
            table = T.group_table(matches, label, seeds, comp.points_per_win, comp.points_per_draw)
            out["groups"].append({"label": label, "rows": _entry_rows(table, by_id, elo)})

    out["podium"] = _podium(comp, matches, entries, ctx, libre_rows, swiss_rows)
    return out


def player_competitions(db: Session, player) -> list[dict]:
    """Ligas y torneos donde jugó (o está inscrito) un jugador, con sede y puesto final."""
    pid = player.id
    ids = set(db.scalars(select(CompetitionMember.competition_id).where(CompetitionMember.player_id == pid)))
    ids |= set(db.scalars(select(Entry.competition_id).where((Entry.player1_id == pid) | (Entry.player2_id == pid))))
    ids |= set(
        db.scalars(
            select(Match.competition_id)
            .join(MatchPlayer, MatchPlayer.match_id == Match.id)
            .where(MatchPlayer.player_id == pid, Match.competition_id.is_not(None))
        )
    )
    if not ids:
        return []
    comps = db.scalars(
        select(Competition).where(Competition.id.in_(ids)).order_by(Competition.start_date.desc(), Competition.id.desc())
    ).all()
    out = []
    for c in comps:
        place = None
        if c.status == "finished":
            for p in detail(db, c)["podium"]["places"]:
                if any(x["public_id"] == player.public_id for x in p["players"]):
                    place = p["place"]
        out.append(
            {
                "id": c.id, "name": c.name, "kind": c.kind,
                "format_label": FORMAT_LABELS.get(c.format, c.format),
                "location": c.location,
                "start_date": c.start_date.isoformat() if c.start_date else None,
                "status": c.status, "place": place,
            }
        )
    return out
