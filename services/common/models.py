from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceRef:
    key: str
    kind: str
    size: int
    uploaded: bool
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None


@dataclass
class JobRecord:
    job_id: str
    status: str
    created_at: str
    sources: dict[str, SourceRef] = field(default_factory=dict)
    user_id: str | None = None
    prefs: dict = field(default_factory=dict)
    edit_plan: dict | None = None
    planning: dict | None = None
    analysis_keys: dict = field(default_factory=dict)
    cut_keys: dict = field(default_factory=dict)
    target_profile: dict | None = None
    output_key: str | None = None
    thumbnail_key: str | None = None
    error: str | None = None
    ttl: int | None = None
    timings: dict = field(default_factory=dict)
