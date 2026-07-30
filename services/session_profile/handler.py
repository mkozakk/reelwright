from __future__ import annotations

import os

from services.common import dynamo, session_caps
from services.common.logging import log_job
from services.common.tracing import segment


class SessionCapExceeded(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def run_session_profile(job_id: str, jobs_table: str) -> dict:
    # post-ProbeMap aggregation -- re-checks the session video-duration cap
    # now that every source's decoded duration is known (presign only saw
    # declared size/count), computes target_profile (informational, ADR-1,
    # docs/phases/phase-8.md), and returns the video-only fan-out for Analyze
    dynamo.start_step(jobs_table, job_id, "session_validate")
    job = dynamo.get_job(jobs_table, job_id)
    video_sources = {sid: s for sid, s in job.sources.items() if s.kind == "video"}
    audio_sources = {sid: s for sid, s in job.sources.items() if s.kind == "audio"}

    total_video_seconds = sum(s.duration or 0.0 for s in video_sources.values())
    if total_video_seconds > session_caps.MAX_SESSION_VIDEO_SECONDS:
        error = (
            f"session video duration {total_video_seconds:.1f}s exceeds "
            f"{session_caps.MAX_SESSION_VIDEO_SECONDS}s"
        )
        dynamo.mark_failed(jobs_table, job_id, error)
        raise SessionCapExceeded([error])

    target_profile = {
        # matches Cut's actual fixed-constant normalization (renderer/compile.py) --
        # informational metadata, not consumed by Cut (ADR-1)
        "resolution": "1080p",
        "aspect": job.prefs.get("aspect", "16:9"),
        "fps": 30,
        "sample_rate": 48000,
        "channels": 2,
        "session_duration": round(total_video_seconds, 1),
        "video_source_count": len(video_sources),
        "audio_asset_count": len(audio_sources),
    }
    dynamo.update_job(jobs_table, job_id, target_profile=target_profile)
    dynamo.finish_step(jobs_table, job_id, "session_validate")

    return {
        "job_id": job_id,
        "subtitles_enabled": job.prefs.get("subtitles_enabled", True),
        "video_source_items": [{"job_id": job_id, "src_id": sid} for sid in sorted(video_sources)],
    }


def handler(event: dict, context=None) -> dict:
    job_id = event["job_id"]
    with log_job(__name__, job_id), segment(__name__, job_id):
        result = run_session_profile(job_id, jobs_table=os.environ["JOBS_TABLE"])
    return result
