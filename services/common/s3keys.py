def raw_key(job_id: str, src_id: str) -> str:
    return f"raw/{job_id}/{src_id}"


def work_audio_key(job_id: str, src_id: str) -> str:
    return f"work/{job_id}/audio/{src_id}.flac"


def work_clip_key(job_id: str, index: int) -> str:
    return f"work/{job_id}/clips/{index:03d}.mp4"


def work_clips_prefix(job_id: str) -> str:
    return f"work/{job_id}/clips/"


def output_key(job_id: str) -> str:
    return f"output/{job_id}/montage.mp4"


def thumbnail_key(job_id: str) -> str:
    return f"output/{job_id}/thumbnail.jpg"
