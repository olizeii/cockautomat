"""Video-Wiedergabe mit OpenCV, Ausgabe als pygame-Surface.

VideoPlayer      – Hintergrundvideo, läuft in Endlosschleife, kann pausiert werden.
OverlayPlayer    – einmaliges Overlay; Schwarz wird transparent (Chroma-Key).
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import pygame

log = logging.getLogger(__name__)


def _fit_size(src: tuple[int, int], dst: tuple[int, int], fit: str) -> tuple[int, int]:
    """cover: füllt den Bildschirm (Ränder werden abgeschnitten).
    contain: ganzes Video sichtbar, Rest bleibt Hintergrund."""
    sw, sh = src
    dw, dh = dst
    pick = min if fit == "contain" else max
    scale = pick(dw / sw, dh / sh)
    return max(1, round(sw * scale)), max(1, round(sh * scale))


class _BasePlayer:
    def __init__(self, path: Path, target_size: tuple[int, int], loop: bool, fit: str = "cover"):
        self.path = Path(path)
        self.target_size = target_size
        self.fit = fit
        self.offset = (0, 0)    # Position des Bildes bei fit = "contain"
        self.loop = loop
        self.cap: cv2.VideoCapture | None = None
        self.fps = 30.0
        self.frame: pygame.Surface | None = None
        self.paused = False
        self.finished = False
        self._clock = 0.0       # Videozeit in Sekunden
        self._frame_index = -1
        self._open()

    @property
    def available(self) -> bool:
        return self.cap is not None

    def _open(self) -> None:
        if not self.path.exists():
            log.error("Video fehlt: %s", self.path)
            return
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            log.error("Video kann nicht geöffnet werden: %s", self.path)
            return
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if fps and fps > 1 else 30.0
        self.cap = cap

    def rewind(self) -> None:
        if self.cap:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self._clock = 0.0
        self._frame_index = -1
        self.finished = False

    def update(self, dt: float) -> None:
        """Video um dt Sekunden weiterlaufen lassen (holt nur nötige Frames)."""
        if not self.cap or self.paused or self.finished:
            return
        self._clock += min(dt, 0.25)   # nach Hängern nicht wild vorspulen
        wanted = int(self._clock * self.fps)
        frame = None
        while self._frame_index < wanted:
            ok, img = self.cap.read()
            if not ok:
                if self.loop:
                    self.rewind()
                    ok, img = self.cap.read()
                    if not ok:
                        return
                    wanted = 0
                else:
                    self.finished = True
                    return
            self._frame_index += 1
            frame = img
        if frame is not None:
            self.frame = self._to_surface(frame)

    def _resize(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        nw, nh = _fit_size((w, h), self.target_size, self.fit)
        if (nw, nh) != (w, h):
            img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        tw, th = self.target_size
        if self.fit == "contain":
            self.offset = ((tw - nw) // 2, (th - nh) // 2)
            return img
        # mittig auf Zielgröße zuschneiden
        x0, y0 = (nw - tw) // 2, (nh - th) // 2
        return img[y0:y0 + th, x0:x0 + tw]

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """Bereich, den das Video auf dem Bildschirm einnimmt (x, y, w, h)."""
        if self.frame is None:
            return (0, 0, *self.target_size)
        return (*self.offset, *self.frame.get_size())

    def _to_surface(self, img: np.ndarray) -> pygame.Surface:
        raise NotImplementedError

    def draw(self, screen: pygame.Surface) -> None:
        if self.frame is not None:
            screen.blit(self.frame, self.offset)

    def close(self) -> None:
        if self.cap:
            self.cap.release()
            self.cap = None


class VideoPlayer(_BasePlayer):
    def __init__(self, path: Path, target_size: tuple[int, int], fit: str = "cover"):
        super().__init__(path, target_size, loop=True, fit=fit)
        self.update(1 / self.fps)  # erstes Bild sofort laden

    def _to_surface(self, img: np.ndarray) -> pygame.Surface:
        rgb = cv2.cvtColor(self._resize(img), cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        return pygame.image.frombuffer(rgb.tobytes(), (w, h), "RGB").convert()


class OverlayPlayer(_BasePlayer):
    def __init__(self, path: Path, target_size: tuple[int, int], tolerance: int = 40,
                 fit: str = "cover"):
        super().__init__(path, target_size, loop=False, fit=fit)
        self.tolerance = tolerance
        self.finished = True   # spielt erst nach start()

    def start(self) -> None:
        if self.cap:
            self.rewind()
            self.frame = None

    @property
    def playing(self) -> bool:
        return self.cap is not None and not self.finished

    def _to_surface(self, img: np.ndarray) -> pygame.Surface:
        img = self._resize(img)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # Helligkeit -> Deckkraft: Schwarz wird durchsichtig, weiche Kante
        lum = img.max(axis=2).astype(np.float32)
        tol = float(self.tolerance)
        alpha = np.clip((lum - tol) * (255.0 / max(tol, 1.0)), 0, 255).astype(np.uint8)
        rgba = np.dstack((rgb, alpha))
        h, w = rgba.shape[:2]
        return pygame.image.frombuffer(rgba.tobytes(), (w, h), "RGBA").convert_alpha()

    def draw(self, screen: pygame.Surface) -> None:
        if self.playing:
            super().draw(screen)
