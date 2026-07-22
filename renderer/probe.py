from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_run import run_ffmpeg

MAX_FILE_BYTES = 500 * 1024 * 1024
MAX_DURATION_SECONDS = 300.0
MAX_PIXEL_RATE = 3840 * 2160 * 30  # ~4K30 equivalent
ALLOWED_VIDEO_CODECS = {"h264", "hevc", "vp9"}
ALLOWED_AUDIO_CODECS = {"aac", "opus"}


class ProbeError(RuntimeError):
    pass


@dataclass
class ProbeResult:
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None


def ffprobe_binary() -> str:
    path = shutil.which("ffprobe")
    if path is None:
        raise RuntimeError("ffprobe not found on PATH")
    return path


def _parse_frame_rate(rate: str) -> float:
    numerator, _, denominator = rate.partition("/")
    denominator = denominator or "1"
    if float(denominator) == 0:
        return 0.0
    return float(numerator) / float(denominator)


def run_ffprobe(path: Path) -> dict:
    cmd = [
        ffprobe_binary(),
        "-v", "error",
        "-show_streams", "-show_format", "-of", "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ProbeError(result.stderr[-4000:])
    return json.loads(result.stdout)


def probe_file(path: Path) -> ProbeResult:
    data = run_ffprobe(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError(f"no video stream found in '{path}'")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)

    return ProbeResult(
        duration=duration,
        width=int(video["width"]),
        height=int(video["height"]),
        fps=_parse_frame_rate(video.get("r_frame_rate", "0/1")),
        video_codec=video.get("codec_name", ""),
        audio_codec=audio.get("codec_name") if audio else None,
    )


def validate_probe(result: ProbeResult, file_size_bytes: int) -> list[str]:
    errors: list[str] = []

    if file_size_bytes > MAX_FILE_BYTES:
        errors.append(f"file size {file_size_bytes} exceeds {MAX_FILE_BYTES} bytes")
    if result.duration > MAX_DURATION_SECONDS:
        errors.append(f"duration {result.duration:.1f}s exceeds {MAX_DURATION_SECONDS}s")

    pixel_rate = result.width * result.height * result.fps
    if pixel_rate > MAX_PIXEL_RATE:
        errors.append(f"pixel rate {pixel_rate:.0f} exceeds {MAX_PIXEL_RATE}")

    if result.video_codec not in ALLOWED_VIDEO_CODECS:
        errors.append(f"unsupported video codec '{result.video_codec}'")
    if result.audio_codec is not None and result.audio_codec not in ALLOWED_AUDIO_CODECS:
        errors.append(f"unsupported audio codec '{result.audio_codec}'")

    return errors


def extract_audio_flac(input_path: Path, output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-y", "-i", str(input_path),
            "-vn", "-ar", "16000", "-ac", "1", "-c:a", "flac",
            str(output_path),
        ]
    )
    return output_path
