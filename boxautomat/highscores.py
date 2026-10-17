"""Highscore-Liste als JSON (kompatibel mit der alten highscore.json)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)


class Highscores:
    def __init__(self, path: Path, size: int = 10):
        self.path = Path(path)
        self.size = size
        self.entries: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as e:
            log.error("Highscores konnten nicht gelesen werden (%s) – starte leer", e)
            return []
        if not isinstance(data, list):
            return []
        entries = [
            {"score": int(e["score"]), "date": str(e.get("date", "---"))}
            for e in data
            if isinstance(e, dict) and isinstance(e.get("score"), (int, float)) and e["score"] > 0
        ]
        return sorted(entries, key=lambda e: e["score"], reverse=True)[: self.size]

    @property
    def best(self) -> int:
        return self.entries[0]["score"] if self.entries else 0

    def rank_for(self, score: int) -> int | None:
        """Platz (1-basiert), den der Score bekäme, oder None wenn nicht in der Liste."""
        rank = 1 + sum(1 for e in self.entries if e["score"] >= score)
        return rank if rank <= self.size else None

    def add(self, score: int) -> int | None:
        """Trägt den Score ein, speichert und gibt den Platz zurück."""
        rank = self.rank_for(score)
        if rank is None:
            return None
        self.entries.insert(rank - 1, {"score": int(score), "date": time.strftime("%d.%m.%Y")})
        self.entries = self.entries[: self.size]
        self._save()
        return rank

    def _save(self) -> None:
        # atomar schreiben: Stromausfall darf die Datei nicht zerstören
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            log.error("Highscores konnten nicht gespeichert werden: %s", e)
