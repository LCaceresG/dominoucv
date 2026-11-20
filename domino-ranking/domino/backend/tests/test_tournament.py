"""Pruebas del motor de torneos con simulaciones completas."""
import random
from types import SimpleNamespace

import pytest

from app import tournament as T


def to_matches(specs):
    return [
        SimpleNamespace(
            id=s.key, stage=s.stage, bracket=s.bracket, round=s.round, slot=s.slot,
            src_a=s.src_a, src_b=s.src_b, group=s.group,
            entry_a_id=None, entry_b_id=None, status="pending", points_a=None, points_b=None,
        )
        for s in specs
    ]


def play(m, strong_wins=True, rng=None):
    """Juega una partida: gana el de id menor (más fuerte) o uno al azar."""
    rng = rng or random.Random(m.id)
    a_wins = (m.entry_a_id < m.entry_b_id) if strong_wins else rng.random() < 0.5
    m.points_a, m.points_b = (100, rng.randint(0, 99)) if a_wins else (rng.randint(0, 99), 100)
    m.status = "valid"


def run(matches, seeds, ppw=3, ppd=1, swiss_total=0, strong_wins=True, rng=None):
    for _ in range(1000):
        ctx = T.build_ctx(matches, seeds, ppw, ppd, swiss_total)
        T.sync(matches, ctx)
        ready = [m for m in matches if m.status == "pending" and m.entry_a_id and m.entry_b_id]
        if not ready:
            return
        play(ready[0], strong_wins, rng)
    raise AssertionError("El torneo no termina")


def test_bracket_order():
    assert T.bracket_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]
    assert T.bracket_order(4) == [1, 4, 2, 3]
    assert T.bracket_order(2) == [1, 2]


@pytest.mark.parametrize("n", range(2, 18))
def test_single_elim_strong_wins(n):
    seeds = list(range(1, n + 1))
    ms = to_matches(T.build_single_elim([f"E:{i}" for i in seeds]))
    run(ms, seeds)
    assert T.all_decided(ms)
    pod = T.ko_podium(ms)
    assert pod[0] == (1, 1) and pod[1] == (2, 2)
    if n >= 3:
        assert pod[2] == (3, 3)
    played = [m for m in ms if m.status == "valid"]
    expected = (n - 1) + (1 if n >= 4 else 0)  # + partido por el tercer puesto
    assert len(played) == expected or (n == 3 and len(played) == 2)
    for m in played:
        assert m.entry_a_id != m.entry_b_id


@pytest.mark.parametrize("n", range(4, 18))
def test_double_elim_two_losses(n):
    seeds = list(range(1, n + 1))
    for strong in (True, False):
        rng = random.Random(n)
        ms = to_matches(T.build_double_elim([f"E:{i}" for i in seeds]))
        run(ms, seeds, strong_wins=strong, rng=rng)
        assert T.all_decided(ms)
        pod = T.ko_podium(ms, double=True)
        assert [p for p, _ in pod] == [1, 2, 3]
        assert len({e for _, e in pod}) == 3
        losses = {i: 0 for i in seeds}
        for m in ms:
            if m.status == "valid":
                assert m.entry_a_id != m.entry_b_id
                loser = m.entry_b_id if m.points_a > m.points_b else m.entry_a_id
                losses[loser] += 1
        champion = pod[0][1]
        assert all(l <= 2 for l in losses.values())
        assert all(l == 2 for e, l in losses.items() if e not in (champion, pod[1][1]))
        if strong:
            assert [e for _, e in pod[:2]] == [1, 2]


def test_double_elim_needs_four():
    with pytest.raises(ValueError):
        T.build_double_elim(["E:1", "E:2", "E:3"])


def test_swiss_no_rematch_and_bye_once():
    for n in (6, 7, 8, 9, 10, 11, 12):
        seeds = list(range(1, n + 1))
        rounds = T.default_swiss_rounds(n)
        ms, next_id = [], 1
        rng = random.Random(n)
        for r in range(1, rounds + 1):
            pairs, bye = T.plan_swiss_round(seeds, ms, r)
            for a, b in pairs:
                ms.append(SimpleNamespace(id=next_id, stage="swiss", bracket="", round=r, slot=0, src_a=None, src_b=None,
                                          group=None, entry_a_id=a, entry_b_id=b, status="pending", points_a=None, points_b=None))
                next_id += 1
            if bye is not None:
                ms.append(SimpleNamespace(id=next_id, stage="swiss", bracket="", round=r, slot=0, src_a=None, src_b=None,
                                          group=None, entry_a_id=bye, entry_b_id=None, status="bye", points_a=None, points_b=None))
                next_id += 1
            for m in [x for x in ms if x.round == r and x.status == "pending"]:
                play(m, strong_wins=False, rng=rng)
        real = [frozenset((m.entry_a_id, m.entry_b_id)) for m in ms if m.status != "bye"]
        assert len(real) == len(set(real)), f"revancha con n={n}"
        byes = [m.entry_a_id for m in ms if m.status == "bye"]
        assert len(byes) == len(set(byes))
        # todos juegan (o descansan) en cada ronda
        for r in range(1, rounds + 1):
            ids = [x for m in ms if m.round == r for x in (m.entry_a_id, m.entry_b_id) if x is not None]
            assert sorted(ids) == seeds


