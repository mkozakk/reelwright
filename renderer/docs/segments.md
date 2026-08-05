# Segment Rewriting (`segments.py`, `thumbnail.py`)

Cut and Render operate on different plans, because Cut re-encodes each clip *before* concatenation while Render only ever sees the result of that cut. `segments.py` is the glue that lets both stages call the exact same `compile_plan` function.

## Splitting for Cut

`build_segment_plan(plan, clip_index)` builds a one-clip plan: just that clip's source range, normalized to the job's target aspect and resolution, with `transition_out` stripped. Transitions apply once, at Render, after segments are concatenated, so they'd be meaningless on an isolated single-clip render.

## Reassembling for Render

`build_concat_plan(plan, clip_durations)` rewrites every clip to reference the already-cut segment outputs (`clip0`, `clip1`, ...) at `start=0`, `end=<known output duration>`, `speed=1.0` (speed was already applied during Cut), while preserving transitions, color, subtitles, and audio from the original plan unchanged. Render then calls `compile_plan` on this exactly as it would on the original plan: pre-cut, pre-normalized inputs, no special casing needed anywhere in the compiler itself.

`clip_output_duration(clip)` is the shared helper both Cut and Render use to compute a clip's real output length (`(end - start) / speed`) before the durations are known any other way.

## Thumbnail (`thumbnail.py`)

`extract_thumbnail(input_path, output_path, at_seconds=1.0)` grabs a single frame via `-frames:v 1` at a fixed offset. Used by `services/finish/` to produce the job's list-view thumbnail from the finished render.

## Related

- [[edit-plan]]: the schema these functions read and rewrite
- [[compile]]: called with the outputs of both functions here, once per clip in Cut and once for the whole plan in Render
