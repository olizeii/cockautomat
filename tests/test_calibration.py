from boxautomat.calibration import Calibration, demo_events
from boxautomat.protocol import (CalAbort, CalBegin, CalDone, CalHit, CalInfo, CalLive, CalReject,
                                 CalRest, CalState, Connection, Hit, SensorConfig, parse_line)


def test_parse_calibration_lines():
    assert parse_line("CAL BEGIN switch") == CalBegin("switch")
    assert parse_line("CAL STATE LIGHT 2 3") == CalState("LIGHT", 2, 3)
    assert parse_line("CAL REST 48") == CalRest(48)
    assert parse_line("CAL HIT HARD 1 15990 1") == CalHit("HARD", 1, 15990, True)
    assert parse_line("CAL REJECT WEAK") == CalReject("WEAK")
    assert parse_line("CAL LIVE 812") == CalLive(812)
    assert parse_line("CAL INFO 998 274 12 -990 85") == CalInfo(998, 274, (12, -990, 85))
    assert parse_line("CAL DONE 532 15080 0") == CalDone(532, 15080, 0)
    assert parse_line("CAL ABORT") == CalAbort()
    assert parse_line("CFG 2000 15500 default") == SensorConfig(2000, 15500, "default")
    assert parse_line("H 512 9340 1") == Hit(512, 9340, True)


def test_broken_calibration_lines_ignored():
    for line in ("CAL", "CAL STATE FOO 1 3", "CAL HIT LIGHT x 3", "CAL INFO 1 2", "CAL WHAT",
                 "CFG 12"):
        assert parse_line(line) is None


def test_demo_run_ends_with_saved_values():
    cal = Calibration()
    cal.apply(SensorConfig(2000, 15500, "default"), 0.0)
    for t, ev in demo_events(0.0):
        cal.apply(ev, t)
    assert not cal.active and not cal.aborted
    assert [mg for mg, _ in cal.light] == [820, 960, 760]
    assert len(cal.hard) == 3                      # der zu schwache Schlag zählt nicht
    assert cal.result.trigger_mg == 532
    assert cal.old_config == SensorConfig(2000, 15500, "default")
    assert cal.visible(16.0) and not cal.visible(40.0)


def test_clipping_warning():
    cal = Calibration()
    cal.apply(CalBegin("command"), 0.0)
    cal.apply(CalHit("HARD", 1, 15900, True), 1.0)
    assert cal.clipped


def test_near_limit_warning():
    cal = Calibration()
    cal.apply(CalBegin("command"), 0.0)
    for i, mg in enumerate((12500, 13000, 12800), 1):
        cal.apply(CalHit("HARD", i, mg, False), float(i))
    assert not cal.clipped and cal.near_limit


def test_disconnect_aborts_and_late_join_starts():
    cal = Calibration()
    cal.apply(CalState("LIGHT", 1, 3), 0.0)       # App verbindet sich mitten hinein
    assert cal.active and cal.step == "LIGHT"
    cal.apply(Connection(False, "/dev/ttyUSB0"), 1.0)
    assert not cal.active and cal.aborted


def test_live_window_is_trimmed():
    cal = Calibration()
    cal.apply(CalBegin("first"), 0.0)
    for i in range(400):
        cal.apply(CalLive(i), i * 0.04)
    assert all(399 * 0.04 - t <= 6.0 for t, _ in cal.live)
    assert cal.live_mg == 399