def test_swiss_first_round_top_half_vs_bottom_half():
    pairs, bye = T.swiss_pair([1, 2, 3, 4, 5, 6], set(), set(), first_round=True)
    assert pairs == [(1, 4), (2, 5), (3, 6)] and bye is None


def test_groups_then_knockout():
    seeds = list(range(1, 9))
    groups = T.snake_groups(seeds, 2)
    assert groups == [[1, 4, 5, 8], [2, 3, 6, 7]]
    labels = ["A", "B"]
    gspecs = T.build_groups(dict(zip(labels, groups)))
    assert len(gspecs) == 12  # 6 partidas por grupo
    ko = T.build_single_elim(T.group_qualifier_sources(labels, 2))
    off = len(gspecs)
    ms = to_matches(gspecs)
    for s in to_matches(ko):
        s.id += off
        for attr in ("src_a", "src_b"):
            v = getattr(s, attr)
            if v and v[0] in "WL":
                k, i = v.split(":")
                setattr(s, attr, f"{k}:{int(i) + off}")
        ms.append(s)
    for m in ms:  # los grupos conocen a sus participantes desde el inicio
        if m.stage == "group":
            m.entry_a_id, m.entry_b_id = int(m.src_a[2:]), int(m.src_b[2:])
    run(ms, seeds)
    assert T.all_decided(ms)
    assert T.ko_podium(ms) == [(1, 1), (2, 2), (3, 3)]
    first = [m for m in ms if m.stage == "ko" and m.round == 1]
    for m in first:  # nadie se cruza con su propio grupo en la primera ronda
        ga = next(i for i, g in enumerate(groups) if m.entry_a_id in g)
        gb = next(i for i, g in enumerate(groups) if m.entry_b_id in g)
        assert ga != gb


def test_edit_blocked_when_dependents_played():
    seeds = [1, 2, 3, 4]
    ms = to_matches(T.build_single_elim([f"E:{i}" for i in seeds]))
    run(ms, seeds)
    semi = next(m for m in ms if m.bracket == "K" and m.slot == 1)
    assert T.dependents_with_results(semi, ms)  # la final ya se jugó
    final = next(m for m in ms if m.bracket == "F")
    assert not T.dependents_with_results(final, ms)


def test_edit_result_updates_next_match():
    seeds = [1, 2, 3, 4]
    ms = to_matches(T.build_single_elim([f"E:{i}" for i in seeds]))
    ctx = T.build_ctx(ms, seeds)
    T.sync(ms, ctx)
    semi1 = next(m for m in ms if m.bracket == "K" and m.slot == 1)
    play(semi1)
    T.sync(ms, ctx)
    final = next(m for m in ms if m.bracket == "F")
    assert final.entry_a_id == 1
    semi1.points_a, semi1.points_b = 10, 100  # corrección: ganó el 4
    T.sync(ms, ctx)
    assert final.entry_a_id == 4


def test_table_and_ranking():
    res = [T.Result(1, 2, 100, 50), T.Result(2, 3, 80, 80), T.Result(1, 3, 20, 100), T.Result(4, None, bye=True)]
    rows = T.compute_table([1, 2, 3, 4], res)
    assert rows[1]["points"] == 3 and rows[3]["points"] == 4 and rows[2]["points"] == 1 and rows[4]["points"] == 3
    assert rows[2]["draws"] == 1 and rows[1]["pf"] == 120 and rows[1]["pa"] == 150
    ranked = T.rank_rows(rows, [1, 2, 3, 4], tiebreak="buchholz")
    assert ranked[0]["id"] == 3


# ---------------------------------------------------------------- suizo + final 1.º vs 2.º, desempate, retirados

