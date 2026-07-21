from pathlib import Path

from renderer.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def test_full_plan_renders_speed_fade_and_ducked_music():
    output = OUT_DIR / "full.mp4"
    exit_code = main(
        [
            "render",
            str(SAMPLE_DIR / "plan_full.json"),
            str(output),
            "--source", f"src1={SAMPLE_DIR / 'clip_a.mp4'}",
            "--source", f"src2={SAMPLE_DIR / 'clip_b.mp4'}",
            "--transcript", f"src1={SAMPLE_DIR / 'transcript_a.json'}",
        ]
    )
    assert exit_code == 0
    assert output.exists()
    assert output.stat().st_size > 0
