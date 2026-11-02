"""Alles, was gezeichnet wird – im Retro-Arcade-Look.

Größen skalieren mit der Bildschirmhöhe (u = 1 % davon), es gibt keine feste Auflösung.
Texte werden einmal gerendert und zwischengespeichert, damit auch schwache Laptops
60 fps schaffen.
"""
from __future__ import annotations

import colorsys
import logging
import math
import random
from pathlib import Path

import numpy as np
import pygame

from .calibration import ABORT_SECONDS, DONE_SECONDS, LIVE_SECONDS, Calibration
from .game import Game, State
from .highscores import Highscores

log = logging.getLogger(__name__)

# -- Farben ---------------------------------------------------------------------
BG = (6, 7, 20)             # Navy wie der Rand des Idle-Videos
INK = (12, 6, 22)           # Umrandung der Schriften
WHITE = (255, 255, 255)
GREY = (130, 130, 150)
RED = (255, 36, 24)
RED_GHOST = (46, 8, 10)
AMBER = (255, 184, 0)
CYAN = (0, 224, 255)
MAGENTA = (255, 50, 190)
GREEN = (60, 255, 100)

# Farbverläufe für Schriften (oben → unten)
FIRE = ((255, 250, 150), (255, 176, 0), (255, 48, 16))
ICE = ((230, 255, 255), (0, 220, 255), (60, 80, 255))
TOXIC = ((230, 255, 180), (60, 255, 100), (0, 150, 90))
SILVER = ((255, 255, 255), (200, 214, 235), (110, 120, 150))
BRONZE = ((255, 220, 170), (230, 130, 55), (140, 60, 20))

# Bewertung nach dem Schlag: (ab Anteil vom Maximum, Text, Farbverlauf)
RATINGS = (
    (0.95, "KNOCKOUT", FIRE),
    (0.80, "MONSTERSCHLAG", FIRE),
    (0.60, "STARK", TOXIC),
    (0.40, "ORDENTLICH", TOXIC),
    (0.20, "GANZ NETT", ICE),
    (0.00, "STREICHELEINHEIT", ICE),
)


# Kalibrier-Bildschirm
CAL_STEPS = (("REST", "RUHE"), ("LIGHT", "LEICHT"), ("HARD", "HART"))
CAL_REASONS = {"first": "ERSTE EINRICHTUNG", "switch": "PER SCHALTER", "command": "PER TASTE"}
CAL_REJECTS = {"MOVING": "BEWEGUNG ERKANNT  SACK RUHIG HALTEN",
               "WEAK": "ZU SCHWACH  FESTER ZUSCHLAGEN",
               "UP": "BAG IST OBEN  ERST RUNTERLASSEN",
               "SETTLE": "ZU FRUEH  BAG ERST AUSPENDELN LASSEN"}
GRID_G = (1, 2, 5, 10, 20, 50, 100, 200, 400)
LOG_FLOOR_MG = 500          # alles darunter (Rauschen) liegt unten auf der Nulllinie


def _g(mg: int) -> str:
    return f"{mg / 1000:.2f}"


def _font(path: Path, size: int, fallback: str = "dejavusans", bold: bool = True) -> pygame.font.Font:
    try:
        return pygame.font.Font(str(path), max(size, 6))
    except (OSError, FileNotFoundError):
        log.warning("Schrift %s fehlt – nutze Systemschrift", path)
        return pygame.font.SysFont(fallback, max(size, 6), bold=bold)


def _ordinal(n: int) -> str:
    return f"{n}" + {1: "ST", 2: "ND", 3: "RD"}.get(n if n < 20 else n % 10, "TH")


