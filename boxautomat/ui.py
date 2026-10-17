"""Alles, was gezeichnet wird. Größen skalieren mit der Bildschirmhöhe."""
from __future__ import annotations

import logging
import math
from pathlib import Path

import pygame

from .game import Game, State
from .highscores import Highscores

log = logging.getLogger(__name__)

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 30, 20)
RED_GHOST = (60, 6, 4)
YELLOW = (255, 220, 0)
ORANGE = (255, 150, 0)
GREEN = (40, 255, 80)
GREY = (140, 140, 140)


def _font(path: Path, size: int, fallback: str = "dejavusans", bold: bool = True) -> pygame.font.Font:
    try:
        return pygame.font.Font(str(path), size)
    except (OSError, FileNotFoundError):
        log.warning("Schrift %s fehlt – nutze Systemschrift", path)
        return pygame.font.SysFont(fallback, size, bold=bold)


class Renderer:
    def __init__(self, size: tuple[int, int], font_digital: Path, font_arcade: Path,
                 max_score: int):
        self.w, self.h = size
        self.max_score = max_score
        self.digits = len(str(max_score))
        u = self.h / 100          # 1 % der Bildschirmhöhe

        # Riesige 7-Segment-Zahl: so groß, dass sie in 80 % der Breite passt
        size_px = int(38 * u)
        while size_px > 20:
            f = _font(font_digital, size_px)
            if f.size("8" * self.digits)[0] <= self.w * 0.8:
                break
            size_px = int(size_px * 0.92)
        self.f_score = f
        self.f_big = _font(font_arcade, int(9 * u))
        self.f_mid = _font(font_arcade, int(6 * u))
        self.f_small = _font(font_arcade, int(4.2 * u))
        self.f_hud = _font(font_digital, int(3.2 * u))
        self.u = u

        self._dim = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        self._dim.fill((0, 0, 0, 185))
        self._flash = pygame.Surface((self.w, self.h))
        self._flash.fill(WHITE)
        self._frozen: pygame.Surface | None = None

    # -- Hilfen --------------------------------------------------------------
    def _blit_center(self, screen, surf, cx, cy):
        screen.blit(surf, surf.get_rect(center=(int(cx), int(cy))))

    def _text(self, screen, font, text, color, cx, cy, shadow=True):
        if shadow:
            sh = font.render(text, True, BLACK)
            off = max(2, int(self.u * 0.4))
            self._blit_center(screen, sh, cx + off, cy + off)
        self._blit_center(screen, font.render(text, True, color), cx, cy)

    def freeze(self, screen: pygame.Surface, black: bool) -> None:
        """Aktuelles Videobild als gedimmten Hintergrund für das Ergebnis merken."""
        if black:
            self._frozen = None
            return
        self._frozen = screen.copy()
        self._frozen.blit(self._dim, (0, 0))

    # -- Szenen --------------------------------------------------------------
    def draw_idle_hud(self, screen, highscores: Highscores, connected: bool, now: float):
        u = self.u
        if highscores.best:
            self._text(screen, self.f_small, f"HIGHSCORE  {highscores.best}", YELLOW,
                       self.w / 2, self.h - 5 * u)
        if not connected:
            # blinkender Hinweis, falls der Arduino fehlt
            if int(now * 2) % 2 == 0:
                pygame.draw.circle(screen, RED, (int(3 * u), int(3.5 * u)), int(1.2 * u))
            label = self.f_small.render("NO SENSOR", True, RED)
            screen.blit(label, label.get_rect(midleft=(int(5.5 * u), int(3.5 * u))))

    def draw_result(self, screen, game: Game, now: float):
        u = self.u
        if self._frozen is not None:
            screen.blit(self._frozen, (0, 0))
        else:
            screen.fill(BLACK)

        value = game.displayed_score(now)
        counting = game.state == State.COUNTING
        cy = self.h * 0.45

        # Kurzer Blitz direkt nach dem Schlag
        since = now - game.state_since
        if counting and since < 0.12:
            self._flash.set_alpha(int(255 * (1 - since / 0.12)))
            screen.blit(self._flash, (0, 0))

        # leichtes "Zittern" während des Hochzählens
        shake = 0
        if counting:
            shake = int(math.sin(now * 90) * u * 0.6 * (1 - game.progress(now)))

        ghost = self.f_score.render("8" * self.digits, True, RED_GHOST)
        num = self.f_score.render(str(value).rjust(self.digits, "!"), True, RED)
        # DSEG: "!" ist eine leere Stelle in Ziffernbreite -> rechtsbündig wie echte Anzeige
        self._blit_center(screen, ghost, self.w / 2, cy)
        self._blit_center(screen, num, self.w / 2 + shake, cy)

        # Kraftbalken
        bar_w, bar_h = self.w * 0.7, 3.2 * u
        x0, y0 = (self.w - bar_w) / 2, self.h * 0.72
        frac = min(1.0, value / max(self.max_score, 1))
        pygame.draw.rect(screen, (40, 40, 40), (x0, y0, bar_w, bar_h), border_radius=int(u))
        if frac > 0:
            fill = pygame.Rect(x0, y0, bar_w * frac, bar_h)
            color = GREEN if frac < 0.5 else YELLOW if frac < 0.8 else RED
            pygame.draw.rect(screen, color, fill, border_radius=int(u))
        pygame.draw.rect(screen, GREY, (x0, y0, bar_w, bar_h), width=2, border_radius=int(u))

        self._text(screen, self.f_mid, "POWER", WHITE, self.w / 2, self.h * 0.1)

        if not counting:
            if game.is_record and int(now * 3) % 2 == 0:
                self._text(screen, self.f_big, "NEUER REKORD", YELLOW, self.w / 2, self.h * 0.87)
            elif game.rank and not game.is_record:
                self._text(screen, self.f_mid, f"PLATZ  {game.rank}", ORANGE, self.w / 2, self.h * 0.87)

    def draw_scoreboard(self, screen, highscores: Highscores):
        u = self.u
        screen.fill(BLACK)
        self._text(screen, self.f_big, "TOTAL SCORE RANKING", GREEN, self.w / 2, 9 * u, shadow=False)

        cols = (self.w * 0.25, self.w * 0.5, self.w * 0.75)
        top = 22 * u
        for x, label in zip(cols, ("RANK", "SCORE", "DATE")):
            self._text(screen, self.f_small, label, YELLOW, x, top, shadow=False)

        row_h = 7 * u
        entries = highscores.entries or []
        for i in range(highscores.size):
            y = top + (i + 1.3) * row_h
            color = RED if i == 0 else ORANGE if i < 3 else WHITE
            e = entries[i] if i < len(entries) else None
            score = str(e["score"]) if e else "---"
            date = e["date"] if e else "---"
            self._text(screen, self.f_small, f"{i + 1}", color, cols[0], y, shadow=False)
            self._text(screen, self.f_hud, score, color, cols[1], y, shadow=False)
            self._text(screen, self.f_hud, date, color, cols[2], y, shadow=False)
