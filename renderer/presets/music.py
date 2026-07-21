import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MUSIC_DIR = REPO_ROOT / "assets" / "music"
MANIFEST_PATH = Path(__file__).parent / "music_manifest.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def resolve_track(track_id: str) -> Path:
    manifest = load_manifest()
    entry = manifest.get(track_id)
    if entry is None:
        raise KeyError(f"unknown music track '{track_id}'")
    path = MUSIC_DIR / entry["file"]
    if not path.exists():
        raise FileNotFoundError(f"music asset missing for '{track_id}': {path}")
    return path
