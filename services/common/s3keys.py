def raw_key(job_id: str, src_id: str) -> str:
    return f"raw/{job_id}/{src_id}"


def work_audio_key(job_id: str, src_id: str) -> str:
    return f"work/{job_id}/audio/{src_id}.flac"


def work_clip_key(job_id: str, index: int) -> str:
    return f"work/{job_id}/clips/{index:03d}.mp4"


def work_clips_prefix(job_id: str) -> str:
    return f"work/{job_id}/clips/"


def work_cut_cache_key(cache_hash: str) -> str:
    # content-addressed, decoupled from job_id -- shared across a job's own
    # reruns and, incidentally, across jobs that cut an identical segment
    # (docs/phases/phase-7.md rerender caching)
    return f"work/cache/{cache_hash}.mp4"


def output_key(job_id: str) -> str:
    return f"output/{job_id}/montage.mp4"


def thumbnail_key(job_id: str) -> str:
    return f"output/{job_id}/thumbnail.jpg"


def work_loudness_key(job_id: str, src_id: str) -> str:
    return f"work/{job_id}/loudness/{src_id}.json"


def work_scenes_key(job_id: str, src_id: str) -> str:
    return f"work/{job_id}/scenes/{src_id}.json"


def work_transcript_key(job_id: str, src_id: str) -> str:
    return f"work/{job_id}/transcript/{src_id}.json"
