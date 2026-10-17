"""Konfiguration: Standardwerte + optionale config.toml im Projektordner."""
from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_DIR / "assets"


@dataclass
class DisplayConfig:
    display_index: int = 1          # 0 = Hauptbildschirm, 1 = Zweitbildschirm
    mode: str = "fullscreen"        # "fullscreen" | "borderless" | "windowed"
    width: int = 0                  # 0 = Auflösung des Bildschirms übernehmen
    height: int = 0
    fps: int = 60
    hide_mouse: bool = True


@dataclass
class SerialConfig:
    port: str = "auto"              # "auto" oder z.B. "/dev/ttyUSB0"
    baudrate: int = 115200
    reconnect_interval: float = 2.0


@dataclass
class GameConfig:
    count_up_time: float = 1.2      # Sekunden, in denen der Score hochzählt
    result_hold_time: float = 5.0   # Sekunden, die das Ergebnis stehen bleibt
    result_background: str = "frozen"  # "frozen" (gedimmtes Standbild) | "black"
    min_score: int = 1              # kleinere Werte werden ignoriert
    max_score: int = 999
    play_ready_overlay: bool = True # nach dem Ergebnis "Ready"-Overlay über dem Loop
    highscore_count: int = 10


@dataclass
class AssetConfig:
    idle_video: str = "videos/idle_loop.mp4"
    ready_overlay_video: str = "videos/ready_overlay.mp4"
    overlay_chroma_tolerance: int = 40
    font_digital: str = "fonts/dseg7-classic-latin-400-normal.ttf"
    font_arcade: str = "fonts/ARCADECLASSIC.TTF"

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else ASSETS_DIR / p


@dataclass
class Config:
    display: DisplayConfig = field(default_factory=DisplayConfig)
    serial: SerialConfig = field(default_factory=SerialConfig)
    game: GameConfig = field(default_factory=GameConfig)
    assets: AssetConfig = field(default_factory=AssetConfig)
    highscore_file: Path = PROJECT_DIR / "data" / "highscore.json"
    log_file: Path = PROJECT_DIR / "logs" / "boxautomat.log"


def _apply(section, values: dict, name: str) -> None:
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            log.warning("Unbekannte Einstellung [%s] %s – ignoriert", name, key)
            continue
        current = getattr(section, key)
        try:
            setattr(section, key, type(current)(value))
        except (TypeError, ValueError):
            log.warning("Ungültiger Wert für [%s] %s: %r", name, key, value)


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or PROJECT_DIR / "config.toml"
    if not path.exists():
        return cfg
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for name in ("display", "serial", "game", "assets"):
        if name in data:
            _apply(getattr(cfg, name), data[name], name)
    if "highscore_file" in data:
        p = Path(data["highscore_file"])
        cfg.highscore_file = p if p.is_absolute() else PROJECT_DIR / p
    return cfg
