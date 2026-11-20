"""Importa un torneo ya jugado (jugadores, parejas y resultados ronda por ronda) a la base de datos.

Es lo que usa `importar_torneo_ucv.py`. Todo ocurre en una sola transacción: si algo falla, no se
guarda nada. Los jugadores que ya existen (mismo nombre, sin importar tildes ni mayúsculas) se
reutilizan en lugar de duplicarse, y sus datos NO se pisan: solo se completan los que estén vacíos.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import competitions as comps
from . import tournament as T
from .models import Competition, Entry, Match, Player
from .schemas import PlayerCreate
from .services import ensure_match_players, new_player, norm, recalculate_all


class ImportError_(ValueError):
    """Datos del torneo inválidos o torneo ya existente (el mensaje es apto para mostrar)."""


def _validar(datos: dict) -> None:
    jugadores = datos.get("jugadores", {})
    claves: dict[str, dict] = {}
    for p in datos.get("parejas", []):
        if p["clave"] in claves:
            raise ImportError_(f"Pareja repetida: {p['clave']}")
        faltan = [j for j in p["jugadores"] if j not in jugadores]
        if faltan:
            raise ImportError_(f"La pareja {p['clave']} usa jugadores sin definir: {', '.join(faltan)}")
        claves[p["clave"]] = p
    usados: dict[str, str] = {}
    for p in claves.values():
        for j in p["jugadores"]:
            if j in usados:
                raise ImportError_(f"El jugador '{j}' está en dos parejas ({usados[j]} y {p['clave']})")
            usados[j] = p["clave"]
    for n, ronda in enumerate(datos.get("rondas", []), 1):
        vistos: set[str] = set()
        for a, b, pa, pb in ronda:
            for k in (a, b):
                if k not in claves:
                    raise ImportError_(f"Ronda {n}: pareja desconocida '{k}'")
                if k in vistos:
                    raise ImportError_(f"Ronda {n}: la pareja {k} juega dos veces")
                vistos.add(k)
            if a == b:
                raise ImportError_(f"Ronda {n}: {a} no puede jugar contra sí misma")
            if min(pa, pb) < 0:
                raise ImportError_(f"Ronda {n}: marcador negativo en {a}-{b}")


def _jugador(db: Session, d: dict, resumen: dict) -> Player:
    nombre = " ".join(d["nombre"].split())
    hallados = list(db.scalars(select(Player).where(Player.search_name == norm(nombre))).all())
    if len(hallados) > 1:
        ids = ", ".join(p.public_id for p in hallados)
        raise ImportError_(f"Hay varios jugadores llamados '{nombre}' ({ids}); renombra o archiva los duplicados")
    if hallados:
        p = hallados[0]
        # Solo se completan datos vacíos; lo que ya tiene el jugador no se toca.
        if not p.university and d.get("universidad"):
            p.university = d["universidad"]
        if not p.career and d.get("carrera"):
            p.career = d["carrera"]
        if p.category == "sin_definir" and d.get("categoria", "sin_definir") != "sin_definir":
            p.category = d["categoria"]
        if not p.active:
            p.active = True
        resumen["reutilizados"].append(p)
        return p
    p = new_player(
        db,
        PlayerCreate(
            name=nombre,
            category=d.get("categoria", "sin_definir"),
            university=d.get("universidad"),
            career=d.get("carrera"),
            city=d.get("ciudad"),
            country=d.get("pais"),
        ),
    )
    resumen["creados"].append(p)
    return p


def _registrar_final(db: Session, comp: Competition, final: dict, entradas: dict, dia: date) -> None:
    """Pone el marcador de la final 1.º vs 2.º (los lados salen de la tabla del suizo)."""
    m = db.scalar(select(Match).where(Match.competition_id == comp.id, Match.stage == "ko", Match.bracket == "F"))
    if m is None or m.entry_a_id is None or m.entry_b_id is None:
        raise ImportError_("No se pudo armar la final: las 5 rondas del suizo deben estar completas")
    a, b = entradas[final["a"]].id, entradas[final["b"]].id
    if {m.entry_a_id, m.entry_b_id} != {a, b}:
        raise ImportError_(
            f"La final del archivo ({final['a']} vs {final['b']}) no coincide con el 1.º y 2.º de la tabla del suizo"
        )
    pa, pb = (final["puntos_a"], final["puntos_b"]) if m.entry_a_id == a else (final["puntos_b"], final["puntos_a"])
    m.points_a, m.points_b, m.status, m.played_at = pa, pb, "valid", dia
    db.flush()
    ensure_match_players(db, m)


def importar_torneo(
    db: Session,
    datos: dict,
    fecha: date | None = None,
    sede: str | None = None,
    reemplazar: bool = False,
) -> dict:
    """Crea el torneo, sus jugadores, parejas y todas las partidas. Devuelve un resumen."""
    cfg = datos["competencia"]
    try:
        _validar(datos)
        existente = db.scalar(
            select(Competition).where(Competition.name == cfg["nombre"])
        )
        if existente is not None:
            if not reemplazar:
                raise ImportError_(
                    f"Ya existe un torneo llamado '{cfg['nombre']}' (id {existente.id}). "
                    "No se importó nada. Para volver a cargarlo usa --reemplazar."
                )
            comps.delete_competition(db, existente)

        dia = fecha or date.fromisoformat(cfg["fecha"])
        resumen: dict = {"creados": [], "reutilizados": [], "fecha": dia}

        comp = Competition(
            name=cfg["nombre"],
            kind=cfg.get("tipo", "torneo"),
            format=cfg["formato"],
            final_only_on_tie=bool(cfg.get("final_solo_si_empate")) and cfg["formato"] == "suizo_gran_final",
            location=sede if sede is not None else cfg.get("sede"),
            season=cfg.get("temporada"),
            start_date=dia,
            status="active",
            team_size=2,
            points_per_win=cfg.get("puntos_por_victoria", 1),
            points_per_draw=cfg.get("puntos_por_empate", 0),
            target_points=cfg.get("puntos_objetivo", 100),
            swiss_rounds=cfg["rondas_suizas"],
        )
        db.add(comp)
        db.flush()

        # Jugadores (se crean o se reutilizan)
        jugadores = {clave: _jugador(db, d, resumen) for clave, d in datos["jugadores"].items()}

        # Siembra neutral: orden de aparición en la ronda 1 (mesa 1, lado A, lado B, mesa 2...).
        # Solo importa para desempates extremos (mismos puntos, average y puntos a favor).
        retiradas = {p["clave"] for p in datos["parejas"] if p.get("retirada")}
        aparicion: list[str] = []
        for ronda in datos["rondas"]:
            for a, b, *_ in ronda:
                for k in (a, b):
                    if k not in aparicion:
                        aparicion.append(k)
        semilla = {k: i for i, k in enumerate([k for k in aparicion if k not in retiradas], 1)}

        entradas: dict[str, Entry] = {}
        for p in datos["parejas"]:
            j1, j2 = (jugadores[j] for j in p["jugadores"])
            e = Entry(
                competition_id=comp.id,
                name=f"{j1.name} / {j2.name}"[:160],
                player1_id=j1.id,
                player2_id=j2.id,
                seed=None if p["clave"] in retiradas else semilla.get(p["clave"]),
                withdrawn=p["clave"] in retiradas,
            )
            db.add(e)
            entradas[p["clave"]] = e
        db.flush()

        # Partidas de cada ronda, ya con su resultado
        partidas: list[Match] = []
        for n, ronda in enumerate(datos["rondas"], 1):
            for slot, (a, b, pa, pb) in enumerate(ronda, 1):
                m = Match(
                    competition_id=comp.id, stage="swiss", round=n, slot=slot,
                    entry_a_id=entradas[a].id, entry_b_id=entradas[b].id,
                    status="valid", points_a=pa, points_b=pb,
                    played_at=dia, target_points=comp.target_points,
                )
                db.add(m)
                partidas.append(m)
        db.flush()
        for m in partidas:
            ensure_match_players(db, m)

        # La final 1.º vs 2.º queda creada y pendiente (se llena sola con la tabla del suizo)
        if cfg["formato"] == "suizo_gran_final":
            comps._insert_specs(db, comp, T.build_single_elim(["R:1", "R:2"], third_place=False))
        db.flush()
        comps.refresh(db, comp)
        if datos.get("final"):
            _registrar_final(db, comp, datos["final"], entradas, dia)
            comps.refresh(db, comp)
        recalculate_all(db)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(comp)
    resumen["competencia"] = comp
    resumen["partidas"] = len(partidas)
    return resumen
