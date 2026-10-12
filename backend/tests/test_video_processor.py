from app.services.video_processor import format_timestamp, probe, sample_frames

from .fakes import RED, make_video


def test_probe_and_sampling(tmp_path):
    path = make_video(tmp_path / "v.mp4", [(0, 10, RED)], duration_s=4, fps=10)
    info = probe(path)
    assert info.fps == 10 and info.frame_count == 40 and (info.width, info.height) == (320, 240)
    frames = list(sample_frames(path, sample_fps=2))
    assert [f.index for f in frames] == list(range(0, 40, 5))
    assert frames[1].timestamp_s == 0.5


def test_format_timestamp():
    assert format_timestamp(3725.5) == "01:02:05.500"
