"""Startet die komplette App ohne Bildschirm (SDL dummy) und simuliert Schläge."""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from boxautomat.app import run  # noqa: E402
from boxautomat.config import Config  # noqa: E402


def test_app_runs_headless(tmp_path):
    cfg = Config()
    cfg.display.mode = "windowed"
    cfg.display.width, cfg.display.height = 640, 480
    cfg.display.fps = 1000
    cfg.game.count_up_time = 0.05
    cfg.game.result_hold_time = 0.05
    cfg.highscore_file = tmp_path / "hs.json"
    run(cfg, use_serial=False, max_frames=300, sim_hits=[(5, 640), (150, 120)])
    assert (tmp_path / "hs.json").exists()
