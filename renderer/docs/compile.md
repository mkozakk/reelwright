# Compiling to ffmpeg (`compile.py`)

`compile_plan(plan, sources, output_path, ass_path=None, music_override=None)` builds one ffmpeg `-filter_complex` graph and returns a `CompiledCommand`, a flat `args: list[str]` that this module never executes itself (`ffmpeg_run.py` does that). It is a pure function: the same plan plus the same inputs always produces the same argv.

## The five stages

Each stage appends to the same running filter list.

1. **`_compile_clips`**: one input pair (`-ss/-to/-i`) per clip, using accurate-seek rather than stream-copy, so cuts land exactly on the plan's timestamps. Every clip is normalized to the target `width`x`height` (`scale` with `force_original_aspect_ratio=increase`, then `crop`) and 30fps, speed applied via `setpts`/`atempo`, and any `fade_to_black` transition on that clip's in/out edge burned in per-clip.
2. **`_compile_junctions`**: clips are chained pairwise. A `crossfade` transition becomes `xfade` (video) plus `acrossfade` (audio) with the offset computed from the running duration; everything else, including `fade_to_black` (already handled per-clip in step 1), becomes a plain `concat`.
3. **`_compile_color`**: `color.preset`, if not `none`, applies the bundled `.cube` LUT via `lut3d`; `color.adjust`, if any field differs from identity, layers a clamped `eq` on top.
4. **`_compile_subtitles`**: burns in `ass_path` if given, via `subtitles=filename=...:fontsdir=...`, with fonts pulled from `assets/fonts/`.
5. **`_compile_music`**: resolves `music_override` or `music_presets.resolve_track(music_track)`, adds it as a looped extra input, trims and fades it to the render's final duration, and mixes it in. When `duck_under_speech` is set, the dialogue track is split (`asplit`) so one copy keys a `sidechaincompress` on the music before the final `amix`, so the model never has to time ducking itself.

## Labels and dimensions

Filter graph labels come from `_Labeler`, an incrementing-counter allocator (`v1`, `a1`, `vx2`, ...) that guarantees every intermediate label in the graph is unique no matter how many stages touched it.

`target_dimensions(aspect, resolution)` maps the two enum fields to actual pixel dimensions, rounding the non-base side to the nearest even number, which `yuv420p` requires.

## Output shape

Always `libx264`/`yuv420p`/`aac`, with `+faststart` and a hard `-t` cap at the computed `cur_duration`, so the container can never run longer than the plan says, even if a filter's own math drifts.

## Related

- [[edit-plan]]: the schema and validation this function assumes has already run
- [[subtitles]]: produces the `.ass` file this module burns in
- [[presets]]: resolves the LUT and music paths this module applies
- [[segments]]: builds the per-clip and concat-ready plans this function is called on twice per job (once in Cut, once in Render)
