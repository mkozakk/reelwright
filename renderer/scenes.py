from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_run import FfmpegError, ffmpeg_binary

# ffmpeg's scdet filter has no JSON output mode -- scene-change scores are
# scraped from stderr logging instead, e.g.:
#   [scdet @ 0x...] lavfi.scd.score: 0.944, lavfi.scd.time: 0.0333333
SCDET_LINE_RE = re.compile(r"lavfi\.scd\.score:\s*(-?[\d.]+),\s*lavfi\.scd\.time:\s*(-?[\d.]+)")


@dataclass
class SceneCut:
    t: float
    score: float


def parse_scdet_output(stderr_text: str) -> list[SceneCut]:
    cuts = []
    for line in stderr_text.splitlines():
        if not line.startswith("[scdet @"):
            continue
        match = SCDET_LINE_RE.search(line)
        if not match:
            continue
        cuts.append(SceneCut(t=float(match.group(2)), score=float(match.group(1))))
    return cuts


def run_scene_analysis(video_path: Path) -> list[SceneCut]:
    cmd = [
        ffmpeg_binary(),
        "-y", "-i", str(video_path),
        "-filter:v", "scdet=threshold=0.05",  # default (10) misses low-motion footage
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FfmpegError(result.returncode, result.stderr)
    return parse_scdet_output(result.stderr)
