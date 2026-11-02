"""Serielles Protokoll zwischen Arduino und PC (siehe arduino/boxautomat_sensor)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    score: int
    peak_mg: int | None = None
    clipped: bool = False       # Sensor war am Anschlag (±16 g) – Wert zu niedrig


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


@dataclass(frozen=True)
class BagPosition:
    down: bool              # True = hängt unten (spielbereit), False = oben eingerastet


@dataclass(frozen=True)
class SensorConfig:
    """Aktive Schwelle / 999-Wert der Firmware (gespeichert oder Standard)."""
    trigger_mg: int
    max_mg: int
    source: str             # "eeprom" | "default"
    range_g: int = 16       # Messbereich des Sensors (alte MPU-6050-Firmware: 16)


# -- Kalibrierung (Zeilen "CAL ...") ----------------------------------------------
@dataclass(frozen=True)
class CalBegin:
    reason: str             # "first" | "switch" | "command"


@dataclass(frozen=True)
class CalState:
    step: str               # "REST" | "LIGHT" | "HARD"
    done: int
    need: int


@dataclass(frozen=True)
class CalRest:
    noise_mg: int


@dataclass(frozen=True)
class CalHit:
    step: str
    index: int
    peak_mg: int
    clipped: bool


@dataclass(frozen=True)
class CalReject:
    reason: str             # "MOVING" | "WEAK"


@dataclass(frozen=True)
class CalLive:
    mg: int


@dataclass(frozen=True)
class CalInfo:
    rate_hz: int
    temp_dc: int | None     # 1/10 °C, None wenn der Sensor keine Temperatur hat
    base_mg: tuple[int, int, int]


@dataclass(frozen=True)
class CalDone:
    trigger_mg: int
    max_mg: int
    clips: int


@dataclass(frozen=True)
class CalAbort:
    pass


CalEvent = (CalBegin | CalState | CalRest | CalHit | CalReject | CalLive | CalInfo | CalDone
            | CalAbort)
Event = (Hit | SwitchState | DeviceReady | DeviceMessage | Connection | SensorConfig | BagPosition
         | CalEvent)


def _parse_cal(rest: str) -> CalEvent | None:
    kind, *a = rest.split()
    if kind == "BEGIN":
        return CalBegin(a[0] if a else "?")
    if kind == "STATE" and a[0] in ("REST", "LIGHT", "HARD"):
        return CalState(a[0], int(a[1]), int(a[2]))
    if kind == "REST":
        return CalRest(int(a[0]))
    if kind == "HIT" and a[0] in ("LIGHT", "HARD"):
        return CalHit(a[0], int(a[1]), int(a[2]), len(a) > 3 and a[3] == "1")
    if kind == "REJECT":
        return CalReject(a[0])
    if kind == "LIVE":
        return CalLive(int(a[0]))
    if kind == "INFO":
        temp = None if a[1] == "-" else int(a[1])
        return CalInfo(int(a[0]), temp, (int(a[2]), int(a[3]), int(a[4])))
    if kind == "DONE":
        return CalDone(int(a[0]), int(a[1]), int(a[2]))
    if kind == "ABORT":
        return CalAbort()
    return None


def parse_line(line: str) -> Event | None:
    """Wandelt eine Zeile vom Arduino in ein Event um. Unbekanntes -> None."""
    line = line.strip()
    if not line:
        return None

    if line == "S1":
        return SwitchState(True)
    if line == "S0":
        return SwitchState(False)
    if line == "POS DOWN":
        return BagPosition(True)
    if line == "POS UP":
        return BagPosition(False)

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
        return Hit(score, peak, len(parts) > 2 and parts[2] == "1")
    if head == "CFG":
        parts = rest.split()
        try:
            return SensorConfig(int(parts[0]), int(parts[1]),
                                parts[2] if len(parts) > 2 else "?",
                                int(parts[3]) if len(parts) > 3 else 16)
        except (IndexError, ValueError):
            return None
    if head == "CAL":
        try:
            return _parse_cal(rest)
        except (IndexError, ValueError):
            return None
    if head == "READY":
        return DeviceReady(rest.strip() or "?")
    if head in ("INFO", "ERR"):
        return DeviceMessage(head, rest.strip())
    # "R <mg>" (Rohwert-Stream) und Rauschen werden ignoriert
    return None
