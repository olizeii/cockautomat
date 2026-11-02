"""Kalibrierung: sammelt die "CAL …"-Meldungen der Firmware für den Kalibrier-Bildschirm.

Die eigentliche Kalibrierung läuft auf dem Arduino (Ergebnis landet dort im EEPROM).
Diese Klasse merkt sich nur alles, was angezeigt werden soll – ohne Grafik, damit testbar.
"""
from __future__ import annotations

import math
import random
from collections import deque

from .protocol import (BagPosition, CalAbort, CalBegin, CalDone, CalHit, CalInfo, CalLive,
                       CalReject, CalRest, CalState, Connection, SensorConfig)

DEFAULT_RANGE_G = 200      # H3LIS331, bis die Firmware ihren Messbereich meldet (CFG)
RESERVE = 1.3              # wie in der Firmware: 999 = Mittel hart * 1,3
LIVE_SECONDS = 6.0         # so lange reicht die Kurve zurück
DONE_SECONDS = 15.0        # so lange bleibt das Ergebnis stehen
ABORT_SECONDS = 4.0


class Calibration:
    def __init__(self):
        self.config: SensorConfig | None = None     # aktuell aktive Firmware-Einstellung
        self.firmware: str | None = None
        self.bag_down: bool | None = None           # None = Firmware meldet es nicht
        self.active = False
        self._reset()

    def _reset(self) -> None:
        self.reason = ""
        self.step = "REST"
        self.done = 0
        self.need = 1
        self.noise_mg: int | None = None
        self.light: list[tuple[int, bool]] = []     # (Spitze mg, übersteuert)
        self.hard: list[tuple[int, bool]] = []
        self.reject: str | None = None
        self.reject_at = 0.0
        self.live: deque[tuple[float, int]] = deque()
        self.marks: deque[tuple[float, int, str, bool]] = deque()   # Schläge in der Kurve
        self.info: CalInfo | None = None
        self.old_config: SensorConfig | None = None
        self.result: CalDone | None = None
        self.aborted = False
        self.ended_at: float | None = None

    # -- Events --------------------------------------------------------------
    def apply(self, ev, now: float) -> bool:
        """Verarbeitet ein Event. True, wenn es zur Kalibrierung gehörte."""
        if isinstance(ev, SensorConfig):
            self.config = ev
            return False        # App loggt es zusätzlich
        if isinstance(ev, BagPosition):
            self.bag_down = ev.down
            return False
        if isinstance(ev, Connection):
            if not ev.connected and self.active:
                self._end(now, aborted=True)    # Nano resettet beim Wiederverbinden
            return False
        if isinstance(ev, CalBegin):
            self._reset()
            self.active = True
            self.reason = ev.reason
            self.old_config = self.config
            return True
        if not isinstance(ev, (CalState, CalRest, CalHit, CalReject, CalLive, CalInfo, CalDone,
                               CalAbort)):
            return False
        if not self.active:
            if not isinstance(ev, CalState):
                return True         # Reste einer beendeten Kalibrierung
            # Mitten in eine laufende Kalibrierung hineinverbunden (STATE kommt jede Sekunde)
            self._reset()
            self.active = True
            self.old_config = self.config
        if isinstance(ev, CalState):
            if ev.step != self.step:
                self.reject = None
            self.step, self.done, self.need = ev.step, ev.done, ev.need
        elif isinstance(ev, CalRest):
            self.noise_mg = ev.noise_mg
        elif isinstance(ev, CalHit):
            (self.light if ev.step == "LIGHT" else self.hard).append((ev.peak_mg, ev.clipped))
            self.marks.append((now, ev.peak_mg, ev.step, ev.clipped))
            self.reject = None
        elif isinstance(ev, CalReject):
            self.reject, self.reject_at = ev.reason, now
        elif isinstance(ev, CalLive):
            self.live.append((now, ev.mg))
        elif isinstance(ev, CalInfo):
            self.info = ev
        elif isinstance(ev, CalDone):
            self.result = ev
            self._end(now, aborted=False)
        elif isinstance(ev, CalAbort):
            self._end(now, aborted=True)
        self._trim(now)
        return True

    def _end(self, now: float, aborted: bool) -> None:
        self.active = False
        self.aborted = aborted
        self.ended_at = now

    def _trim(self, now: float) -> None:
        while self.live and now - self.live[0][0] > LIVE_SECONDS:
            self.live.popleft()
        while self.marks and now - self.marks[0][0] > LIVE_SECONDS:
            self.marks.popleft()

    # -- Anzeige -------------------------------------------------------------
    def visible(self, now: float) -> bool:
        if self.active:
            return True
        if self.ended_at is None:
            return False
        hold = ABORT_SECONDS if self.aborted else DONE_SECONDS
        return now - self.ended_at < hold

    def dismiss(self) -> None:
        self.ended_at = None

    @property
    def live_mg(self) -> int:
        return self.live[-1][1] if self.live else 0

    @property
    def window_peak_mg(self) -> int:
        return max((mg for _, mg in self.live), default=0)

    @property
    def detect_mg(self) -> int | None:
        """Auslöseschwelle der Firmware während der Kalibrierung (gleiche Formel)."""
        if self.noise_mg is None:
            return None
        return max(int(self.noise_mg * 1.5), 500)

    @property
    def range_g(self) -> int:
        return self.config.range_g if self.config else DEFAULT_RANGE_G

    @property
    def clip_mg(self) -> int:
        """So viel kann der Sensor höchstens messen."""
        return self.range_g * 1000

    @property
    def clipped(self) -> bool:
        """Harte Schläge waren am Anschlag -> Sensor näher ans Scharnier."""
        return any(c for _, c in self.hard) or bool(self.result and self.result.clips)

    @property
    def near_limit(self) -> bool:
        """999 läge über dem, was der Sensor messen kann."""
        if not self.hard:
            return False
        mean = sum(mg for mg, _ in self.hard) / len(self.hard)
        return mean * RESERVE >= self.clip_mg * 0.95


