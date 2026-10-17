"""Hintergrund-Thread für die USB-Verbindung zum Arduino.

Liest Zeilen, wandelt sie in Events um und legt sie in eine Queue.
Wird das Kabel abgezogen oder der Arduino resettet, verbindet sich der
Thread selbstständig neu – die GUI läuft dabei einfach weiter.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

import serial
from serial.tools import list_ports

from .protocol import Connection, DeviceReady, Event, parse_line

log = logging.getLogger(__name__)

# USB-IDs typischer Arduino-Nano-Varianten (Original, CH340-Klon, FTDI, CP210x)
KNOWN_USB_IDS = {
    (0x2341, None),   # Arduino SA
    (0x2A03, None),   # Arduino.org
    (0x1A86, 0x7523), # CH340 (die meisten Nano-Klone)
    (0x1A86, 0x55D4), # CH9102
    (0x0403, 0x6001), # FTDI FT232
    (0x10C4, 0xEA60), # CP210x
}


def candidate_ports() -> list[str]:
    """Serielle Ports, sortiert: bekannte Arduino-Chips zuerst."""
    preferred, other = [], []
    for p in list_ports.comports():
        if p.vid is not None and any(
            vid == p.vid and (pid is None or pid == p.pid) for vid, pid in KNOWN_USB_IDS
        ):
            preferred.append(p.device)
        elif "ttyUSB" in p.device or "ttyACM" in p.device:
            other.append(p.device)
    return preferred + other


class SerialLink(threading.Thread):
    def __init__(self, port: str = "auto", baudrate: int = 115200,
                 reconnect_interval: float = 2.0):
        super().__init__(name="SerialLink", daemon=True)
        self.port_setting = port
        self.baudrate = baudrate
        self.reconnect_interval = reconnect_interval
        self.events: "queue.Queue[Event]" = queue.Queue()
        self._stop = threading.Event()
        self._ser: serial.Serial | None = None
        self.connected_port: str | None = None

    # -- öffentliche API -----------------------------------------------------
    def poll(self) -> list[Event]:
        """Alle seit dem letzten Aufruf eingetroffenen Events (nicht blockierend)."""
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def send(self, text: str) -> None:
        ser = self._ser
        if ser:
            try:
                ser.write(text.encode())
            except (serial.SerialException, OSError):
                pass

    def stop(self) -> None:
        self._stop.set()

    # -- Thread --------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            if not self._connect():
                self._stop.wait(self.reconnect_interval)
                continue
            self._read_loop()
            self._disconnect()
            if not self._stop.is_set():
                self._stop.wait(self.reconnect_interval)
        self._disconnect()

    def _connect(self) -> bool:
        ports = candidate_ports() if self.port_setting == "auto" else [self.port_setting]
        for dev in ports:
            try:
                ser = serial.Serial(dev, self.baudrate, timeout=0.2)
            except (serial.SerialException, OSError) as e:
                log.debug("Port %s nicht nutzbar: %s", dev, e)
                continue
            self._ser = ser
            self.connected_port = dev
            log.info("Verbunden mit %s (%d Baud)", dev, self.baudrate)
            self.events.put(Connection(True, dev))
            return True
        return False

    def _disconnect(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
            log.warning("Verbindung zu %s getrennt", self.connected_port)
            self.events.put(Connection(False, self.connected_port))
            self.connected_port = None

    def _read_loop(self) -> None:
        assert self._ser is not None
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(256)
            except (serial.SerialException, OSError) as e:
                # z.B. "[Errno 5] Input/output error" bei Wackelkontakt
                log.warning("Lesefehler: %s", e)
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                log.debug("Arduino: %s", line)
                event = parse_line(line)
                if event is None:
                    continue
                if isinstance(event, DeviceReady):
                    log.info("Arduino-Firmware %s bereit", event.version)
                self.events.put(event)
            if len(buf) > 4096:   # Müll ohne Zeilenende verwerfen
                buf = b""


class NullLink:
    """Ersatz, wenn ohne Arduino gestartet wird (--no-serial)."""
    connected_port = None

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def send(self, text: str) -> None: ...
    def poll(self) -> list[Event]:
        return []
    def join(self, timeout: float | None = None) -> None: ...
