"""Hauptprogramm: Fenster öffnen, Events verarbeiten, Szenen zeichnen."""
from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time
from logging.handlers import RotatingFileHandler

from .calibration import Calibration, demo_events
from .config import Config, load_config
from .game import Game, State
from .highscores import Highscores
from .protocol import (CalAbort, CalBegin, CalDone, CalHit, CalReject, CalRest, Connection,
                       DeviceMessage, DeviceReady, Hit, SensorConfig, SwitchState)

log = logging.getLogger("boxautomat")

WINDOW_TITLE = "Boxautomat Retro Display"


def setup_logging(cfg: Config, debug: bool) -> None:
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(cfg.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    # Nur auf die Konsole schreiben, wenn es eine gibt (Start per .desktop hat keine!)
    if sys.stderr is not None and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )

    def excepthook(exc_type, exc, tb):
        log.critical("Unbehandelter Fehler", exc_info=(exc_type, exc, tb))
    sys.excepthook = excepthook


def open_window(cfg: Config):
    import pygame

    d = cfg.display
    pygame.display.init()
    num = pygame.display.get_num_displays()
    idx = d.display_index if 0 <= d.display_index < num else 0
    if idx != d.display_index:
        log.warning("Bildschirm %d nicht gefunden (%d vorhanden) – nutze %d",
                    d.display_index, num, idx)
    desktop = pygame.display.get_desktop_sizes()
    w = d.width or desktop[idx][0]
    h = d.height or desktop[idx][1]

    if d.mode == "fullscreen":
        flags = pygame.FULLSCREEN
    elif d.mode == "borderless":
        flags = pygame.NOFRAME
        # Fenster auf den gewünschten Bildschirm schieben (Bildschirme nebeneinander)
        x = sum(s[0] for s in desktop[:idx])
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{x},0"
        pygame.display.quit()
        pygame.display.init()
    else:
        flags = 0
        w, h = d.width or 1280, d.height or 720

    screen = pygame.display.set_mode((w, h), flags, display=idx)
    pygame.display.set_caption(WINDOW_TITLE)
    if d.hide_mouse and d.mode != "windowed":
        pygame.mouse.set_visible(False)
    log.info("Fenster %dx%d auf Bildschirm %d (%s)", *screen.get_size(), idx, d.mode)
    return screen


