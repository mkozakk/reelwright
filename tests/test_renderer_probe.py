from pathlib import Path

import pytest

from renderer.probe import (
    MAX_DURATION_SECONDS,
    MAX_FILE_BYTES,
    MAX_PIXEL_RATE,
    ProbeResult,
    extract_audio_flac,
    probe_file,
    validate_probe,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def _ok_result() -> ProbeResult:
    return ProbeResult(duration=8.0, width=1280, height=720, fps=30.0, video_codec="h264", audio_codec="aac")


def test_validate_probe_accepts_in_bounds_result():
    assert validate_probe(_ok_result(), file_size_bytes=1_000_000) == []


def test_validate_probe_rejects_oversized_file():
    errors = validate_probe(_ok_result(), file_size_bytes=MAX_FILE_BYTES + 1)
    assert any("file size" in e for e in errors)


def test_validate_probe_rejects_too_long_duration():
    result = ProbeResult(duration=MAX_DURATION_SECONDS + 1, width=1280, height=720, fps=30.0, video_codec="h264", audio_codec="aac")
    errors = validate_probe(result, file_size_bytes=1_000)
    assert any("duration" in e for e in errors)


def test_validate_probe_rejects_decode_bomb_pixel_rate():
    # small file, short duration, but absurd resolution/fps -> pixel-rate cap catches it
    result = ProbeResult(duration=1.0, width=7680, height=4320, fps=120.0, video_codec="h264", audio_codec="aac")
    assert result.width * result.height * result.fps > MAX_PIXEL_RATE
    errors = validate_probe(result, file_size_bytes=1_000)
    assert any("pixel rate" in e for e in errors)


def test_validate_probe_rejects_unsupported_codecs():
    result = ProbeResult(duration=1.0, width=1280, height=720, fps=30.0, video_codec="mpeg2video", audio_codec="mp3")
    errors = validate_probe(result, file_size_bytes=1_000)
    assert any("video codec" in e for e in errors)
    assert any("audio codec" in e for e in errors)


@pytest.mark.media
def test_probe_file_reads_real_sample_clip():
    result = probe_file(SAMPLE_DIR / "clip_a.mp4")
    assert result.width == 1280
    assert result.height == 720
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert 7.5 < result.duration < 8.5
    assert validate_probe(result, file_size_bytes=(SAMPLE_DIR / "clip_a.mp4").stat().st_size) == []


@pytest.mark.media
def test_extract_audio_flac_produces_a_real_file():
    output = OUT_DIR / "clip_a_audio.flac"
    result = extract_audio_flac(SAMPLE_DIR / "clip_a.mp4", output)
    assert result == output
    assert output.exists()
    assert output.stat().st_size > 0
