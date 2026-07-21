from pathlib import Path

from renderer.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "assets" / "sample"
OUT_DIR = REPO_ROOT / "out"


def test_basic_plan_renders_single_clip():
    output = OUT_DIR / "basic.mp4"
    exit_code = main(
        [
            "render",
            str(SAMPLE_DIR / "plan_basic.json"),
            str(output),
            "--source", f"src1={SAMPLE_DIR / 'clip_a.mp4'}",
        ]
    )
    assert exit_code == 0
    assert output.exists()
    assert output.stat().st_size > 0
