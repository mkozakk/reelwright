from __future__ import annotations

from pathlib import Path

from .ffmpeg_run import run_ffmpeg


def extract_thumbnail(input_path: Path, output_path: Path, at_seconds: float = 1.0) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-y", "-ss", str(at_seconds), "-i", str(input_path),
            "-frames:v", "1",
            str(output_path),
        ]
    )
    return output_path
