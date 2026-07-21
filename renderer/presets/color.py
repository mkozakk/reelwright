from pathlib import Path

LUT_DIR = Path(__file__).parent / "luts"

LUT_FILES = {
    "cinematic": LUT_DIR / "cinematic.cube",
    "vivid": LUT_DIR / "vivid.cube",
    "bw": LUT_DIR / "bw.cube",
}


def lut_path_for(preset: str) -> Path | None:
    if preset == "none":
        return None
    path = LUT_FILES.get(preset)
    if path is None:
        raise KeyError(f"unknown color preset '{preset}'")
    if not path.exists():
        raise FileNotFoundError(f"no LUT asset bundled for preset '{preset}': {path}")
    return path
