from __future__ import annotations

import hashlib


def profile_key(aspect: str, resolution: str) -> tuple[str, str]:
    """Phase-7 stand-in for Phase 8's target_profile (docs/phases/phase-8.md,
    unpopulated until multi-file sessions land) -- (aspect, resolution) is
    enough to satisfy the actual requirement: an aspect/resolution change
    must not reuse cuts encoded to the old profile."""
    return (aspect, resolution)


def cache_key(source_key: str, start: float, end: float, speed: float, profile: tuple) -> str:
    payload = "|".join(
        [source_key, f"{start:.3f}", f"{end:.3f}", f"{speed:.3f}", *profile]
    )
    return hashlib.sha256(payload.encode()).hexdigest()
