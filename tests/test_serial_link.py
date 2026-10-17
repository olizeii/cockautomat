"""SerialLink gegen einen simulierten Arduino (Pseudo-Terminal) testen."""
import os
import time

from boxautomat.protocol import Connection, DeviceReady, Hit, SwitchState
from boxautomat.serial_link import SerialLink


def wait_for(link, n, timeout=3.0):
    events, end = [], time.time() + timeout
    while time.time() < end and len(events) < n:
        events += link.poll()
        time.sleep(0.02)
    return events


def test_reads_events_from_fake_arduino():
    master, slave = os.openpty()
    port = os.ttyname(slave)
    link = SerialLink(port=port, baudrate=115200, reconnect_interval=0.2)
    link.start()
    try:
        events = wait_for(link, 1)
        assert events and events[0] == Connection(True, port)
        os.write(master, b"READY 2.0\r\nS1\r\nR 123\r\nH 456 8800\r\n")
        # Zeile in zwei Teilen -> muss trotzdem korrekt zusammengesetzt werden
        os.write(master, b"H 7")
        time.sleep(0.1)
        os.write(master, b"8 3000\r\n")
        events = wait_for(link, 4)
        assert events == [DeviceReady("2.0"), SwitchState(True), Hit(456, 8800), Hit(78, 3000)]
    finally:
        link.stop()
        link.join(2)
        os.close(master)
        os.close(slave)
