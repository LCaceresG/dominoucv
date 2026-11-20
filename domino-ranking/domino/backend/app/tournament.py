"""Motor de torneos (puro: sin base de datos, fácil de probar).

Idea central: todo torneo es un conjunto de partidas donde cada lado se define
por una "fuente" (src). Así se describen todas las modalidades con las mismas piezas:

    "E:12"      entrada (pareja o jugador) número 12
    "W:7"       ganador de la partida 7
    "L:7"       perdedor de la partida 7
    "R:3"       puesto 3 de la fase suiza
    "G:A:1"     puesto 1 del grupo A
    "BYE"       lado vacío (pase libre)

`sync` recorre las partidas y rellena quién juega contra quién según los
resultados ya registrados. Si se corrige un resultado, `sync` vuelve a calcular
las partidas siguientes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

BYE = -1  # marca de "no hay rival" (distinta de None = "aún no se sabe")
DECIDED = ("valid", "bye")


@dataclass
class Spec:
    """Descripción de una partida a crear. `key` es local a cada llamada al constructor."""

    key: int
    stage: str  # 'swiss' | 'group' | 'ko'
    bracket: str  # '' | 'K' (eliminación) | 'W' (ganadores) | 'L' (perdedores) | 'F' (final) | 'T' (tercer puesto)
    round: int
    slot: int
    src_a: str | None = None
    src_b: str | None = None
    group: str | None = None


@dataclass
class Result:
    a: int
    b: int | None
    pa: int | None = None
    pb: int | None = None
    bye: bool = False


# ---------------------------------------------------------------- utilidades

def next_pow2(n: int) -> int:
    return 1 if n <= 1 else 1 << (n - 1).bit_length()


def bracket_order(size: int) -> list[int]:
    """Orden estándar de siembra: [1, 8, 4, 5, 2, 7, 3, 6] para 8. Los mejores se cruzan al final."""
    order = [1, 2]
    n = 2
    while n < size:
        n *= 2
        order = [x for s in order for x in (s, n + 1 - s)]
    return order


def round_robin(ids: list[int]) -> list[list[tuple[int, int]]]:
    """Todos contra todos (método del círculo). Devuelve una lista de rondas."""
    ids = list(ids)
    if len(ids) % 2:
        ids.append(None)  # descanso
    n = len(ids)
    rounds = []
    for _ in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = ids[i], ids[n - 1 - i]
            if a is not None and b is not None:
                pairs.append((a, b))
        rounds.append(pairs)
        ids = [ids[0], ids[-1]] + ids[1:-1]
    return rounds


def snake_groups(ids_by_seed: list[int], n_groups: int) -> list[list[int]]:
    """Reparte en grupos en serpiente: 1,2,3,4 / 8,7,6,5 / ... para equilibrar los grupos."""
    groups: list[list[int]] = [[] for _ in range(n_groups)]
    for i, e in enumerate(ids_by_seed):
        row, col = divmod(i, n_groups)
        groups[col if row % 2 == 0 else n_groups - 1 - col].append(e)
    return groups


def group_label(i: int) -> str:
    return chr(ord("A") + i)


def default_swiss_rounds(n: int) -> int:
    if n < 2:
        return 0
    return min(max(3, math.ceil(math.log2(n))), n - 1)


# ---------------------------------------------------------------- constructores

def build_single_elim(sources: list[str], third_place: bool = True) -> list[Spec]:
    """Eliminación directa. `sources` en orden de siembra (el primero es el mejor)."""
    n = len(sources)
    if n < 2:
        raise ValueError("Se necesitan al menos 2 participantes")
    size = next_pow2(n)
    rounds = size.bit_length() - 1
    order = bracket_order(size)

    def src_for(seed: int) -> str:
        return sources[seed - 1] if seed <= n else "BYE"

    specs: list[Spec] = []
    prev: dict[tuple[int, int], int] = {}
    key = 0
    for r in range(1, rounds + 1):
        for p in range(1, size // 2**r + 1):
            if r == 1:
                a, b = src_for(order[2 * p - 2]), src_for(order[2 * p - 1])
            else:
                a, b = f"W:{prev[(r - 1, 2 * p - 1)]}", f"W:{prev[(r - 1, 2 * p)]}"
            specs.append(Spec(key, "ko", "F" if r == rounds else "K", r, p, a, b))
            prev[(r, p)] = key
            key += 1
    if third_place and size >= 4:
        specs.append(
            Spec(key, "ko", "T", rounds, 2, f"L:{prev[(rounds - 1, 1)]}", f"L:{prev[(rounds - 1, 2)]}")
        )
    return specs


def build_double_elim(sources: list[str]) -> list[Spec]:
    """Doble eliminación: llave de ganadores, llave de perdedores y gran final (sin revancha)."""
    n = len(sources)
    if n < 4:
        raise ValueError("La doble eliminación necesita al menos 4 participantes")
    size = next_pow2(n)
    k = size.bit_length() - 1
    order = bracket_order(size)

    def src_for(seed: int) -> str:
        return sources[seed - 1] if seed <= n else "BYE"

    specs: list[Spec] = []
    w: dict[tuple[int, int], int] = {}
    lb: dict[tuple[int, int], int] = {}
    key = 0

    def add(bracket, r, p, a, b, store):
        nonlocal key
        specs.append(Spec(key, "ko", bracket, r, p, a, b))
        store[(r, p)] = key
        key += 1

    for r in range(1, k + 1):
        for p in range(1, size // 2**r + 1):
            if r == 1:
                a, b = src_for(order[2 * p - 2]), src_for(order[2 * p - 1])
            else:
                a, b = f"W:{w[(r - 1, 2 * p - 1)]}", f"W:{w[(r - 1, 2 * p)]}"
            add("W", r, p, a, b, w)

    for p in range(1, size // 4 + 1):
        add("L", 1, p, f"L:{w[(1, 2 * p - 1)]}", f"L:{w[(1, 2 * p)]}", lb)
    for m in range(1, k):
        for p in range(1, size // 2 ** (m + 1) + 1):  # ronda par: entran los perdedores de ganadores
            add("L", 2 * m, p, f"W:{lb[(2 * m - 1, p)]}", f"L:{w[(m + 1, p)]}", lb)
        if m <= k - 2:  # ronda impar: solo juegan los que vienen de perdedores
            for p in range(1, size // 2 ** (m + 2) + 1):
                add("L", 2 * m + 1, p, f"W:{lb[(2 * m, 2 * p - 1)]}", f"W:{lb[(2 * m, 2 * p)]}", lb)

    final_store: dict = {}
    add("F", k + 1, 1, f"W:{w[(k, 1)]}", f"W:{lb[(2 * k - 2, 1)]}", final_store)
    return specs


def build_groups(groups: dict[str, list[int]]) -> list[Spec]:
    """Fase de grupos: todos contra todos dentro de cada grupo."""
    specs: list[Spec] = []
    key = 0
    for label, ids in groups.items():
        for r, pairs in enumerate(round_robin(ids), 1):
            for p, (a, b) in enumerate(pairs, 1):
                specs.append(Spec(key, "group", "", r, p, f"E:{a}", f"E:{b}", label))
                key += 1
    return specs


def group_qualifier_sources(labels: list[str], per_group: int) -> list[str]:
    """Orden de siembra: primeros de grupo, luego segundos... (se cruzan A1-B2, B1-A2)."""
    return [f"G:{label}:{rank}" for rank in range(1, per_group + 1) for label in labels]


# ---------------------------------------------------------------- suizo

def swiss_pair(order, played, had_bye, first_round=False):
    """Empareja una ronda suiza.

    order:    ids ordenados del mejor al peor (por puntos; en la ronda 1, por siembra).
    played:   conjunto de frozenset({a, b}) con los cruces ya jugados.
    had_bye:  ids que ya tuvieron pase libre.
    Devuelve (pares, id_con_bye_o_None). Evita repetir rivales si es posible.
    """
    ids = list(order)
    bye = None
    if len(ids) % 2:
        for cand in reversed(ids):  # el peor clasificado que no haya descansado
            if cand not in had_bye:
                bye = cand
                break
        else:
            bye = ids[-1]
        ids.remove(bye)

    if first_round:
        h = len(ids) // 2
        return [(ids[i], ids[i + h]) for i in range(h)], bye

    steps = [0]

    def solve(rest, allow_rematch):
        if not rest:
            return []
        steps[0] += 1
        if steps[0] > 200_000:
            return None
        a = rest[0]
        for j in range(1, len(rest)):
            b = rest[j]
            if not allow_rematch and frozenset((a, b)) in played:
                continue
            tail = solve(rest[1:j] + rest[j + 1 :], allow_rematch)
            if tail is not None:
                return [(a, b)] + tail
        return None

    pairs = solve(ids, False)
    if pairs is None:
        steps[0] = 0
        pairs = solve(ids, True)
    if pairs is None:  # último recurso: en orden
        pairs = [(ids[i], ids[i + 1]) for i in range(0, len(ids), 2)]
    return pairs, bye


# ---------------------------------------------------------------- tablas

def compute_table(ids, results: list[Result], ppw=3, ppd=1) -> dict[int, dict]:
    rows = {
        i: dict(id=i, played=0, wins=0, draws=0, losses=0, pf=0, pa=0, points=0, byes=0, opps=[])
        for i in ids
    }
    for r in results:
        if r.bye:
            row = rows.get(r.a)
            if row:
                row["points"] += ppw
                row["byes"] += 1
            continue
        for me, opp, pm, po in ((r.a, r.b, r.pa, r.pb), (r.b, r.a, r.pb, r.pa)):
            row = rows.get(me)
            if row is None:
                continue
            row["played"] += 1
            row["pf"] += pm
            row["pa"] += po
            row["opps"].append(opp)
            if pm > po:
                row["wins"] += 1
                row["points"] += ppw
            elif pm == po:
                row["draws"] += 1
                row["points"] += ppd
            else:
                row["losses"] += 1
    for row in rows.values():
        row["diff"] = row["pf"] - row["pa"]
        row["buchholz"] = sum(rows[o]["points"] for o in row["opps"] if o in rows)
    return rows


def rank_rows(rows: dict[int, dict], seed_order: list[int], tiebreak: str = "average") -> list[dict]:
    """Ordena la tabla.

    tiebreak="average":  puntos, average (puntos a favor - en contra), puntos a favor.
    tiebreak="buchholz": puntos, Buchholz (suma de puntos de los rivales), average, puntos a favor.
    Si todo empata, manda el orden de siembra.
    """
    pos = {e: i for i, e in enumerate(seed_order)}
    if tiebreak == "buchholz":
        key = lambda r: (-r["points"], -r["buchholz"], -r["diff"], -r["pf"], pos.get(r["id"], 10**9))
    else:
        key = lambda r: (-r["points"], -r["diff"], -r["pf"], pos.get(r["id"], 10**9))
    ordered = sorted(rows.values(), key=key)
    for i, r in enumerate(ordered, 1):
        r["position"] = i
    return ordered


# ---------------------------------------------------------------- motor de fuentes

class Ctx:
    """Clasificaciones de fases previas (None mientras la fase no haya terminado)."""

    def __init__(self, swiss_order: list[int] | None = None, group_orders: dict | None = None):
        self.swiss_order = swiss_order
        self.group_orders = group_orders or {}
        # True cuando la final 1.º vs 2.º solo se juega si hay empate por el primer lugar
        # y ese empate no existe: la final se omite y el 1.º es campeón directo.
        self.skip_final = False


def outcome(m):
    """(ganador, perdedor) de una partida decidida; None si falta resultado."""
    if m.status == "valid":
        if m.points_a > m.points_b:
            return (m.entry_a_id, m.entry_b_id)
        if m.points_b > m.points_a:
            return (m.entry_b_id, m.entry_a_id)
        return None
    if m.status == "bye":
        real = [x for x in (m.entry_a_id, m.entry_b_id) if x is not None]
        return (real[0], BYE) if real else (BYE, BYE)
    return None


def resolve(src, by_id, ctx: Ctx):
    """Entrada concreta de una fuente: id, BYE, o None si todavía no se sabe."""
    if src is None:
        return None
    kind, *rest = src.split(":")
    if kind == "BYE":
        return BYE
    if kind == "E":
        return int(rest[0])
    if kind in ("W", "L"):
        o = outcome(by_id[int(rest[0])])
        if o is None:
            return None
        return o[0] if kind == "W" else o[1]
    if kind == "R":
        n = int(rest[0])
        order = ctx.swiss_order
        return order[n - 1] if order and n <= len(order) else None
    if kind == "G":
        n = int(rest[1])
        order = ctx.group_orders.get(rest[0])
        return order[n - 1] if order and n <= len(order) else None
    raise ValueError(f"Fuente desconocida: {src}")


def sync(matches, ctx: Ctx) -> None:
    """Rellena los lados de cada partida según resultados previos y marca los pases libres."""
    by_id = {m.id: m for m in matches}
    for m in sorted(matches, key=lambda x: x.id):
        if m.src_a is None and m.src_b is None:
            continue  # partidas suizas o libres: sus lados no dependen de otras
        if m.status == "valid":
            continue
        if ctx.skip_final and m.stage == "ko" and m.bracket == "F":
            # Sin empate por el primer lugar no hace falta final: el 1.º del suizo es campeón.
            a = resolve(m.src_a, by_id, ctx)
            m.entry_a_id = a if a not in (None, BYE) else None
            m.entry_b_id = None
            m.status = "bye" if m.entry_a_id is not None else "pending"
            continue
        a = resolve(m.src_a, by_id, ctx)
        b = resolve(m.src_b, by_id, ctx)
        m.entry_a_id = a if a not in (None, BYE) else None
        m.entry_b_id = b if b not in (None, BYE) else None
        if a is None or b is None:
            m.status = "pending"
        elif a == BYE or b == BYE:
            m.status = "bye"
        else:
            m.status = "pending"


def dependents_with_results(m, matches) -> list:
    """Partidas ya jugadas que dependen de `m`. Si hay alguna, no se puede cambiar `m`."""
    children: dict[int, list] = {}
    for x in matches:
        for s in (x.src_a, x.src_b):
            if s and s[0] in "WL" and s[1] == ":":
                children.setdefault(int(s.split(":")[1]), []).append(x)
    found, seen, stack = [], set(), [m.id]
    while stack:
        for x in children.get(stack.pop(), []):
            if x.id in seen:
                continue
            seen.add(x.id)
            if x.status == "valid":
                found.append(x)
            stack.append(x.id)
    if m.stage in ("swiss", "group"):
        found += [x for x in matches if x.stage == "ko" and x.status == "valid"]
        if m.stage == "swiss":
            found += [x for x in matches if x.stage == "swiss" and x.round > m.round]
    return found


def ko_podium(matches, double: bool = False) -> list[tuple[int, int]]:
    """[(puesto, entrada)] de las llaves de eliminación; solo lo ya decidido."""
    out: list[tuple[int, int]] = []
    finals = [m for m in matches if m.stage == "ko" and m.bracket == "F"]
    if finals:
        o = outcome(finals[0])
        if o:
            out += [(1, o[0]), (2, o[1])]
    if double:
        losers = [m for m in matches if m.stage == "ko" and m.bracket == "L"]
        if losers and out:
            last = max(losers, key=lambda m: (m.round, m.slot))
            o = outcome(last)
            if o:
                out.append((3, o[1]))
    else:
        third = [m for m in matches if m.stage == "ko" and m.bracket == "T"]
        if third:
            o = outcome(third[0])
            if o:
                out.append((3, o[0]))
    return [(p, e) for p, e in out if e not in (None, BYE)]


def all_decided(matches) -> bool:
    return bool(matches) and all(m.status in DECIDED for m in matches)


# ---------------------------------------------------------------- clasificaciones a partir de partidas

def results_of(matches) -> list[Result]:
    out: list[Result] = []
    for m in matches:
        if m.status == "valid":
            out.append(Result(m.entry_a_id, m.entry_b_id, m.points_a, m.points_b))
        elif m.status == "bye":
            real = [x for x in (m.entry_a_id, m.entry_b_id) if x is not None]
            if real:
                out.append(Result(real[0], None, bye=True))
    return out


def swiss_table(matches, seed_order, ppw=3, ppd=1, tiebreak="average") -> list[dict]:
    """Tabla suiza. Solo salen las entradas de `seed_order`: una pareja retirada se deja fuera de la
    lista, pero sus partidas ya jugadas siguen contando para sus rivales."""
    sw = [m for m in matches if m.stage == "swiss"]
    return rank_rows(compute_table(seed_order, results_of(sw), ppw, ppd), seed_order, tiebreak)


def group_table(matches, label, seed_order, ppw=3, ppd=1) -> list[dict]:
    gm = [m for m in matches if m.stage == "group" and m.group == label]
    ids = sorted({x for m in gm for x in (m.entry_a_id, m.entry_b_id) if x is not None})
    return rank_rows(compute_table(ids, results_of(gm), ppw, ppd), seed_order, "average")


def final_win_bonus(matches, rows: list[dict], ppw: int = 1) -> list[dict]:
    """Suma la victoria de la final 1.º vs 2.º al campeón, solo para mostrar la tabla.

    La final NO suma puntos a favor ni en contra (no toca el average) y el perdedor no recibe derrota:
    por ejemplo, un campeón con 4-1 en el suizo queda 5-1 y el subcampeón sigue en 4-1.
    """
    finals = [m for m in matches if m.stage == "ko" and m.bracket == "F" and m.status == "valid"]
    played = outcome(finals[0]) if finals else None
    if not played:
        return rows
    champion = next((r for r in rows if r["id"] == played[0]), None)
    if champion is None:
        return rows
    champion["wins"] += 1
    champion["played"] += 1
    champion["points"] += ppw
    return rank_rows({r["id"]: r for r in rows}, [r["id"] for r in rows])


def has_tie_for_first(rows: list[dict]) -> bool:
    """¿Los dos primeros de la tabla tienen los mismos puntos? (el average no cuenta como desempate aquí)"""
    return len(rows) >= 2 and rows[0]["points"] == rows[1]["points"]


def build_ctx(matches, seed_order, ppw=3, ppd=1, swiss_total=0, tiebreak="average", final_only_on_tie=False) -> Ctx:
    """Clasificaciones de las fases suiza y de grupos, solo cuando ya terminaron."""
    ctx = Ctx()
    sw = [m for m in matches if m.stage == "swiss"]
    if sw and swiss_total and max(m.round for m in sw) >= swiss_total and all_decided(sw):
        rows = swiss_table(matches, seed_order, ppw, ppd, tiebreak)
        ctx.swiss_order = [r["id"] for r in rows]
        ctx.skip_final = bool(final_only_on_tie) and not has_tie_for_first(rows)
    labels = sorted({m.group for m in matches if m.stage == "group"})
    for label in labels:
        gm = [m for m in matches if m.stage == "group" and m.group == label]
        ctx.group_orders[label] = (
            [r["id"] for r in group_table(matches, label, seed_order, ppw, ppd)] if all_decided(gm) else None
        )
    return ctx


def plan_swiss_round(seed_order, matches, round_no, ppw=3, ppd=1, tiebreak="average"):
    """(pares, bye) de la ronda `round_no`, según resultados y cruces anteriores."""
    sw = [m for m in matches if m.stage == "swiss"]
    played = {frozenset((m.entry_a_id, m.entry_b_id)) for m in sw if m.status != "bye"}
    had_bye = {x for m in sw if m.status == "bye" for x in (m.entry_a_id, m.entry_b_id) if x is not None}
    if round_no == 1:
        return swiss_pair(seed_order, played, had_bye, first_round=True)
    order = [r["id"] for r in swiss_table(matches, seed_order, ppw, ppd, tiebreak)]
    return swiss_pair(order, played, had_bye)
