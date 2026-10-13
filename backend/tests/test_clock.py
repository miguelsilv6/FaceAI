from datetime import datetime

import pytest

from app.services.clock import ClockSample, fit_clock, normalize_ocr, parse_datetime, real_time


@pytest.mark.parametrize("text,order,expected", [
    ("12-10-2026 Seg 14:03:27", "DMY", datetime(2026, 10, 12, 14, 3, 27)),
    ("2026/10/12 14:03:27", "DMY", datetime(2026, 10, 12, 14, 3, 27)),
    ("10/12/2026 02:03:27 PM", "MDY", datetime(2026, 10, 12, 14, 3, 27)),
    ("10/12/2026 12:03:27 AM", "MDY", datetime(2026, 10, 12, 0, 3, 27)),
    ("25.12.26 23:59:59", "MDY", datetime(2026, 12, 25, 23, 59, 59)),  # 25 não é mês: DMY forçado
    ("CAM1 12 Out 2026 14:03:27", "DMY", datetime(2026, 10, 12, 14, 3, 27)),
    ("2026-1O-12 l4:O3:27", "DMY", datetime(2026, 10, 12, 14, 3, 27)),  # confusões de OCR
    ("14:03:27 2026-10-12", "DMY", datetime(2026, 10, 12, 14, 3, 27)),
])
def test_parse_datetime(text, order, expected):
    assert parse_datetime(text, order) == expected


@pytest.mark.parametrize("text", ["CAM 03 ENTRADA", "14:03:27", "2026-13-45 10:00:00", ""])
def test_parse_datetime_rejects(text):
    assert parse_datetime(text) is None


def test_normalize_keeps_letters_in_words():
    assert normalize_ocr("Out 1O:2O") == "Out 10:20"


def _s(t, dvr):
    return ClockSample(video_s=t, text="", dvr=dvr)


def test_fit_constant_offset_with_outlier():
    base = datetime(2026, 10, 12, 14, 0, 0)
    from datetime import timedelta
    samples = [_s(t, base + timedelta(seconds=int(t + 7.3))) for t in (0, 10, 20, 30, 40)]
    samples.append(_s(25, base + timedelta(hours=3)))  # leitura errada
    samples.append(_s(35, None))                        # sem leitura
    fit = fit_clock(samples)
    assert 5 not in fit.inliers and len(fit.inliers) == 5
    assert abs((fit.start - base).total_seconds() - 7.3) <= 0.7
    assert fit.rate == 1.0


def test_fit_estimates_rate_for_wrong_fps():
    """Vídeo exportado a 25 fps nominais mas gravado a 12,5 fps: 1 s de vídeo = 2 s de relógio."""
    from datetime import timedelta
    base = datetime(2026, 10, 12, 8, 0, 0)
    samples = [_s(t, base + timedelta(seconds=int(t * 1.05))) for t in range(0, 600, 50)]
    fit = fit_clock(samples)
    assert abs(fit.rate - 1.05) < 0.005
    assert abs((fit.at(500) - (base + timedelta(seconds=525))).total_seconds()) <= 1.0


def test_fit_rejects_inconsistent_readings():
    from datetime import timedelta
    base = datetime(2026, 1, 1)
    with pytest.raises(ValueError):
        fit_clock([_s(0, base), _s(10, base + timedelta(hours=1)), _s(20, base + timedelta(hours=5))])
    with pytest.raises(ValueError):
        fit_clock([_s(0, base), _s(10, None)])


def test_real_time():
    start = datetime(2026, 10, 12, 14, 0, 0)
    assert real_time(start, 1.0, -180, 65.5) == datetime(2026, 10, 12, 13, 58, 5, 500000)
    assert real_time(None, 1.0, 0, 10) is None
