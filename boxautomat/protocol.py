"""Serielles Protokoll zwischen Arduino und PC (siehe arduino/boxautomat_sensor)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    score: int
    peak_mg: int | None = None


@dataclass(frozen=True)
class SwitchState:
    on: bool


@dataclass(frozen=True)
class DeviceReady:
    version: str


@dataclass(frozen=True)
class DeviceMessage:
    level: str   # "INFO" | "ERR"
    text: str


@dataclass(frozen=True)
class Connection:
    connected: bool
    port: str | None = None


Event = Hit | SwitchState | DeviceReady | DeviceMessage | Connection


def parse_line(line: str) -> Event | None:
    """Wandelt eine Zeile vom Arduino in ein Event um. Unbekanntes -> None."""
    line = line.strip()
    if not line:
        return None

    if line == "S1":
        return SwitchState(True)
    if line == "S0":
        return SwitchState(False)

    # Altes Protokoll: nur eine Zahl pro Zeile = Score
    if line.isdigit():
        return Hit(int(line))

    head, _, rest = line.partition(" ")
    if head == "H":
        parts = rest.split()
        try:
            score = int(parts[0])
            peak = int(parts[1]) if len(parts) > 1 else None
        except (IndexError, ValueError):
            return None
        return Hit(score, peak)
    if head == "READY":
        return DeviceReady(rest.strip() or "?")
    if head in ("INFO", "ERR"):
        return DeviceMessage(head, rest.strip())
    # "R <mg>" (Rohwert-Stream) und Rauschen werden ignoriert
    return None
