# The Edit Plan (`edit_plan/`)

The Edit Plan is the only interface between the LLM and the renderer. The model never touches ffmpeg; it only ever produces JSON matching this schema, and every field in it is either enum-restricted or numerically clamped before it reaches a filter graph.

## Schema (`models.py`)

```
EditPlan
├── version: str
├── summary: str
├── clips: list[Clip]            # 1..N
│   ├── source: str               # job-scoped source id, never a path
│   ├── start / end: float
│   ├── reason: str                # mandatory, shown to the user
│   ├── speed: float = 1.0
│   └── transition_out: Transition | None
│       ├── type: cut | crossfade | fade_to_black
│       └── duration: float
├── subtitles: SubtitlesConfig { enabled, style, mode }
├── color: ColorConfig { preset, adjust: { contrast, saturation, brightness } }
├── audio: AudioConfig { music_track, music_gain_db, duck_under_speech }
└── output: OutputConfig { aspect, resolution, max_duration }
```

`Clip.check_bounds`, a pydantic `model_validator`, rejects `start < 0` and `end <= start` at construction time, before the plan ever reaches `validate_plan`. Everything else about a clip's legality (length, overlap, budget) is checked one layer up.

## Validation (`validate.py`)

`validate_plan(plan, prefs, sources)` runs four passes, always in this order:

1. **Preference overwrite**: `output.aspect` and `output.max_duration` are overwritten from the caller's `prefs` dict, the user's own job settings, before anything else runs. These two fields are never left as the model's call.
2. **Clamping** (`_clamp_numerics`), applied silently rather than rejected:

   | Field | Range |
   |---|---|
   | `color.adjust.contrast` | 0.9 to 1.2 |
   | `color.adjust.saturation` | 0.8 to 1.4 |
   | `color.adjust.brightness` | -0.1 to 0.1 |
   | `audio.music_gain_db` | -20 to -8 dB |
   | `clips[].speed` | 0.5x to 2.0x |

3. **Structural checks** (`_check_structure`), always run: at least one clip and at most `MAX_CLIPS = 30`; every clip at least `MIN_CLIP_SECONDS = 0.5`s; a transition's `duration` capped at half the shorter adjacent clip; same-source clips with overlapping ranges at the same `speed` rejected (different speeds may overlap, since a slow-mo replay of the same footage is a legitimate choice); total output duration within `output.max_duration`.
4. **Source checks** (`_check_sources`), run only when a `sources` map is supplied (Cut and Render have real bounds; a bare schema-validation caller may not): every `clip.source` must exist and be video-kind, `end` must not exceed the source's real duration, and a `user:<id>` `music_track` must resolve to an audio-kind source in the same map. This is what turns an unknown, audio-only, or out-of-bounds `clip.source` into a rejected plan instead of an unhandled `KeyError` three steps downstream in Cut.

Failures raise `EditPlanValidationError(errors: list[str])` with every failure collected, not just the first, so a retry prompt to the LLM (or a human debugging a fallback) sees the whole picture in one pass.

## Loading a plan

`load_plan(path)` reads and `EditPlan.model_validate()`s a JSON file, wrapping pydantic's own `ValidationError` in the same `EditPlanValidationError` type, so a caller only ever needs to catch one exception class regardless of whether the failure was a schema mismatch or a rule violation.

## Related

- [[compile]]: consumes the validated plan this module produces
- [[segments]]: rewrites a validated plan for the Cut/Render split
- [[presets]]: resolves the color and music ids this schema carries as plain strings
