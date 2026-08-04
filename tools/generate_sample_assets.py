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


def generate_rotation_fixtures(source: Path, baked_out: Path, flagged_out: Path) -> None:
    # transpose=2 (90 CCW) matches the h264_metadata bsf's "anticlockwise" rotate=90 SEI below.
    run(["-i", str(source), "-vf", "transpose=2", "-c:v", "libx264", "-c:a", "aac", str(baked_out)])
    # h264_metadata bsf stamps a Display Orientation SEI (rotation=90) without
    # re-encoding -- coded pixels stay 1280x720, matching how phones flag rotation.
    run(
        [
            "-i", str(source), "-c", "copy",
            "-bsf:v", "h264_metadata=display_orientation=insert:rotate=90",
            str(flagged_out),
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

    generate_rotation_fixtures(
        SAMPLE_DIR / "clip_b.mp4",
        SAMPLE_DIR / "clip_b_portrait_baked.mp4",
        SAMPLE_DIR / "clip_b_portrait_flagged.mp4",
    )

    generate_music(MUSIC_DIR / "placeholder-energetic.wav", 220, 14, vibrato=False)
    generate_music(MUSIC_DIR / "placeholder-chill.wav", 180, 14, vibrato=True)

    print("generated sample assets")


if __name__ == "__main__":
    main()
