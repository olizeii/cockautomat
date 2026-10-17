import json

from boxautomat.config import GameConfig
from boxautomat.game import Game, State
from boxautomat.highscores import Highscores


def make_game(tmp_path, **kw):
    cfg = GameConfig(count_up_time=1.0, result_hold_time=5.0, **kw)
    return Game(cfg, Highscores(tmp_path / "hs.json", 10))


def test_full_cycle(tmp_path):
    g = make_game(tmp_path)
    assert g.state == State.IDLE
    assert g.on_hit(500, now=0.0)
    assert g.state == State.COUNTING
    assert 0 < g.displayed_score(0.5) < 500
    g.update(1.0)
    assert g.state == State.RESULT
    assert g.displayed_score(1.0) == 500
    g.update(5.9)
    assert g.state == State.RESULT
    g.update(6.1)
    assert g.state == State.IDLE
    assert g.just_returned_to_idle


def test_hits_ignored_while_counting(tmp_path):
    g = make_game(tmp_path)
    g.on_hit(300, 0.0)
    assert not g.on_hit(900, 0.2)
    assert g.score == 300


def test_new_hit_during_result_restarts(tmp_path):
    g = make_game(tmp_path)
    g.on_hit(300, 0.0)
    g.update(1.5)
    assert g.state == State.RESULT
    assert g.on_hit(700, 2.0)
    assert g.state == State.COUNTING and g.score == 700


def test_scoreboard_switch(tmp_path):
    g = make_game(tmp_path)
    g.on_switch(True, 0.0)
    assert g.state == State.SCOREBOARD
    assert not g.on_hit(500, 0.1)
    g.on_switch(False, 0.2)
    assert g.state == State.IDLE


def test_score_clamped_and_min(tmp_path):
    g = make_game(tmp_path, min_score=5)
    assert not g.on_hit(3, 0.0)
    assert g.on_hit(5000, 0.0)
    assert g.score == 999


def test_record_and_rank(tmp_path):
    g = make_game(tmp_path)
    g.on_hit(400, 0.0)
    assert g.is_record and g.rank == 1
    g.update(10.0); g.update(20.0)
    g.on_hit(200, 30.0)
    assert not g.is_record and g.rank == 2


def test_highscores_persist_and_legacy_file(tmp_path):
    p = tmp_path / "hs.json"
    p.write_text(json.dumps([{"score": 989, "date": "07.02.2026"},
                             {"score": 0, "date": "---"},
                             {"score": 766, "date": "10.01.2026"}]))
    hs = Highscores(p, 3)
    assert [e["score"] for e in hs.entries] == [989, 766]
    assert hs.add(800) == 2
    assert hs.add(10) is None  # Liste hat nur 3 Plätze
    hs2 = Highscores(p, 3)
    assert [e["score"] for e in hs2.entries][:2] == [989, 800]
    assert hs2.rank_for(1) is None


def test_corrupt_highscore_file(tmp_path):
    p = tmp_path / "hs.json"
    p.write_text("{kaputt")
    assert Highscores(p).entries == []
