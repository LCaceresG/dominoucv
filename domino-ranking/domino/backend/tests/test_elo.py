import math

from app.elo import Participant, compute_deltas, expected_score, k_factor, margin_multiplier


def test_expected_score_equal_ratings_is_half():
    assert math.isclose(expected_score(1000, 1000), 0.5)


def test_expected_score_is_symmetric():
    assert math.isclose(expected_score(1200, 1000) + expected_score(1000, 1200), 1.0)


def test_equal_teams_winner_gains_loser_loses():
    a = [Participant(1000, 0), Participant(1000, 0)]
    b = [Participant(1000, 0), Participant(1000, 0)]
    da, db = compute_deltas(a, b, 100, 100 - 1)
    assert all(d > 0 for d in da)
    assert all(d < 0 for d in db)
    assert math.isclose(sum(da) + sum(db), 0, abs_tol=0.05)


def test_upset_pays_more_than_expected_win():
    strong = [Participant(1300, 30)]
    weak = [Participant(1000, 30)]
    upset_gain, _ = compute_deltas(weak, strong, 100, 50)
    expected_gain, _ = compute_deltas(strong, weak, 100, 50)
    assert upset_gain[0] > expected_gain[0]


def test_new_players_move_faster():
    assert k_factor(0) > k_factor(30) > k_factor(100)


def test_blowout_counts_more_than_close_win():
    assert margin_multiplier(100, 10, 100) > margin_multiplier(100, 95, 100) >= 1.0


def test_draw_between_equals_changes_nothing():
    da, db = compute_deltas([Participant(1000, 0)], [Participant(1000, 0)], 50, 50)
    assert da == [0.0] and db == [0.0]


def test_draw_costs_the_favorite():
    da, db = compute_deltas([Participant(1300, 30)], [Participant(1000, 30)], 70, 70)
    assert da[0] < 0 < db[0]
