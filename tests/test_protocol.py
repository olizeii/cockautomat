from boxautomat.protocol import DeviceMessage, DeviceReady, Hit, SwitchState, parse_line


def test_hit_new_protocol():
    assert parse_line("H 512 9340\r\n") == Hit(512, 9340)


def test_hit_without_peak():
    assert parse_line("H 77") == Hit(77, None)


def test_legacy_plain_number():
    assert parse_line("845") == Hit(845)


def test_switch():
    assert parse_line("S1") == SwitchState(True)
    assert parse_line("S0") == SwitchState(False)


def test_ready_and_messages():
    assert parse_line("READY 2.0") == DeviceReady("2.0")
    assert parse_line("ERR MPU6050 nicht gefunden") == DeviceMessage("ERR", "MPU6050 nicht gefunden")


def test_garbage_and_raw_stream_ignored():
    for line in ("", "   ", "R 1234", "H", "H abc", "\x00\xffxx", "hello"):
        assert parse_line(line) is None


def test_example_config_loads_cleanly(caplog):
    from boxautomat.config import PROJECT_DIR, Config, load_config
    cfg = load_config(PROJECT_DIR / "config.example.toml")
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    assert cfg.display == Config().display and cfg.game == Config().game
