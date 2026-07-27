from __future__ import annotations

import os
from pathlib import Path

import pytest

from renderer.transcribe import (
    AVG_LOGPROB_THRESHOLD,
    NO_SPEECH_PROB_THRESHOLD,
    filter_segments,
    words_from_segments,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def _segment(**overrides) -> dict:
    base = {
        "start": 1.0,
        "end": 2.0,
        "text": "hello",
        "avg_logprob": -0.2,
        "no_speech_prob": 0.05,
        "words": [{"start": 1.0, "end": 2.0, "word": "hello", "probability": 0.9}],
    }
    base.update(overrides)
    return base


# drop iff no_speech_prob > 0.6 AND avg_logprob <= -1.0 (AND, not OR)


def test_filter_segments_drops_when_both_thresholds_are_tripped():
    segment = _segment(no_speech_prob=0.9, avg_logprob=-2.0)
    assert filter_segments([segment]) == []


def test_filter_segments_keeps_when_neither_threshold_is_tripped():
    segment = _segment(no_speech_prob=0.1, avg_logprob=-0.3)
    assert filter_segments([segment]) == [segment]


def test_filter_segments_keeps_when_only_no_speech_prob_is_tripped():
    # confidently-decoded speech over background music can have an elevated
    # no_speech_prob without being a hallucination -- the AND must keep it
    segment = _segment(no_speech_prob=0.9, avg_logprob=-0.1)
    assert filter_segments([segment]) == [segment]


def test_filter_segments_keeps_when_only_avg_logprob_is_tripped():
    segment = _segment(no_speech_prob=0.1, avg_logprob=-3.0)
    assert filter_segments([segment]) == [segment]


def test_filter_segments_boundary_no_speech_prob_exactly_at_threshold_is_not_tripped():
    segment = _segment(no_speech_prob=NO_SPEECH_PROB_THRESHOLD, avg_logprob=-5.0)
    assert filter_segments([segment]) == [segment]


def test_filter_segments_boundary_avg_logprob_exactly_at_threshold_is_tripped():
    segment = _segment(no_speech_prob=0.99, avg_logprob=AVG_LOGPROB_THRESHOLD)
    assert filter_segments([segment]) == []


def test_filter_segments_preserves_relative_order_of_kept_segments():
    kept_a = _segment(start=1.0, no_speech_prob=0.1, avg_logprob=-0.1)
    dropped = _segment(start=2.0, no_speech_prob=0.9, avg_logprob=-2.0)
    kept_b = _segment(start=3.0, no_speech_prob=0.1, avg_logprob=-0.1)
    assert filter_segments([kept_a, dropped, kept_b]) == [kept_a, kept_b]


def test_filter_segments_empty_input_returns_empty_list():
    assert filter_segments([]) == []


def test_words_from_segments_flattens_and_renames_word_key_to_text():
    segments = [
        {
            "start": 1.0,
            "end": 2.0,
            "text": "hi there",
            "avg_logprob": -0.1,
            "no_speech_prob": 0.1,
            "words": [
                {"start": 1.0, "end": 1.4, "word": "hi", "probability": 0.9},
                {"start": 1.4, "end": 2.0, "word": "there", "probability": 0.8},
            ],
        }
    ]
    words = words_from_segments(segments)
    assert words == [
        {"start": 1.0, "end": 1.4, "text": "hi"},
        {"start": 1.4, "end": 2.0, "text": "there"},
    ]
    assert all(set(word.keys()) == {"start", "end", "text"} for word in words)


def test_words_from_segments_concatenates_multiple_segments_in_order():
    segments = [
        {"start": 0.0, "end": 1.0, "words": [{"start": 0.0, "end": 1.0, "word": "one", "probability": 0.9}]},
        {"start": 2.0, "end": 3.0, "words": [{"start": 2.0, "end": 3.0, "word": "two", "probability": 0.9}]},
    ]
    words = words_from_segments(segments)
    assert [w["text"] for w in words] == ["one", "two"]


def test_words_from_segments_empty_segments_returns_empty_list():
    assert words_from_segments([]) == []


def test_words_from_segments_segment_with_no_words_contributes_nothing():
    segments = [{"start": 0.0, "end": 1.0, "words": []}]
    assert words_from_segments(segments) == []


@pytest.mark.whisper
@pytest.mark.media
def test_run_transcription_real_model_round_trip():
    # deliberately excluded from default CI -- run manually with:
    #   pip install -e ".[transcribe]"  # once faster-whisper is available
    #   WHISPER_MODEL_DIR=/opt/whisper-model pytest -m whisper
    pytest.importorskip("faster_whisper")
    model_dir = os.environ.get("WHISPER_MODEL_DIR")
    if not model_dir:
        pytest.skip(
            "WHISPER_MODEL_DIR not set -- requires baked faster-whisper model weights"
        )

    from renderer.probe import extract_audio_flac
    from renderer.transcribe import run_transcription

    flac_path = extract_audio_flac(SAMPLE_DIR / "clip_a.mp4", OUT_DIR / "transcribe_clip_a.flac")
    segments = run_transcription(flac_path, model_dir)

    assert isinstance(segments, list)
    for segment in segments:
        assert "avg_logprob" in segment
        assert "no_speech_prob" in segment
        assert "words" in segment
