from pathlib import Path

import pytest

from renderer.loudness import (
    LoudnessPoint,
    downsample_to_1hz,
    parse_ebur128_output,
    run_loudness_analysis,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
OUT_DIR = REPO_ROOT / "out"


def _fixture_text() -> str:
    return (FIXTURES_DIR / "ebur128_clip_a.txt").read_text()


def test_parse_ebur128_output_extracts_one_point_per_logged_frame():
    points = parse_ebur128_output(_fixture_text())
    assert len(points) == 80
    assert all(isinstance(p, LoudnessPoint) for p in points)


def test_parse_ebur128_output_reads_the_first_and_last_real_frame_values():
    points = parse_ebur128_output(_fixture_text())
    assert points[0].t == pytest.approx(0.0999375, abs=1e-4)
    assert points[0].level_db == pytest.approx(-120.7, abs=0.05)
    assert points[-1].t == pytest.approx(7.999938, abs=1e-4)
    assert points[-1].level_db == pytest.approx(-21.7, abs=0.05)


def test_parse_ebur128_output_is_unaffected_by_the_fixture_header_comment_and_banner():
    # the fixture file leads with a "# fixture captured..." comment block and
    # full ffmpeg startup banner/summary noise -- neither may leak a point
    points = parse_ebur128_output(_fixture_text())
    data_lines = [
        line
        for line in _fixture_text().splitlines()
        if "Parsed_ebur128" in line and " t: " in line
    ]
    assert len(points) == len(data_lines)


def test_parse_ebur128_output_empty_input_returns_no_points():
    assert parse_ebur128_output("") == []


def test_downsample_to_1hz_averages_dense_points_within_the_same_second():
    points = [
        LoudnessPoint(t=0.1, level_db=-10.0),
        LoudnessPoint(t=0.4, level_db=-20.0),
        LoudnessPoint(t=0.7, level_db=-30.0),
    ]
    result = downsample_to_1hz(points)
    assert len(result) == 1
    assert result[0].t == 0.0
    assert result[0].level_db == pytest.approx(-20.0)


def test_downsample_to_1hz_keeps_one_point_per_bucket_for_sparse_input():
    points = [
        LoudnessPoint(t=0.5, level_db=-10.0),
        LoudnessPoint(t=2.5, level_db=-20.0),
    ]
    result = downsample_to_1hz(points)
    assert [p.t for p in result] == [0.0, 2.0]
    assert result[0].level_db == pytest.approx(-10.0)
    assert result[1].level_db == pytest.approx(-20.0)


def test_downsample_to_1hz_buckets_by_floor_at_an_exact_integer_boundary():
    points = [LoudnessPoint(t=3.0, level_db=-5.0), LoudnessPoint(t=3.999, level_db=-15.0)]
    result = downsample_to_1hz(points)
    assert len(result) == 1
    assert result[0].t == 3.0
    assert result[0].level_db == pytest.approx(-10.0)


def test_downsample_to_1hz_empty_input_returns_empty_list():
    assert downsample_to_1hz([]) == []


def test_downsample_to_1hz_output_is_sorted_by_time_regardless_of_input_order():
    points = [
        LoudnessPoint(t=5.2, level_db=-1.0),
        LoudnessPoint(t=0.1, level_db=-2.0),
        LoudnessPoint(t=2.9, level_db=-3.0),
    ]
    result = downsample_to_1hz(points)
    assert [p.t for p in result] == sorted(p.t for p in result)
    assert [p.t for p in result] == [0.0, 2.0, 5.0]


@pytest.mark.media
def test_run_loudness_analysis_produces_at_least_one_point_on_the_real_sample_clip():
    from renderer.probe import extract_audio_flac

    flac_path = extract_audio_flac(SAMPLE_DIR / "clip_a.mp4", OUT_DIR / "loudness_clip_a.flac")
    points = run_loudness_analysis(flac_path)
    assert len(points) >= 1
    assert all(isinstance(p, LoudnessPoint) for p in points)
    assert all(p.t >= 0 for p in points)
