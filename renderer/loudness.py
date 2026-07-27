from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_run import FfmpegError, ffmpeg_binary

# ffmpeg's ebur128 filter has no JSON output mode -- momentary loudness is
# scraped from verbose stderr logging instead, e.g.:
#   [Parsed_ebur128_0 @ 0x...] t: 0.0999375  TARGET:-23 LUFS    M:-120.7 S:-120.7  ...
EBUR128_LINE_RE = re.compile(r"Parsed_ebur128.*\st:\s*(-?[\d.]+).*\bM:\s*(-?[\d.]+)")


@dataclass
class LoudnessPoint:
    t: float
    level_db: float


def parse_ebur128_output(stderr_text: str) -> list[LoudnessPoint]:
    points = []
    for line in stderr_text.splitlines():
        if "Parsed_ebur128" not in line or " t: " not in line:
            continue
        match = EBUR128_LINE_RE.search(line)
        if not match:
            continue
        points.append(LoudnessPoint(t=float(match.group(1)), level_db=float(match.group(2))))
    return points


def downsample_to_1hz(points: list[LoudnessPoint]) -> list[LoudnessPoint]:
    buckets: dict[int, list[float]] = {}
    for point in points:
        bucket = int(point.t // 1)
        buckets.setdefault(bucket, []).append(point.level_db)
    return [
        LoudnessPoint(t=float(bucket), level_db=sum(levels) / len(levels))
        for bucket, levels in sorted(buckets.items())
    ]


def run_loudness_analysis(audio_path: Path) -> list[LoudnessPoint]:
    cmd = [
        ffmpeg_binary(),
        "-y", "-loglevel", "verbose",
        "-i", str(audio_path),
        "-filter:a", "ebur128",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FfmpegError(result.returncode, result.stderr)
    return downsample_to_1hz(parse_ebur128_output(result.stderr))