def _swiss_ms(results):
    """results: {(a, b): (pa, pb)}; dos partidas por ronda (4 participantes, todos contra todos)."""
    ms = []
    for i, ((a, b), (pa, pb)) in enumerate(results.items(), 1):
        ms.append(SimpleNamespace(id=i, stage="swiss", bracket="", round=(i - 1) // 2 + 1, slot=0, src_a=None, src_b=None,
                                  group=None, entry_a_id=a, entry_b_id=b, status="valid", points_a=pa, points_b=pb))
    final = T.build_single_elim(["R:1", "R:2"], third_place=False)
    assert len(final) == 1 and final[0].bracket == "F"
    ms.append(SimpleNamespace(id=100, stage="ko", bracket="F", round=1, slot=1, src_a="R:1", src_b="R:2", group=None,
                              entry_a_id=None, entry_b_id=None, status="pending", points_a=None, points_b=None))
    return ms


# 1 y 2 terminan con 2 victorias cada uno (empate por el primer lugar); 1 queda arriba por average (+130 vs +120)
TIE = {(1, 2): (50, 100), (3, 4): (100, 40), (1, 3): (100, 10), (2, 4): (90, 100), (1, 4): (100, 10), (2, 3): (100, 20)}
# igual, pero 1 le gana a 2: 1 tiene 3 victorias y nadie lo alcanza
NO_TIE = {**TIE, (1, 2): (100, 50)}


def test_final_is_played_when_tie_for_first():
    ms = _swiss_ms(TIE)
    ctx = T.build_ctx(ms, [1, 2, 3, 4], 1, 0, 3, "average", final_only_on_tie=True)
    assert ctx.swiss_order[:2] == [1, 2] and ctx.skip_final is False
    T.sync(ms, ctx)
    final = ms[-1]
    assert (final.entry_a_id, final.entry_b_id, final.status) == (1, 2, "pending")
    final.points_a, final.points_b, final.status = 80, 60, "valid"
    assert T.ko_podium(ms) == [(1, 1), (2, 2)]
    assert T.all_decided(ms)


def test_final_is_skipped_without_tie_when_option_on():
    ms = _swiss_ms(NO_TIE)
    ctx = T.build_ctx(ms, [1, 2, 3, 4], 1, 0, 3, "average", final_only_on_tie=True)
    assert ctx.skip_final is True and ctx.swiss_order[0] == 1
    T.sync(ms, ctx)
    final = ms[-1]
    assert final.status == "bye" and final.entry_a_id == 1 and final.entry_b_id is None
    assert T.all_decided(ms)
    assert T.ko_podium(ms) == [(1, 1)]


def test_final_is_always_played_when_option_off():
    ms = _swiss_ms(NO_TIE)
    ctx = T.build_ctx(ms, [1, 2, 3, 4], 1, 0, 3, "average", final_only_on_tie=False)
    T.sync(ms, ctx)
    final = ms[-1]
    assert final.status == "pending" and (final.entry_a_id, final.entry_b_id) == (1, ms[-1].entry_b_id) and final.entry_b_id is not None


def test_final_waits_until_swiss_is_complete():
    ms = _swiss_ms(TIE)
    ms[-2].status = "pending"  # falta un resultado del suizo
    ms[-2].points_a = ms[-2].points_b = None
    ctx = T.build_ctx(ms, [1, 2, 3, 4], 1, 0, 3, "average", final_only_on_tie=True)
    assert ctx.swiss_order is None and ctx.skip_final is False
    T.sync(ms, ctx)
    assert ms[-1].status == "pending" and ms[-1].entry_a_id is None


def test_tiebreak_average_vs_buchholz():
    def rows():
        return {
            1: dict(id=1, points=2, buchholz=5, diff=10, pf=100),
            2: dict(id=2, points=2, buchholz=3, diff=40, pf=120),
        }
    assert [r["id"] for r in T.rank_rows(rows(), [1, 2], tiebreak="average")] == [2, 1]
    assert [r["id"] for r in T.rank_rows(rows(), [1, 2], tiebreak="buchholz")] == [1, 2]


def test_withdrawn_pair_is_left_out_but_still_counts_for_opponents():
    ms = _swiss_ms(TIE)[:-1]
    ms.append(SimpleNamespace(id=50, stage="swiss", bracket="", round=1, slot=0, src_a=None, src_b=None, group=None,
                              entry_a_id=99, entry_b_id=1, status="valid", points_a=30, points_b=77))
    rows = T.swiss_table(ms, [1, 2, 3, 4], 1, 0)  # 99 (retirada) no está en la siembra
    assert {r["id"] for r in rows} == {1, 2, 3, 4}
    one = next(r for r in rows if r["id"] == 1)
    assert one["wins"] == 3 and one["pf"] == 100 + 100 + 50 + 77 and one["pa"] == 10 + 10 + 100 + 30
