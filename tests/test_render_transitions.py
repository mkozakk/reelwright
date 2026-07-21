from pathlib import Path

from renderer.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def test_transitions_plan_renders_crossfade_with_subtitles():
    output = OUT_DIR / "transitions.mp4"
    exit_code = main(
        [
            "render",
            str(SAMPLE_DIR / "plan_transitions.json"),
            str(output),
            "--source", f"src1={SAMPLE_DIR / 'clip_a.mp4'}",
            "--source", f"src2={SAMPLE_DIR / 'clip_b.mp4'}",
            "--transcript", f"src1={SAMPLE_DIR / 'transcript_a.json'}",
        ]
    )
    assert exit_code == 0
    assert output.exists()
    assert output.stat().st_size > 0
    assert output.with_suffix(".ass").exists()
