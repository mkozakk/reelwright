import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
MUSIC_DIR = REPO_ROOT / "assets" / "music"


def run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, text=True)


def generate_clip(path: Path, video_source: str, tone_hz: int, duration: int) -> None:
    run(
        [
            "-f", "lavfi", "-i", f"{video_source}=size=1280x720:rate=30:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(path),
        ]
    )


def generate_music(path: Path, tone_hz: int, duration: int, vibrato: bool) -> None:
    filters = "volume=0.4"
    if vibrato:
        filters += ",vibrato=f=4:d=0.3"
    run(
        [
            "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000:duration={duration}",
            "-af", filters,
            "-c:a", "pcm_s16le",
            str(path),
        ]
    )


def main() -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    generate_clip(SAMPLE_DIR / "clip_a.mp4", "testsrc2", 440, 8)
    generate_clip(SAMPLE_DIR / "clip_b.mp4", "smptebars", 880, 8)

    generate_music(MUSIC_DIR / "placeholder-energetic.wav", 220, 14, vibrato=False)
    generate_music(MUSIC_DIR / "placeholder-chill.wav", 180, 14, vibrato=True)

    print("generated sample assets")


if __name__ == "__main__":
    main()
