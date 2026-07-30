from pathlib import Path

import pytest

from renderer.probe import (
    MAX_DURATION_SECONDS,
    MAX_FILE_BYTES,
    MAX_PIXEL_RATE,
    ProbeResult,
    extract_audio_asset,
    extract_audio_flac,
    probe_file,
    validate_probe,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def _ok_result() -> ProbeResult:
    return ProbeResult(
        kind="video", duration=8.0, width=1280, height=720, fps=30.0, video_codec="h264", audio_codec="aac"
    )


def test_validate_probe_accepts_in_bounds_result():
    assert validate_probe(_ok_result(), file_size_bytes=1_000_000) == []


def test_validate_probe_accepts_mp3_audio_codec():
    # ADR-4 (docs/phases/phase-8.md): mp3 accepted alongside aac/opus
    result = ProbeResult(
        kind="video", duration=8.0, width=1280, height=720, fps=30.0, video_codec="h264", audio_codec="mp3"
    )
    assert validate_probe(result, file_size_bytes=1_000_000) == []


def test_validate_probe_rejects_oversized_file():
    errors = validate_probe(_ok_result(), file_size_bytes=MAX_FILE_BYTES + 1)
    assert any("file size" in e for e in errors)


def test_validate_probe_rejects_too_long_duration():
    result = ProbeResult(
        kind="video",
        duration=MAX_DURATION_SECONDS + 1,
        width=1280,
        height=720,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
    )
    errors = validate_probe(result, file_size_bytes=1_000)
    assert any("duration" in e for e in errors)


def test_validate_probe_rejects_decode_bomb_pixel_rate():
    # small file, short duration, but absurd resolution/fps -> pixel-rate cap catches it
    result = ProbeResult(
        kind="video", duration=1.0, width=7680, height=4320, fps=120.0, video_codec="h264", audio_codec="aac"
    )
    assert result.width * result.height * result.fps > MAX_PIXEL_RATE
    errors = validate_probe(result, file_size_bytes=1_000)
    assert any("pixel rate" in e for e in errors)


def test_validate_probe_rejects_unsupported_codecs():
    result = ProbeResult(
        kind="video",
        duration=1.0,
        width=1280,
        height=720,
        fps=30.0,
        video_codec="mpeg2video",
        audio_codec="vorbis",
    )
    errors = validate_probe(result, file_size_bytes=1_000)
    assert any("video codec" in e for e in errors)
    assert any("audio codec" in e for e in errors)


def test_validate_probe_skips_video_only_checks_for_audio_kind():
    # an audio-only source has no video codec/pixel-rate to check
    result = ProbeResult(
        kind="audio", duration=180.0, width=0, height=0, fps=0.0, video_codec="", audio_codec="aac"
    )
    assert validate_probe(result, file_size_bytes=1_000_000) == []


def test_validate_probe_rejects_audio_kind_with_no_audio_stream():
    result = ProbeResult(kind="audio", duration=10.0, width=0, height=0, fps=0.0, video_codec="", audio_codec=None)
    errors = validate_probe(result, file_size_bytes=1_000)
    assert any("no usable audio stream" in e for e in errors)


def test_validate_probe_rejects_declared_kind_mismatch():
    errors = validate_probe(_ok_result(), file_size_bytes=1_000_000, declared_kind="audio")
    assert any("declared kind" in e for e in errors)


def test_validate_probe_accepts_matching_declared_kind():
    errors = validate_probe(_ok_result(), file_size_bytes=1_000_000, declared_kind="video")
    assert errors == []


@pytest.mark.media
def test_probe_file_reads_real_sample_clip():
    result = probe_file(SAMPLE_DIR / "clip_a.mp4")
    assert result.kind == "video"
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


@pytest.mark.media
def test_extract_audio_asset_produces_a_48khz_stereo_file():
    output = OUT_DIR / "clip_a_asset.flac"
    result = extract_audio_asset(SAMPLE_DIR / "clip_a.mp4", output)
    assert result == output
    assert output.exists()
    assert output.stat().st_size > 0
