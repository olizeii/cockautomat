"""Zustandsautomat des Boxautomaten – ohne Grafik, damit er testbar ist.

    IDLE ──Schlag──▶ COUNTING ──fertig──▶ RESULT ──Zeit um──▶ IDLE
      ▲                                                       │
      └──────────────── Schalter aus ◀── SCOREBOARD ◀─ Schalter an (jederzeit)
"""
from __future__ import annotations

import math
from enum import Enum, auto

from .config import GameConfig
from .highscores import Highscores


class State(Enum):
    IDLE = auto()
    COUNTING = auto()
    RESULT = auto()
    SCOREBOARD = auto()


class Game:
    def __init__(self, cfg: GameConfig, highscores: Highscores):
        self.cfg = cfg
        self.highscores = highscores
        self.state = State.IDLE
        self.state_since = 0.0
        self.score = 0
        self.last_hit = -math.inf
        self.rank: int | None = None
        self.is_record = False
        self.scoreboard_switch = False
        # wird True gesetzt, wenn die GUI beim Zurück-in-IDLE das Ready-Overlay starten soll
        self.just_returned_to_idle = False

    # -- Eingaben ------------------------------------------------------------
    def on_hit(self, score: int, now: float) -> bool:
        """Ein Schlag wurde gemeldet. Gibt True zurück, wenn er gewertet wurde."""
        if self.state in (State.COUNTING, State.SCOREBOARD):
            return False
        if score < self.cfg.min_score:
            return False
        if now - self.last_hit < self.cfg.min_hit_interval:
            return False        # Nachschwingen des letzten Schlags
        self.last_hit = now
        score = min(int(score), self.cfg.max_score)
        self.score = score
        self.is_record = score > self.highscores.best
        self.rank = self.highscores.add(score)
        self._enter(State.COUNTING, now)
        return True

    def on_switch(self, on: bool, now: float) -> None:
        self.scoreboard_switch = on
        if on and self.state != State.SCOREBOARD:
            self._enter(State.SCOREBOARD, now)
        elif not on and self.state == State.SCOREBOARD:
            self._enter(State.IDLE, now)

    # -- Zeitverlauf ---------------------------------------------------------
    def update(self, now: float) -> None:
        elapsed = now - self.state_since
        if self.state == State.COUNTING and elapsed >= self.cfg.count_up_time:
            self._enter(State.RESULT, now)
        elif self.state == State.RESULT and elapsed >= self.cfg.result_hold_time:
            self._enter(State.IDLE, now)
            self.just_returned_to_idle = True

    def displayed_score(self, now: float) -> int:
        """Score für die Anzeige: zählt in COUNTING mit Ease-Out hoch."""
        if self.state == State.COUNTING:
            t = min(1.0, (now - self.state_since) / max(self.cfg.count_up_time, 1e-6))
            eased = 1 - math.pow(1 - t, 3)
            return int(round(self.score * eased))
        return self.score

    def progress(self, now: float) -> float:
        """0..1 innerhalb des aktuellen Zustands (für Animationen)."""
        dur = {State.COUNTING: self.cfg.count_up_time,
               State.RESULT: self.cfg.result_hold_time}.get(self.state)
        if not dur:
            return 0.0
        return min(1.0, (now - self.state_since) / dur)

    def _enter(self, state: State, now: float) -> None:
        self.state = state
        self.state_since = now