def _ease_out(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


class Renderer:
    def __init__(self, size: tuple[int, int], font_digital: Path, font_arcade: Path,
                 max_score: int, crt: bool = True):
        self.w, self.h = size
        self.max_score = max_score
        self.digits = len(str(max_score))
        u = self.u = self.h / 100          # 1 % der Bildschirmhöhe

        # Riesige 7-Segment-Zahl: so groß, dass sie in 62 % der Breite passt
        size_px = int(34 * u)
        while size_px > 20:
            f = _font(font_digital, size_px)
            if f.size("8" * self.digits)[0] <= self.w * 0.62:
                break
            size_px = int(size_px * 0.92)
        self.f_score = f
        self.f_title = _font(font_arcade, int(11 * u))
        self.f_big = _font(font_arcade, int(8 * u))
        self.f_mid = _font(font_arcade, int(5.5 * u))
        self.f_small = _font(font_arcade, int(4 * u))
        self.f_tiny = _font(font_arcade, int(2.8 * u))
        self.f_seg = _font(font_digital, int(4.4 * u))
        self.f_seg_small = _font(font_digital, int(3.2 * u))
        self.f_seg_tiny = _font(font_digital, int(2.2 * u))
        self._arcade = {id(f) for f in (self.f_title, self.f_big, self.f_mid, self.f_small,
                                        self.f_tiny)}

        self._cache: dict = {}
        self._grad_cache: dict = {}
        self._fx = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        self._flash = pygame.Surface((self.w, self.h))
        self._flash.fill(WHITE)
        self._band: pygame.Surface | None = None
        self._horizon = int(self.h * 0.55)
        self._sky = self._gradient((self.w, self._horizon), (BG, BG, (54, 12, 76)))
        self._crt = self._make_crt() if crt else None
        # Ergebnis-Hintergrund in Mosaik-Auflösung (spart Rechenzeit, sieht nach 8-Bit aus)
        self._block = max(4, int(u * 1.2))
        self._lo_size = (max(1, self.w // self._block), max(1, self.h // self._block))
        self._frozen: pygame.Surface | None = None
        self._lo = pygame.Surface(self._lo_size)
        self._lo_fx = pygame.Surface(self._lo_size, pygame.SRCALPHA)

        # Funken / Konfetti: [x, y, vx, vy, alter, lebensdauer, farbe, größe, schwerkraft]
        self._sparks: list[list] = []
        self._hit_id: float | None = None
        self._last_now: float | None = None
        self._confetti_acc = 0.0
        self._glow_key = None
        self._glow: pygame.Surface | None = None

    # -- Text ----------------------------------------------------------------
    def _gradient(self, size: tuple[int, int], stops) -> pygame.Surface:
        key = (size, stops)
        g = self._grad_cache.get(key)
        if g is None:
            w, h = size
            col = pygame.Surface((1, max(h, 1)), pygame.SRCALPHA)
            n = len(stops) - 1
            for y in range(max(h, 1)):
                t = y / max(h - 1, 1) * n
                i = min(int(t), n - 1)
                f = t - i
                a, b = stops[i], stops[i + 1]
                col.set_at((0, y), (*(int(a[k] + (b[k] - a[k]) * f) for k in range(3)), 255))
            g = self._grad_cache[key] = pygame.transform.scale(col, (max(w, 1), max(h, 1)))
        return g

    def text(self, font, text: str, color=WHITE, gradient=None, outline: int = 0,
             aa: bool = False) -> pygame.Surface:
        """Text mit optionalem Farbverlauf und Arcade-Umrandung (zwischengespeichert)."""
        key = (id(font), text, color, gradient, outline, aa)
        surf = self._cache.get(key)
        if surf is None:
            if len(self._cache) > 800:
                self._cache.clear()
            surf = self._cache[key] = self._render_text(font, text, color, gradient, outline, aa)
        return surf

    def _raw(self, font, text: str, aa: bool, color) -> pygame.Surface:
        if id(font) not in self._arcade:
            return font.render(text, aa, color).convert_alpha()
        # ArcadeClassic hat schiefe Laufweiten (T, L, I …) – Zeichen einzeln nach Tinte setzen
        h = font.get_height()
        gap = max(1, round(h * 0.045))
        space = round(h * 0.3)
        parts, x = [], 0
        for ch in text:
            if ch == " ":
                x += space
                continue
            g = font.render(ch, aa, color).convert_alpha()
            ink = g.get_bounding_rect()
            if not ink.width:
                x += space
                continue
            parts.append((g, x, pygame.Rect(ink.x, 0, ink.width, h)))
            x += ink.width + gap
        out = pygame.Surface((max(1, x - gap), h), pygame.SRCALPHA)
        for g, gx, area in parts:
            out.blit(g, (gx, 0), area)
        return out

    def _render_text(self, font, text, color, gradient, outline, aa) -> pygame.Surface:
        base = self._raw(font, text, aa, WHITE if gradient else color)
        if gradient:
            base.blit(self._gradient(base.get_size(), gradient), (0, 0),
                      special_flags=pygame.BLEND_RGBA_MULT)
        if not outline:
            return base
        o = outline
        shadow = max(1, o // 2)
        ink = self._raw(font, text, aa, INK)
        bw, bh = base.get_size()
        out = pygame.Surface((bw + 2 * o + shadow, bh + 2 * o + shadow), pygame.SRCALPHA)
        step = max(1, o // 3)
        offsets = [(dx, dy) for dx in range(-o, o + 1, step) for dy in range(-o, o + 1, step)]
        for dx, dy in offsets:      # Schlagschatten nach rechts unten
            out.blit(ink, (o + dx + shadow, o + dy + shadow))
        for dx, dy in offsets:      # Umrandung
            out.blit(ink, (o + dx, o + dy))
        out.blit(base, (o, o))
        return out

    def _ol(self, font) -> int:
        """Passende Umrandungsstärke für eine Schriftgröße."""
        return max(2, int(font.get_height() * 0.07))

    @staticmethod
    def _put(screen, surf, x, y, anchor: str = "center") -> pygame.Rect:
        rect = surf.get_rect(**{anchor: (int(x), int(y))})
        screen.blit(surf, rect)
        return rect

    def _glow_of(self, surf: pygame.Surface, pad: int, factor: int = 8) -> pygame.Surface:
        """Weichgezeichnete Kopie auf Schwarz – wird additiv geblittet (Leuchten)."""
        w, h = surf.get_size()
        sw, sh = max(1, w // factor), max(1, h // factor)
        sp = max(1, pad // factor)
        small = pygame.Surface((sw + 2 * sp, sh + 2 * sp))
        small.fill((0, 0, 0))
        small.blit(pygame.transform.smoothscale(surf, (sw, sh)), (sp, sp))
        small.blit(small, (0, 0), special_flags=pygame.BLEND_RGB_ADD)    # doppelt so hell
        return pygame.transform.smoothscale(small, (w + 2 * pad, h + 2 * pad))

    # -- Effekte -------------------------------------------------------------
    def _make_crt(self) -> pygame.Surface:
        """Scanlines + Vignette als ein einziges Overlay (einmal berechnet)."""
        w, h = self.w, self.h
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        xs = np.linspace(-1, 1, w, dtype=np.float32)[:, None]
        ys = np.linspace(-1, 1, h, dtype=np.float32)[None, :]
        r = np.sqrt(xs * xs * 0.85 + ys * ys)
        alpha = np.clip((r - 0.8) / 0.65, 0, 1) ** 1.5 * 180
        period = max(2, round(h / 330))
        lines = (np.arange(h) % period == period - 1).astype(np.float32)[None, :] * 64
        a = pygame.surfarray.pixels_alpha(surf)
        a[:] = np.maximum(alpha, lines).astype(np.uint8)
        del a
        return surf

    def post(self, screen: pygame.Surface) -> None:
        """Zum Schluss jedes Frames: CRT-Look drüberlegen."""
        if self._crt is not None:
            screen.blit(self._crt, (0, 0))

    def _tick(self, now: float) -> float:
        dt = 0.0 if self._last_now is None else min(0.05, max(0.0, now - self._last_now))
        self._last_now = now
        alive = []
        for p in self._sparks:
            p[4] += dt
            if p[4] < p[5]:
                p[0] += p[2] * dt
                p[1] += p[3] * dt
                p[3] += p[8] * dt
                alive.append(p)
        self._sparks = alive
        return dt

    def _burst(self, cx, cy, n: int, colors, speed: float) -> None:
        u = self.u
        for _ in range(n):
            a = random.uniform(0, math.tau)
            v = random.uniform(0.25, 1.0) * speed
            self._sparks.append([cx, cy, math.cos(a) * v, math.sin(a) * v - speed * 0.25, 0.0,
                                 random.uniform(0.5, 1.1), random.choice(colors),
                                 max(2, int(random.uniform(0.6, 1.4) * u)), 70 * u])

    def _confetti(self, dt: float) -> None:
        u = self.u
        self._confetti_acc += dt * 45
        colors = (AMBER, RED, CYAN, MAGENTA, GREEN, WHITE)
        while self._confetti_acc >= 1:
            self._confetti_acc -= 1
            self._sparks.append([random.uniform(0, self.w), -2 * u, random.uniform(-6, 6) * u,
                                 random.uniform(10, 25) * u, 0.0, 4.0, random.choice(colors),
                                 max(2, int(random.uniform(0.8, 1.4) * u)), 4 * u])

    def _draw_sparks(self, screen) -> None:
        for x, y, _vx, _vy, age, life, col, size, _g in self._sparks:
            s = max(1, int(size * (1 - 0.6 * age / life)))
            pygame.draw.rect(screen, col, (int(x), int(y), s, s))

    def _sunburst(self, surf, cx, cy, angle: float, color, rays: int = 20) -> None:
        R = math.hypot(*surf.get_size())
        step = math.tau / rays
        for i in range(0, rays, 2):
            a0 = angle + i * step
            a1 = a0 + step
            pygame.draw.polygon(surf, color, [
                (cx, cy),
                (cx + R * math.cos(a0), cy + R * math.sin(a0)),
                (cx + R * math.cos(a1), cy + R * math.sin(a1)),
            ])

    def freeze(self, screen: pygame.Surface, black: bool) -> None:
        """Aktuelles Videobild als verpixelten, gedimmten Hintergrund für das Ergebnis merken."""
        if black:
            self._frozen = None
            return
        try:
            frozen = pygame.transform.smoothscale(screen, self._lo_size)
        except ValueError:      # exotisches Pixelformat
            frozen = pygame.transform.scale(screen, self._lo_size)
        frozen = frozen.convert()
        dim = pygame.Surface(self._lo_size, pygame.SRCALPHA)
        dim.fill((10, 4, 18, 200))
        frozen.blit(dim, (0, 0))
        self._frozen = frozen

    # -- Szenen --------------------------------------------------------------
    def draw_idle_hud(self, screen, video_rect, highscores: Highscores, connected: bool,
                      now: float, bag_up: bool = False) -> None:
        u = self.u
        _vx, vy, _vw, vh = video_rect
        top_m, bot_m = vy, self.h - (vy + vh)

        # Freier Platz über dem Video (z.B. 5:4-Monitor): Titel
        if top_m >= 9 * u:
            font = self.f_big if self.f_big.get_height() < top_m * 0.85 else self.f_mid
            title = self.text(font, "BOXAUTOMAT", gradient=FIRE, outline=self._ol(font))
            self._put(screen, title, self.w / 2, top_m / 2)

        # Leiste unten: über dem Video halbtransparent, im freien Rand direkt
        bar_h = int(max(bot_m, 11 * u))
        y0 = self.h - bar_h
        if bot_m < bar_h:
            if self._band is None or self._band.get_height() != bar_h:
                self._band = pygame.Surface((self.w, bar_h))
                self._band.fill(BG)
                self._band.set_alpha(200)
            screen.blit(self._band, (0, y0))
            pygame.draw.line(screen, (0, 140, 180), (0, y0), (self.w, y0), max(2, int(u * 0.3)))
        cy = y0 + bar_h / 2

        # links: Highscore
        x = 4 * u
        self._put(screen, self.text(self.f_tiny, "HI SCORE", CYAN), x, cy - 1.2 * u, "bottomleft")
        best = str(highscores.best) if highscores.best else "---"
        self._put(screen, self.text(self.f_seg, best, AMBER, aa=True), x, cy - 0.4 * u, "topleft")

        # Mitte: Aufforderung, blinkt im Arcade-Takt
        if (now % 1.2) < 0.85:
            label, grad = ("BAG RUNTERLASSEN", ICE) if bag_up else ("HAU DRAUF", FIRE)
            call = self.text(self.f_mid, label, gradient=grad, outline=self._ol(self.f_mid))
            self._put(screen, call, self.w / 2, cy)

        # rechts: Sensorstatus, nur wenn etwas fehlt
        if not connected:
            label = self.text(self.f_small, "NO SENSOR", RED, outline=2)
            r = self._put(screen, label, self.w - 4 * u, cy, "midright")
            if int(now * 2) % 2 == 0:
                pygame.draw.circle(screen, RED, (int(r.left - 2 * u), int(cy)), int(1.1 * u))

    def draw_result(self, screen, game: Game, best: int, now: float) -> None:
        u = self.u
        dt = self._tick(now)
        value = game.displayed_score(now)
        counting = game.state == State.COUNTING
        since = now - game.state_since
        frac = min(1.0, value / max(self.max_score, 1))
        cx, cy = self.w / 2, self.h * 0.42

        # neuer Schlag → Funkenregen aus der Anzeige
        if counting and self._hit_id != game.state_since:
            self._hit_id = game.state_since
            self._burst(cx, cy, 80, (AMBER, RED, WHITE, (255, 120, 0)), 110 * u)

        # Hintergrund + rotierende Strahlen, klein gezeichnet und als Mosaik hochskaliert
        if self._frozen is not None:
            self._lo.blit(self._frozen, (0, 0))
        else:
            self._lo.fill(BG)
        self._lo_fx.fill((0, 0, 0, 0))
        speed = 1.6 if counting else 0.25
        self._sunburst(self._lo_fx, cx / self._block, cy / self._block, now * speed,
                       (255, 40, 20, int(12 + 26 * frac)))
        self._lo.blit(self._lo_fx, (0, 0))
        pygame.transform.scale(self._lo, (self.w, self.h), screen)

        # Schütteln beim Hochzählen, klingt aus
        shake_x = shake_y = 0
        if counting:
            amp = u * 1.4 * (1 - game.progress(now)) ** 2
            shake_x = int(math.sin(now * 83) * amp)
            shake_y = int(math.cos(now * 61) * amp)

        title = self.text(self.f_big, "POWER", gradient=FIRE, outline=self._ol(self.f_big))
        self._put(screen, title, cx, self.h * 0.1)

        self._draw_led_panel(screen, value, cx + shake_x, cy + shake_y)

        bar_w = min(self.w * 0.78, 130 * u)
        self._draw_meter(screen, cx - bar_w / 2, self.h * 0.66, bar_w, 4.6 * u, frac,
                         min(1.0, best / max(self.max_score, 1)))

        if not counting:
            # Bewertung ploppt auf
            label, grad = next((t, g) for f, t, g in RATINGS if game.score >= f * self.max_score)
            rating = self.text(self.f_mid, label, gradient=grad, outline=self._ol(self.f_mid))
            pop = 1 + 0.7 * (1 - _ease_out(since / 0.3))
            if pop > 1.001:
                rw, rh = rating.get_size()
                rating = pygame.transform.scale(rating, (int(rw * pop), int(rh * pop)))
            self._put(screen, rating, cx, self.h * 0.78)

            if game.is_record:
                self._confetti(dt)
                if int(now * 3) % 3 != 2:
                    rec = self.text(self.f_big, "NEUER REKORD", gradient=FIRE,
                                    outline=self._ol(self.f_big))
                    s = 1 + 0.05 * math.sin(now * 9)
                    rec = pygame.transform.scale(rec, (int(rec.get_width() * s),
                                                       int(rec.get_height() * s)))
                    self._put(screen, rec, cx, self.h * 0.9)
            elif game.rank:
                rank = self.text(self.f_big, f"PLATZ {game.rank}", gradient=ICE,
                                 outline=self._ol(self.f_big))
                self._put(screen, rank, cx, self.h * 0.9)
            else:
                self._put(screen, self.text(self.f_small, "KEIN PLATZ IN DER BESTENLISTE", GREY,
                                            outline=2), cx, self.h * 0.9)

            # Restzeit bis zurück zum Video
            left = 1 - game.progress(now)
            lw = self.w * 0.6 * left
            pygame.draw.rect(screen, CYAN, (cx - lw / 2, self.h - 1.4 * u, lw, max(2, 0.5 * u)))

        self._draw_sparks(screen)

        # kurzer Blitz direkt nach dem Schlag
        if counting and since < 0.12:
            self._flash.set_alpha(int(255 * (1 - since / 0.12)))
            screen.blit(self._flash, (0, 0))

    def _draw_led_panel(self, screen, value: int, cx: float, cy: float) -> None:
        """7-Segment-Anzeige im Gehäuse: Geistersegmente, rote Ziffern, Leuchten."""
        u = self.u
        ghost = self.text(self.f_score, "8" * self.digits, RED_GHOST, aa=True)
        # DSEG: "!" ist eine leere Stelle in Ziffernbreite -> rechtsbündig wie echte Anzeige
        num = self.text(self.f_score, str(value).rjust(self.digits, "!"), RED, aa=True)
        gw, gh = ghost.get_size()
        pad_x, pad_y = 5 * u, 2.5 * u
        outer = pygame.Rect(0, 0, gw + 2 * pad_x, gh + 2 * pad_y)
        outer.center = (int(cx), int(cy))
        r = int(1.6 * u)
        pygame.draw.rect(screen, (34, 34, 46), outer, border_radius=r)
        pygame.draw.rect(screen, (86, 86, 104), outer, width=max(2, int(0.35 * u)), border_radius=r)
        inner = outer.inflate(-int(2.4 * u), -int(2.4 * u))
        pygame.draw.rect(screen, (14, 3, 5), inner, border_radius=int(0.8 * u))
        for sx in (outer.left + 1.3 * u, outer.right - 1.3 * u):     # Schrauben
            for sy in (outer.top + 1.3 * u, outer.bottom - 1.3 * u):
                pygame.draw.circle(screen, (120, 120, 136), (int(sx), int(sy)), max(2, int(0.45 * u)))

        self._put(screen, ghost, cx, cy)
        if value:
            pad = int(3 * u)
            if self._glow_key != value:
                self._glow_key = value
                self._glow = self._glow_of(num, pad)
            glow = self._glow
            gr = glow.get_rect(center=(int(cx), int(cy)))
            screen.blit(glow, gr, special_flags=pygame.BLEND_RGB_ADD)
        self._put(screen, num, cx, cy)
        # Glasreflex oben
        glare = pygame.Rect(inner.left, inner.top, inner.width, int(inner.height * 0.12))
        self._fx.fill((0, 0, 0, 0), glare)
        pygame.draw.rect(self._fx, (255, 255, 255, 14), glare, border_radius=int(0.8 * u))
        screen.blit(self._fx, glare, glare)

    def _draw_meter(self, screen, x0, y0, bw, bh, frac: float, best_frac: float) -> None:
        """Segmentierter Kraftbalken (grün → rot) mit Rekordmarke."""
        u = self.u
        n = 32
        gap = max(2, int(bw * 0.005))
        seg_w = (bw - gap * (n - 1)) / n
        frame = pygame.Rect(int(x0 - u), int(y0 - u), int(bw + 2 * u), int(bh + 2 * u))
        pygame.draw.rect(screen, (14, 12, 26), frame, border_radius=int(0.8 * u))
        pygame.draw.rect(screen, (86, 86, 104), frame, width=max(2, int(0.3 * u)),
                         border_radius=int(0.8 * u))
        lit = frac * n
        for i in range(n):
            hue = 0.33 * (1 - i / (n - 1))
            col = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1, 1))
            x = int(x0 + i * (seg_w + gap))
            rect = (x, int(y0), math.ceil(seg_w), int(bh))
            if i < int(lit) or (i == int(lit) and lit - int(lit) >= 0.5):
                pygame.draw.rect(screen, col, rect)
                hi = tuple(min(255, c + 110) for c in col)
                pygame.draw.rect(screen, hi, (x, int(y0), math.ceil(seg_w), max(1, int(bh * 0.25))))
            else:
                pygame.draw.rect(screen, tuple(int(c * 0.17) for c in col), rect)

        if best_frac > 0:
            mx = x0 + bw * best_frac
            top = y0 - 1.6 * u
            pygame.draw.polygon(screen, CYAN, [(mx - u, top - 1.4 * u), (mx + u, top - 1.4 * u),
                                               (mx, top)])
            self._put(screen, self.text(self.f_tiny, "HI", CYAN), mx, top - 1.8 * u, "midbottom")

    def _draw_grid(self, screen, now: float) -> None:
        """Synthwave-Boden mit Fluchtpunkt, läuft langsam auf den Betrachter zu."""
        w, h, hz = self.w, self.h, self._horizon
        screen.blit(self._sky, (0, 0))
        screen.fill((12, 4, 26), (0, hz, w, h - hz))
        color = (96, 24, 130)
        lw = max(1, int(self.u * 0.25))
        for k in range(-14, 15):
            pygame.draw.line(screen, color, (w / 2 + k * w * 0.03, hz), (w / 2 + k * w * 0.16, h), lw)
        phase = (now * 0.5) % 1
        for j in range(12):
            t = (j + phase) / 12
            y = hz + (h - hz) * t ** 2.2
            pygame.draw.line(screen, color, (0, y), (w, y), lw)
        pygame.draw.line(screen, MAGENTA, (0, hz), (w, hz), max(2, int(self.u * 0.4)))

    def draw_scoreboard(self, screen, highscores: Highscores, now: float, since: float,
                        highlight: int | None = None) -> None:
        u = self.u
        self._draw_grid(screen, now)

        title = self.text(self.f_title, f"TOP {highscores.size}", gradient=FIRE,
                          outline=self._ol(self.f_title))
        self._put(screen, title, self.w / 2, 8 * u)
        self._put(screen, self.text(self.f_small, "TOTAL SCORE RANKING", CYAN, outline=2),
                  self.w / 2, 16 * u)

        pw = min(self.w * 0.88, 125 * u)
        panel = pygame.Rect(int((self.w - pw) / 2), int(20.5 * u), int(pw), int(76 * u))
        self._fx.fill((0, 0, 0, 0))
        pygame.draw.rect(self._fx, (8, 8, 26, 220), panel, border_radius=int(1.5 * u))
        pygame.draw.rect(self._fx, (*CYAN, 255), panel, width=max(2, int(0.3 * u)),
                         border_radius=int(1.5 * u))

        cols = (panel.left + pw * 0.17, panel.left + pw * 0.5, panel.left + pw * 0.81)
        header_y = 24.5 * u
        n = highscores.size
        row_h = min(6.6 * u, 64 * u / max(n, 1))
        first_y = 30.5 * u
        if highlight and 1 <= highlight <= n and int(now * 3) % 2 == 0:
            hy = first_y + (highlight - 1) * row_h
            pygame.draw.rect(self._fx, (255, 255, 255, 36),
                             (panel.left + u, hy - row_h / 2, panel.width - 2 * u, row_h))
        screen.blit(self._fx, panel, panel)

        for x, label in zip(cols, ("RANK", "SCORE", "DATE")):
            self._put(screen, self.text(self.f_tiny, label, GREY), x, header_y)

        entries = highscores.entries or []
        for i in range(n):
            t = (now - since - 0.15 - i * 0.07) / 0.3
            if t <= 0:
                continue
            dx = (1 - _ease_out(t)) * self.w * 0.4
            y = first_y + i * row_h
            if i == 0:
                grad, color = FIRE, (255, 214, 40)
            elif i == 1:
                grad, color = SILVER, (200, 214, 235)
            elif i == 2:
                grad, color = BRONZE, (230, 130, 55)
            else:
                rgb = colorsys.hsv_to_rgb((0.5 + (i - 3) * 0.09) % 1, 0.75, 1)
                grad, color = None, tuple(int(c * 255) for c in rgb)
            e = entries[i] if i < len(entries) else None
            score = str(e["score"]).rjust(self.digits, "!") if e else "-" * self.digits
            date = e["date"] if e else "--.--.----"
            rank = self.text(self.f_small, _ordinal(i + 1), color, gradient=grad, outline=2)
            self._put(screen, rank, cols[0] + dx, y)
            self._put(screen, self.text(self.f_seg, score, color, aa=True), cols[1] + dx, y)
            dim = tuple(int(c * 0.75) for c in color)
            self._put(screen, self.text(self.f_seg_small, date, dim, aa=True), cols[2] + dx, y)

            if highlight == i + 1:      # Pfeile neben dem frischen Eintrag
                ax = panel.left + 2.2 * u + dx + math.sin(now * 8) * 0.6 * u
                s = 1.2 * u
                pygame.draw.polygon(screen, WHITE, [(ax, y - s), (ax + s, y), (ax, y + s)])
                bx = panel.right - 2.2 * u + dx - math.sin(now * 8) * 0.6 * u
                pygame.draw.polygon(screen, WHITE, [(bx, y - s), (bx - s, y), (bx, y + s)])

    # -- Kalibrierung ----------------------------------------------------------
    def _num(self, screen, value: str, unit: str, x, y, color, anchor: str = "midright",
             font=None, max_w: float | None = None):
        """Zahl in 7-Segment (kann Punkt und Minus) + Einheit in Arcade-Schrift."""
        num = self.text(font or self.f_seg_small, value, color, aa=True)
        if max_w and num.get_width() > max_w:       # zu lang -> schmaler drücken
            num = pygame.transform.smoothscale(num, (int(max_w), num.get_height()))
        unit_s = self.text(self.f_tiny, unit, color) if unit else None
        gap = int(self.u * 0.7)
        box = pygame.Rect(0, 0, num.get_width() + (gap + unit_s.get_width() if unit_s else 0),
                          num.get_height())
        setattr(box, anchor, (int(x), int(y)))
        screen.blit(num, box.topleft)
        if unit_s:
            screen.blit(unit_s, unit_s.get_rect(midleft=(box.left + num.get_width() + gap,
                                                         box.centery)))
        return box

    def _dashed(self, screen, color, y, x0, x1, width: int) -> None:
        dash = max(4, int(self.u * 1.2))
        for x in range(int(x0), int(x1), dash * 2):
            pygame.draw.line(screen, color, (x, y), (min(x + dash, x1), y), width)

    def _dashed_v(self, screen, color, x, y0, y1) -> None:
        dash = max(3, int(self.u * 0.8))
        for y in range(int(y0), int(y1), dash * 2):
            pygame.draw.line(screen, color, (x, y), (x, min(y + dash, y1)), 1)

    def draw_calibration(self, screen, cal: Calibration, now: float, port: str | None = None) -> None:
        u, w = self.u, self.w
        screen.fill(BG)
        title = self.text(self.f_big, "SENSOR KALIBRIERUNG", gradient=FIRE, outline=self._ol(self.f_big))
        self._put(screen, title, w / 2, 6 * u)
        if cal.reason in CAL_REASONS:
            self._put(screen, self.text(self.f_tiny, CAL_REASONS[cal.reason], GREY),
                      w - 3 * u, 2 * u, "topright")

        finished = not cal.active
        order = [k for k, _ in CAL_STEPS]
        if finished and not cal.aborted:
            cur = 3
        else:
            cur = order.index(cal.step) if cal.step in order else 0

        # Schritt-Leiste
        pill_w, gap = min(30 * u, w * 0.27), 3 * u
        x0 = w / 2 - (3 * pill_w + 2 * gap) / 2
        for i, (key, label) in enumerate(CAL_STEPS):
            rect = pygame.Rect(int(x0 + i * (pill_w + gap)), int(12 * u), int(pill_w), int(5 * u))
            text = f"{i + 1} {label}"
            if i < cur:
                fill, border, col = (8, 40, 20), GREEN, GREEN
                text += "  OK"
            elif i == cur and not finished:
                fill, col = (6, 30, 40), WHITE
                border = CYAN if int(now * 2) % 2 == 0 else (0, 110, 140)
                if key != "REST":
                    text += f"  {cal.done} VON {cal.need}"
            else:
                fill, border, col = (14, 14, 28), (60, 60, 80), GREY
            pygame.draw.rect(screen, fill, rect, border_radius=int(u))
            pygame.draw.rect(screen, border, rect, width=max(2, int(0.3 * u)), border_radius=int(u))
            self._put(screen, self.text(self.f_tiny, text, col), rect.centerx, rect.centery)

        # Anweisung
        if finished and cal.aborted:
            big, grad, sub = "ABGEBROCHEN", FIRE, "ALTE WERTE BLEIBEN AKTIV"
        elif finished:
            big, grad, sub = "FERTIG", TOXIC, "WERTE SIND IM ESP32 GESPEICHERT"
        elif cal.step == "REST":
            big, grad, sub = "NICHT BERUEHREN", ICE, "BAG UNTEN HAENGEN LASSEN  RUHE WIRD GEMESSEN"
        elif cal.step == "LIGHT":
            big, grad, sub = "LEICHT KLOPFEN", TOXIC, f"{cal.need}X SANFT ABER ECHT ANTIPPEN"
        else:
            big, grad, sub = "VOLL DRAUF", FIRE, f"{cal.need}X SO HART WIE MOEGLICH"
        instr = self.text(self.f_big, big, gradient=grad, outline=self._ol(self.f_big))
        if not finished:
            pulse = 1 + 0.04 * math.sin(now * 6)
            instr = pygame.transform.scale(instr, (int(instr.get_width() * pulse),
                                                   int(instr.get_height() * pulse)))
        self._put(screen, instr, w / 2, 23 * u)
        self._put(screen, self.text(self.f_small, sub, WHITE), w / 2, 30 * u)
        if not finished and cal.step != "REST":
            r = 1.1 * u
            for i in range(cal.need):
                cx = w / 2 + (i - (cal.need - 1) / 2) * 4 * u
                if i < cal.done:
                    pygame.draw.circle(screen, GREEN, (int(cx), int(34 * u)), int(r))
                else:
                    pygame.draw.circle(screen, GREY, (int(cx), int(34 * u)), int(r),
                                       max(1, int(0.3 * u)))

        top, height = 37 * u, 49 * u
        self._draw_scope(screen, cal, now,
                         pygame.Rect(int(3 * u), int(top), int(w * 0.6), int(height)))
        left = 3 * u + w * 0.6 + 2 * u
        self._draw_cal_values(screen, cal, port,
                              pygame.Rect(int(left), int(top), int(w - 3 * u - left), int(height)))

        # Hinweise unten: Übersteuerung > abgelehnter Schlag
        msg, col = None, AMBER
        fix = ("MESSBEREICH IM SKETCH ERHOEHEN" if cal.range_g < 400
               else "SENSOR NAEHER ANS SCHARNIER SETZEN")
        if cal.clipped:
            msg, col = f"UEBERSTEUERT  {fix}", RED
        elif cal.near_limit:
            msg = f"AM MESSLIMIT  {fix}"
        elif cal.bag_down is False and not finished and cal.step != "REST":
            msg = "BAG IST OBEN  RUNTERLASSEN UND KURZ WARTEN"
        elif cal.reject and now - cal.reject_at < 3 and not finished:
            msg, col = CAL_REJECTS.get(cal.reject, cal.reject), RED
        if msg and int(now * 2.5) % 3 != 2:
            self._put(screen, self.text(self.f_small, msg, col, outline=2), w / 2, 90 * u)
        if finished and cal.ended_at is not None:
            left_s = (ABORT_SECONDS if cal.aborted else DONE_SECONDS) - (now - cal.ended_at)
            footer = f"ZURUECK ZUM SPIEL IN {max(0, math.ceil(left_s))}"
        else:
            footer = "ABBRECHEN  SCHALTER 6X KIPPEN ODER TASTE K"
        self._put(screen, self.text(self.f_tiny, footer, GREY), w / 2, 95.5 * u)

    def _draw_scope(self, screen, cal: Calibration, now: float, panel: pygame.Rect) -> None:
        """Oszilloskop: Beschleunigung der letzten Sekunden, logarithmisch bis zum Messbereich."""
        u = self.u
        lw = max(2, int(0.3 * u))
        pygame.draw.rect(screen, (4, 14, 12), panel, border_radius=int(u))
        pygame.draw.rect(screen, (0, 120, 100), panel, width=lw, border_radius=int(u))
        self._put(screen, self.text(self.f_tiny, "LIVE KURVE IN G", CYAN),
                  panel.left + 1.5 * u, panel.top + 1.2 * u, "topleft")
        plot = pygame.Rect(panel.left + int(7 * u), panel.top + int(5.5 * u),
                           panel.width - int(9 * u), panel.height - int(7.5 * u))
        clip_mg = cal.clip_mg
        top_mg = clip_mg * 1.05

        def gy(mg: float) -> float:
            # log: leichte Klopfer (wenige g) und harte Schläge (bis 200 g) gut sichtbar
            frac = math.log(min(max(mg, LOG_FLOOR_MG), top_mg) / LOG_FLOOR_MG) / math.log(top_mg / LOG_FLOOR_MG)
            return plot.bottom - plot.height * frac

        def gx(t: float) -> float:
            return plot.right - (now - t) / LIVE_SECONDS * plot.width

        for g in (g for g in GRID_G if LOG_FLOOR_MG < g * 1000 <= clip_mg):
            y = gy(g * 1000)
            pygame.draw.line(screen, (0, 46, 36), (plot.left, y), (plot.right, y), 1)
            self._put(screen, self.text(self.f_seg_tiny, f"{g:g}", (0, 150, 110), aa=True),
                      plot.left - 1.2 * u, y, "midright")

        # Grenzen: Messlimit, Schwellen
        y = gy(clip_mg)
        pygame.draw.line(screen, RED, (plot.left, y), (plot.right, y), lw)
        self._put(screen, self.text(self.f_tiny, "CLIP", RED), plot.right - u, y + 0.6 * u, "topright")
        if cal.result:
            for mg, label, col, anchor, x in (
                    (cal.result.trigger_mg, "SCHWELLE", GREEN, "bottomleft", plot.left + u),
                    (cal.result.max_mg, "999 PUNKTE", AMBER, "topright", plot.right - u)):
                y = gy(mg)
                self._dashed(screen, col, y, plot.left, plot.right, lw)
                self._put(screen, self.text(self.f_tiny, label, col), x,
                          y + (0.6 * u if anchor == "topright" else -0.6 * u), anchor)
        elif cal.active and cal.detect_mg:
            y = gy(cal.detect_mg)
            self._dashed(screen, AMBER, y, plot.left, plot.right, max(1, lw // 2))
            self._put(screen, self.text(self.f_tiny, "AUSLOESER", AMBER), plot.left + u,
                      y - 0.6 * u, "bottomleft")

        pts = [(gx(t), gy(mg)) for t, mg in cal.live if now - t <= LIVE_SECONDS]
        if len(pts) >= 2:
            pygame.draw.lines(screen, (0, 90, 45), False, pts, max(3, int(0.9 * u)))
            pygame.draw.lines(screen, (90, 255, 140), False, pts, max(1, int(0.3 * u)))
        else:
            self._put(screen, self.text(self.f_small, "WARTE AUF DATEN", GREY), plot.centerx,
                      plot.centery)

        for t, mg, step, clip in cal.marks:
            x, y = gx(t), gy(mg)
            if x < plot.left:
                continue
            col = RED if clip else CYAN if step == "LIGHT" else AMBER
            self._dashed_v(screen, col, x, y, plot.bottom)
            pygame.draw.polygon(screen, col, [(x - u, y - 1.6 * u), (x + u, y - 1.6 * u),
                                              (x, y - 0.3 * u)])
            self._put(screen, self.text(self.f_seg_tiny, f"{mg / 1000:.1f}", col, aa=True),
                      x, y - 2 * u, "midbottom")

    def _draw_cal_values(self, screen, cal: Calibration, port: str | None,
                         panel: pygame.Rect) -> None:
        """Alle Messwerte, die der Sensor liefert, als Tabelle."""
        u = self.u
        pygame.draw.rect(screen, (8, 8, 26), panel, border_radius=int(u))
        pygame.draw.rect(screen, (0, 110, 140), panel, width=max(2, int(0.3 * u)),
                         border_radius=int(u))
        self._put(screen, self.text(self.f_tiny, "MESSWERTE", CYAN),
                  panel.left + 1.5 * u, panel.top + 1.2 * u, "topleft")

        def peaks(hits):
            return "  ".join(f"{mg / 1000:.1f}" for mg, _ in hits) if hits else "--"

        live = cal.live_mg
        live_col = (RED if live >= cal.clip_mg * 0.95 else AMBER if live >= cal.clip_mg * 0.4
                    else GREEN)
        info = cal.info
        old = cal.old_config or cal.config
        if cal.result:
            trig, mx, source = cal.result.trigger_mg, cal.result.max_mg, "NEU"
        elif old:
            trig, mx = old.trigger_mg, old.max_mg
            source = {"eeprom": "EEPROM", "default": "STANDARD"}.get(old.source, "UNBEKANNT")
        else:
            trig = mx = None
            source = "UNBEKANNT"
        new_col = GREEN if cal.result else WHITE
        rows = [
            ("LIVE", _g(live), "G", live_col),
            ("SPITZE 6S", _g(cal.window_peak_mg), "G", WHITE),
            ("RAUSCHEN", _g(cal.noise_mg) if cal.noise_mg is not None else "--", "G", WHITE),
            ("LEICHT", peaks(cal.light), "G", CYAN),
            ("HART", peaks(cal.hard), "G", RED if cal.clipped else AMBER),
            ("ABTASTRATE", str(info.rate_hz) if info else "--", "HZ", WHITE),
            ("MESSBEREICH", str(cal.range_g), "G", WHITE),
            ("LAGE XYZ", " ".join(f"{v / 1000:.2f}" for v in info.base_mg) if info else "--",
             "G", WHITE),
            ("BAG", None, {True: "UNTEN", False: "OBEN"}.get(cal.bag_down, "UNBEKANNT"),
             GREEN if cal.bag_down else AMBER if cal.bag_down is False else GREY),
            ("SCHWELLE", _g(trig) if trig is not None else "--", "G", new_col),
            ("999 BEI", _g(mx) if mx is not None else "--", "G", new_col),
            ("QUELLE", None, source, new_col),
            ("FIRMWARE", cal.firmware or "--", "", GREY),
            ("PORT", None, port.split("/")[-1].upper() if port else "KEINER", GREY),
        ]
        first = panel.top + 5.5 * u
        row_h = (panel.bottom - u - first) / len(rows)
        for i, (label, value, unit, col) in enumerate(rows):
            y = first + (i + 0.5) * row_h
            lab = self._put(screen, self.text(self.f_tiny, label, GREY), panel.left + 1.5 * u, y,
                            "midleft")
            if value is None:
                self._put(screen, self.text(self.f_tiny, unit, col), panel.right - 1.5 * u, y,
                          "midright")
            else:
                font = self.f_seg_small if i == 0 else self.f_seg_tiny
                max_w = panel.right - 1.5 * u - lab.right - 2 * u - 3 * u
                self._num(screen, value, unit, panel.right - 1.5 * u, y, col, font=font,
                          max_w=max_w)

