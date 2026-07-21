from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Transition(BaseModel):
    type: Literal["cut", "crossfade", "fade_to_black"] = "cut"
    duration: float = 0.0


class Clip(BaseModel):
    source: str
    start: float
    end: float
    reason: str
    speed: float = 1.0
    transition_out: Transition | None = None

    @model_validator(mode="after")
    def check_bounds(self) -> "Clip":
        if self.start < 0:
            raise ValueError("clip start must be >= 0")
        if self.end <= self.start:
            raise ValueError(f"clip end ({self.end}) must be after start ({self.start})")
        return self


class SubtitlesConfig(BaseModel):
    enabled: bool = False
    style: Literal["bold-bottom", "lower-third"] = "bold-bottom"
    mode: Literal["phrase", "word_highlight"] = "phrase"


class ColorAdjust(BaseModel):
    contrast: float = 1.0
    saturation: float = 1.0
    brightness: float = 0.0


class ColorConfig(BaseModel):
    preset: Literal["none", "cinematic", "vivid", "bw"] = "none"
    adjust: ColorAdjust = Field(default_factory=ColorAdjust)


class AudioConfig(BaseModel):
    music_track: str | None = None
    music_gain_db: float = -14.0
    duck_under_speech: bool = True


class OutputConfig(BaseModel):
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    resolution: Literal["1080p", "720p"] = "1080p"
    max_duration: float = 60.0


class EditPlan(BaseModel):
    version: str
    summary: str = ""
    clips: list[Clip]
    subtitles: SubtitlesConfig = Field(default_factory=SubtitlesConfig)
    color: ColorConfig = Field(default_factory=ColorConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