def run(cfg: Config, use_serial: bool = True, max_frames: int | None = None,
        sim_hits: list[tuple[int, int]] | None = None, sim_calibration: bool = False) -> None:
    """Startet die App. max_frames/sim_hits/sim_calibration sind für automatische Tests."""
    import pygame

    from .serial_link import NullLink, SerialLink
    from .ui import BG, Renderer
    from .video import OverlayPlayer, VideoPlayer

    pygame.font.init()      # kein pygame.init(): Audio wird nicht gebraucht
    screen = open_window(cfg)
    size = screen.get_size()
    clock = pygame.time.Clock()

    highscores = Highscores(cfg.highscore_file, cfg.game.highscore_count)
    game = Game(cfg.game, highscores)
    a = cfg.assets
    fit = cfg.display.video_fit
    video = VideoPlayer(a.path(a.idle_video), size, fit)
    overlay = OverlayPlayer(a.path(a.ready_overlay_video), size, a.overlay_chroma_tolerance, fit)
    renderer = Renderer(size, a.path(a.font_digital), a.path(a.font_arcade), cfg.game.max_score,
                        crt=cfg.display.crt_effect)

    link = (SerialLink(cfg.serial.port, cfg.serial.baudrate, cfg.serial.reconnect_interval)
            if use_serial else NullLink())
    link.start()
    connected = False
    sim_hits = sorted(sim_hits or [])
    cal = Calibration()
    demo: list = demo_events(time.monotonic()) if sim_calibration else []

    frame = 0
    running = True
    prev_state = game.state
    try:
        while running:
            dt = clock.tick(cfg.display.fps) / 1000.0
            now = time.monotonic()

            # ---- Eingaben -----------------------------------------------------
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    elif event.key == pygame.K_SPACE:      # Test-Schlag ohne Arduino
                        game.on_hit(random.randint(100, cfg.game.max_score), now)
                    elif pygame.K_1 <= event.key <= pygame.K_9:
                        game.on_hit((event.key - pygame.K_0) * 111, now)
                    elif event.key == pygame.K_TAB:        # Scoreboard umschalten
                        game.on_switch(not game.scoreboard_switch, now)
                    elif event.key == pygame.K_k:          # Kalibrierung starten / abbrechen
                        if use_serial:
                            link.send("x" if cal.active else "k")
                        elif cal.active:                   # ohne Arduino: Demo-Ablauf
                            demo.clear()
                            cal.apply(CalAbort(), now)
                        else:
                            demo = demo_events(now)

            events = link.poll()
            while demo and demo[0][0] <= now:
                events.append(demo.pop(0)[1])
            for ev in events:
                if cal.apply(ev, now):
                    if isinstance(ev, (CalBegin, CalRest, CalHit, CalReject, CalDone, CalAbort)):
                        log.info("Kalibrierung: %s", ev)
                    continue
                if isinstance(ev, Hit):
                    if cal.visible(now):       # Schlag beendet die Ergebnisanzeige
                        cal.dismiss()
                    accepted = game.on_hit(ev.score, now)
                    log.info("Schlag: %d Punkte (Peak %s mg)%s", ev.score, ev.peak_mg,
                             "" if accepted else " – ignoriert")
                    if ev.clipped:
                        log.warning("Schlag übersteuert (16 g erreicht) – Sensor näher ans "
                                    "Scharnier setzen oder neu kalibrieren")
                elif isinstance(ev, SensorConfig):
                    log.info("Sensor: Schwelle %.2f g, 999 Punkte bei %.2f g (%s)",
                             ev.trigger_mg / 1000, ev.max_mg / 1000, ev.source)
                elif isinstance(ev, DeviceReady):
                    cal.firmware = ev.version
                elif isinstance(ev, SwitchState):
                    game.on_switch(ev.on, now)
                elif isinstance(ev, Connection):
                    connected = ev.connected
                    if connected:   # ESP32 mit nativem USB startet beim Verbinden nicht neu
                        link.send("?")
                elif isinstance(ev, DeviceMessage):
                    (log.error if ev.level == "ERR" else log.info)("Arduino: %s", ev.text)

            while sim_hits and frame >= sim_hits[0][0]:
                game.on_hit(sim_hits.pop(0)[1], now)

            game.update(now)

            # ---- Zustandswechsel ---------------------------------------------
            if game.state != prev_state:
                if game.state == State.COUNTING:
                    video.paused = True
                    overlay.finished = True
                    if prev_state == State.IDLE:   # bei Schlag während Ergebnis: Standbild behalten
                        # nur das Videobild einfrieren, ohne HUD-Texte
                        screen.fill(BG)
                        video.draw(screen)
                        renderer.freeze(screen, cfg.game.result_background == "black")
                elif game.state == State.IDLE:
                    video.paused = False
                    if game.just_returned_to_idle and cfg.game.play_ready_overlay:
                        overlay.start()
                    game.just_returned_to_idle = False
                log.debug("Zustand %s -> %s", prev_state.name, game.state.name)
                prev_state = game.state

            # ---- Zeichnen ----------------------------------------------------
            if cal.visible(now):
                renderer.draw_calibration(screen, cal, now, link.connected_port)
            elif game.state == State.IDLE:
                video.update(dt)
                overlay.update(dt)
                if fit == "contain":
                    screen.fill(BG)
                video.draw(screen)
                overlay.draw(screen)
                renderer.draw_idle_hud(screen, video.rect, highscores,
                                       connected or not use_serial, now,
                                       bag_up=connected and cal.bag_down is False)
            elif game.state in (State.COUNTING, State.RESULT):
                renderer.draw_result(screen, game, highscores.best, now)
            else:
                renderer.draw_scoreboard(screen, highscores, now, game.state_since,
                                         highlight=game.rank if game.score else None)

            renderer.post(screen)
            pygame.display.flip()
            frame += 1
            if max_frames is not None and frame >= max_frames:
                running = False
    finally:
        link.stop()
        video.close()
        overlay.close()
        pygame.quit()
        log.info("Beendet")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="boxautomat", description="Boxautomat – Punch Machine GUI")
    p.add_argument("--config", help="Pfad zur config.toml")
    p.add_argument("--windowed", action="store_true", help="im Fenster statt Vollbild starten")
    p.add_argument("--display", type=int, help="Bildschirm-Nummer (0 = Haupt, 1 = Zweit)")
    p.add_argument("--port", help="serieller Port, z.B. /dev/ttyUSB0 (Standard: auto)")
    p.add_argument("--no-serial", action="store_true", help="ohne Arduino starten (Leertaste = Schlag)")
    p.add_argument("--debug", action="store_true", help="ausführliches Log inkl. aller Arduino-Zeilen")
    args = p.parse_args(argv)

    from pathlib import Path
    cfg = load_config(Path(args.config) if args.config else None)
    if args.windowed:
        cfg.display.mode = "windowed"
    if args.display is not None:
        cfg.display.display_index = args.display
    if args.port:
        cfg.serial.port = args.port

    setup_logging(cfg, args.debug)
    log.info("Boxautomat startet")
    run(cfg, use_serial=not args.no_serial)
