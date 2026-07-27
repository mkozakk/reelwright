from __future__ import annotations

from pathlib import Path

# OpenAI Whisper's own published reference thresholds. A segment is dropped
# only if BOTH conditions hold -- a confidently-decoded segment over
# background music can legitimately have an elevated no_speech_prob without
# being a hallucination.
NO_SPEECH_PROB_THRESHOLD = 0.6
AVG_LOGPROB_THRESHOLD = -1.0


def filter_segments(segments: list[dict]) -> list[dict]:
    return [
        segment
        for segment in segments
        if not (
            segment["no_speech_prob"] > NO_SPEECH_PROB_THRESHOLD
            and segment["avg_logprob"] <= AVG_LOGPROB_THRESHOLD
        )
    ]


def words_from_segments(segments: list[dict]) -> list[dict]:
    words = []
    for segment in segments:
        for word in segment.get("words", []):
            words.append({"start": word["start"], "end": word["end"], "text": word["word"]})
    return words


def run_transcription(audio_path: Path, model_dir: str) -> list[dict]:
    # local_files_only=True is load-bearing: without it, a missing/corrupt
    # baked model silently attempts a network call that the no-egress VPC
    # blocks, hanging the Lambda until timeout instead of failing fast.
    from faster_whisper import WhisperModel

    model = WhisperModel(model_dir, device="cpu", compute_type="int8", local_files_only=True)
    segments, _info = model.transcribe(str(audio_path), word_timestamps=True, vad_filter=True)

    return [
        {
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "avg_logprob": segment.avg_logprob,
            "no_speech_prob": segment.no_speech_prob,
            "words": [
                {"start": word.start, "end": word.end, "word": word.word, "probability": word.probability}
                for word in (segment.words or [])
            ],
        }
        for segment in segments
    ]