def demo_events(t0: float) -> list[tuple[float, object]]:
    """Ablauf einer Kalibrierung ohne Arduino (Taste K mit --no-serial)."""
    rng = random.Random(7)
    out: list[tuple[float, object]] = [
        (0.0, CalBegin("command")), (0.0, CalState("REST", 0, 1)),
        (0.2, CalInfo(1000, None, (40, -1010, 120))),
    ]
    # Zeitpunkte und Spitzen der Schläge (H3LIS331, ±200 g)
    light = [(5.0, 4200), (6.6, 5100), (8.1, 3800)]
    hard = [(11.0, 61000), (12.4, 6900), (13.8, 74000), (15.3, 82000)]
    peaks = {t: mg for t, mg in light + hard}
    t = 0.0
    while t < 15.6:
        mg = 0 if t < 1.0 else int(abs(rng.gauss(0, 150)))
        for tp, pk in peaks.items():
            if 0 <= t - tp < 0.4:
                mg = max(mg, int(pk * math.exp(-(t - tp) * 9) * (0.85 + 0.3 * rng.random())))
        out.append((t, CalLive(mg)))
        t += 0.04
    out += [(2.5, CalRest(390)), (2.5, CalState("LIGHT", 0, 3))]
    for i, (tp, pk) in enumerate(light, 1):
        out.append((tp + 0.12, CalHit("LIGHT", i, pk, False)))
        out.append((tp + 0.12, CalState("LIGHT", i, 3) if i < 3 else CalState("HARD", 0, 3)))
    n = 0
    for tp, pk in hard:
        if pk < 5100 * 1.5:         # zweiter Schlag ist absichtlich zu schwach
            out.append((tp + 0.12, CalReject("WEAK")))
            continue
        n += 1
        out.append((tp + 0.12, CalHit("HARD", n, pk, False)))
        if n < 3:
            out.append((tp + 0.12, CalState("HARD", n, 3)))
    mean = (61000 + 74000 + 82000) / 3
    trigger, top = int(3800 * 0.7), int(min(mean * RESERVE, DEFAULT_RANGE_G * 950))
    out.append((15.6, CalDone(trigger, top, 0)))
    out.append((15.6, SensorConfig(trigger, top, "eeprom", DEFAULT_RANGE_G)))
    return sorted(((t0 + t, ev) for t, ev in out), key=lambda x: x[0])
