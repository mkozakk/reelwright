# Session Profile (`session_profile/`)

Runs once per job, after every source has been through `ProbeMap`. It's the point where the pipeline knows every source's *decoded* duration, not just what the client declared at presign time.

## Re-checking the session cap

`session_caps.MAX_SESSION_VIDEO_SECONDS` is checked again here, summed across every video source's real, probed duration. The presign step could only see declared size and file count; this is the first point where the real total is known, and a session that's too long is rejected before it reaches the analysis Lambdas.

## `target_profile`

Computed and written to the job record as informational session metadata: resolution, aspect, fps, sample rate, session duration, and source counts. Cut normalizes to fixed constants regardless of what's recorded here, so `target_profile` is shown and logged, not consumed by any downstream compute step.

## Fanning out to Analyze

The function returns the video-only source list that `AnalyzeParallel`'s three Map branches fan out over. An audio-only asset is excluded here, once, rather than being filtered again independently inside each of the three analysis branches.

## Related

- [[probe]]: the Map this state runs immediately after
- [[analyze]]: the branches this service's output list feeds
