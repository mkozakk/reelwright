from pathlib import Path

import pytest

from renderer.scenes import SceneCut, parse_scdet_output, run_scene_analysis

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


def _fixture_text() -> str:
    return (FIXTURES_DIR / "scdet_clip_a.txt").read_text()


def test_parse_scdet_output_extracts_one_cut_per_logged_line():
    cuts = parse_scdet_output(_fixture_text())
    assert len(cuts) == 89
    assert all(isinstance(c, SceneCut) for c in cuts)


def test_parse_scdet_output_reads_the_first_and_last_real_cut_values():
    cuts = parse_scdet_output(_fixture_text())
    assert cuts[0].t == pytest.approx(0.0333333, abs=1e-4)
    assert cuts[0].score == pytest.approx(0.944, abs=1e-3)
    assert cuts[-1].t == pytest.approx(7.966667, abs=1e-4)
    assert cuts[-1].score == pytest.approx(0.076, abs=1e-3)


def test_parse_scdet_output_is_unaffected_by_the_fixture_header_comment_and_banner():
    cuts = parse_scdet_output(_fixture_text())
    data_lines = [line for line in _fixture_text().splitlines() if line.startswith("[scdet @")]
    assert len(cuts) == len(data_lines)


def test_parse_scdet_output_empty_input_returns_no_cuts():
    assert parse_scdet_output("") == []


def test_parse_scdet_output_cuts_are_in_ascending_time_order_as_logged():
    cuts = parse_scdet_output(_fixture_text())
    assert [c.t for c in cuts] == sorted(c.t for c in cuts)


@pytest.mark.media
def test_run_scene_analysis_produces_at_least_one_cut_on_the_real_sample_clip():
    cuts = run_scene_analysis(SAMPLE_DIR / "clip_a.mp4")
    assert len(cuts) >= 1
    assert all(isinstance(c, SceneCut) for c in cuts)
    assert all(c.t >= 0 for c in cuts)
