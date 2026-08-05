# 0003: Cut always re-encodes, never stream-copies

## Status

Accepted.

## Context

ffmpeg can extract a segment two ways: stream-copy (`-c copy`), which is fast because it doesn't touch the codec, or re-encode, which is slower but produces frame-accurate output. Stream-copy can only cut on keyframes, so a requested cut point often lands seconds away from where the plan said it should.

Given the pipeline processes short jobs (up to 5 minutes total media) rather than long-form video, the speed advantage of stream-copy matters less here than it would on a bulk video platform.

## Decision

The Cut step always re-encodes every segment. Stream-copy is not used anywhere in the pipeline.

## Consequences

- Cuts land exactly where the plan says, not on the nearest keyframe, which matters for anything time-sensitive (a beat, a word, a reaction).
- Every segment fed into the transition filtergraph shares the same codec, resolution, and pixel format, since re-encoding is also where multi-source normalization happens (matching a 1080p30 phone clip against a 4K60 source, see the multi-file session design). `xfade` and `acrossfade` need matching inputs; stream-copied segments from mismatched sources wouldn't have them.
- This is also a security boundary, not just a quality one: because Cut always re-encodes, the Fargate renderer downstream never touches raw, attacker-controlled upload bytes. It only ever reads ffmpeg's own re-encoded intermediates. Switching any path back to stream-copy would reopen that boundary.
- Re-encoding costs more compute time than stream-copy would, which is the traded-away option. At current job sizes this stays inside the Lambda time and cost budget; it would need revisiting if job length limits ever grow substantially.
