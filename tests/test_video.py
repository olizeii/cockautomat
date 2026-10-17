from boxautomat.video import _fit_size


def test_contain_keeps_whole_video_visible():
    # 16:9-Video auf 5:4-Monitor: volle Breite, Rand oben/unten
    assert _fit_size((848, 478), (1280, 1024), "contain") == (1280, 722)


def test_cover_fills_screen():
    w, h = _fit_size((848, 478), (1280, 1024), "cover")
    assert h == 1024 and w > 1280
