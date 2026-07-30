# Phase 8 — Multi-file sessions (v1.2)

## Overview

Widen the upload and pipeline machinery so a job becomes a **session** of up
to 5 media files (video and/or audio) that the LLM edits into one montage.
The Edit Plan schema and renderer have been multi-source since Phase 1
(`source` is a job-scoped id resolved against the job's source map); this
phase is entirely about the AWS session/upload/analysis machinery around
that already-multi-source contract, plus closing two boundary-validation
gaps the single-file world never exercised (unknown `source` ids, source
timestamp bounds) that become load-bearing the moment more than one source
exists.

Grounding in the current code changed the shape of this plan versus a naive
reading of `docs/phases/phase-8.md`: several pieces the doc implies are new
(per-file S3 key layout, per-source analysis artifact keys, the
`enable_dev_eventbridge_trigger` Terraform flag) **already exist**, built
ahead of schedule in Phase 3/7. Conversely, two pieces the doc treats as
settled (Cut "normalizes for free", a validator that already enforces
source bounds) are **not actually true today** and need real fixes. Both
are called out explicitly below and in Risks & Open Questions.

## Phase & Scope

This is `docs/phases/phase-8.md` (tag `v1.2`), cross-referenced against
`docs/DESIGN.md` §12, §6 (data model), §10 (security — capability URLs,
no-file-references), and back-references to Phase 2 (upload/trigger, Cut
re-encode, conditional status flip), Phase 3 (analyze block, evidence), and
Phase 4 (token caps, no-file-references for `music_track`). It does not
reach into Phase 9 (public API/webhooks/tiering) — no new auth model, no
API keys, no queueing changes to Render.

## Goals & Success Criteria

- `POST /jobs` accepts 1–5 declared files and returns one presigned upload
  target per file; `POST /jobs/{id}/start` verifies all uploads and starts
  the pipeline exactly once, idempotently.
- Session caps (≤5 files, ≤500 MB/file, ≤5 min total **video** media)
  enforced at presign and re-checked after Probe.
- Probe, and each Analyze branch, run per file as a Step Functions Map;
  Cut continues to normalize every clip to a uniform profile regardless of
  source (already true today — verified, not rebuilt).
- The LLM sees one merged evidence bundle where every phrase / loudness
  point / scene cut carries its source id, and can select clips from any
  source.
- A user-uploaded audio file becomes a job-scoped asset; `audio.music_track:
  "user:<src_id>"` resolves only within that job, validated at the plan
  boundary (not just repaired on the LLM path).
- Demo: 3 phone clips + 1 music file in → one interleaved montage out.
- Two pre-existing validator gaps closed: clips referencing an unknown
  `source` and clips whose timestamps exceed their source's duration are
  now **rejected at the validation boundary**, not left to crash Cut with
  a `KeyError` or silently clamp inside ffmpeg.

## Assumptions

State these explicitly since `phase-8.md` doesn't disambiguate them —
flagged again in Risks & Open Questions for confirmation:

1. **The ≤5-min-total-media cap applies to video sources only.** The cap's
   stated rationale ("transcription compute and Bedrock costs scale with
   total minutes") only applies to sources that reach Whisper/loudness/
   scenes/evidence — a user-uploaded music file is explicitly excluded from
   Analyze (see Architecture). It still counts toward the 5-file limit and
   the 500 MB/file limit.
2. **`target_profile` is informational, not functionally load-bearing for
   Cut.** See ADR-1. Recommendation, not yet confirmed by the user.
3. **User-uploaded music assets accept `mp3` in addition to `aac`/`opus`**
   (decided 2026-07-30, overriding this plan's original recommendation) —
   the ffmpeg audio decoder allowlist widens to include `mp3`, accepting the
   added untrusted-input surface for demo/UX value. See ADR-4.
4. **`src_id`s become `src1..srcN`** (file declaration order), replacing the
   single-file world's hardcoded literal `"source"`. No migration needed —
   job records TTL at 30 days, raw objects at 48 h, and there is one shared
   dev environment; an in-flight job spanning a deploy is an accepted,
   pre-existing risk of this project's deploy model, not new to Phase 8.
5. Audio-only sources are never eligible as `clips[].source` (no video
   stream to cut) — only as `audio.music_track: "user:<id>"`.
6. No new Terraform environment is introduced; `enable_dev_eventbridge_trigger`
   continues to default `true` in `envs/dev` (the only env) and the "off
   outside dev" requirement is satisfied structurally until a second env
   exists — noted, not re-litigated.

## Out of Scope

- Public API / API keys / webhooks / tiering (Phase 9).
- Beat-grid evidence (Stretch — nothing produces beats yet, so no
  per-source beat-grid join).
- Content-ID / rights verification for user-uploaded music (README caveat
  only, per the phase doc).
- Changing Cut's fixed-constant normalization behavior (fps=30, output
  resolution from prefs, 48 kHz stereo) — see ADR-1.
- Fixing the pre-existing `_rerender` field-authority gap (see Risks) —
  flagged, not bundled into this phase's scope silently.

## Architecture & Design

### Current-state findings that reshape this phase's actual work

| Phase-8 doc says | Code actually does today | Consequence for this plan |
|---|---|---|
| "Cut already re-encodes everything... normalizes at zero extra passes" | True, and *more* true than the doc implies: `renderer/compile.py::_compile_clips` scales/crops **every clip** to `output.resolution`/`output.aspect` (user-owned prefs) and hardcodes `fps=30` and a `48kHz stereo` audio format filter, **unconditionally, per source, regardless of the source's native profile** — this has been true since Phase 1 | A "target-profile function" that computes fps/resolution for Cut to consume is redundant for correctness. See ADR-1. |
| (implicit) plan validation is the boundary for source correctness | `renderer/edit_plan/validate.py::_check_structure` never checks that `clip.source` exists in the job's sources, nor that `clip.end` is within that source's duration. A bad `source` id reaches `services/cut/handler.py`'s `job.sources[clip.source]` and raises an unhandled `KeyError` inside a Cut Lambda, not a graceful plan rejection | This gap is invisible with one source (nothing to get wrong) and becomes a real correctness/security gap with N sources. Closing it is now in scope (T4). |
| "EventBridge...becomes a Terraform flag, off outside dev" | Already exists: `var.enable_dev_eventbridge_trigger` (`infra/envs/dev/variables.tf`), gates `infra/envs/dev/eventbridge.tf` | Mostly done. Remaining gap: it does no per-file-count awareness — see T10. |
| Analysis artifacts keyed per source | `services/common/s3keys.py` already has `work_audio_key/work_loudness_key/work_scenes_key/work_transcript_key(job_id, src_id)` — built multi-source-ready in Phase 3 | No S3 key-layout work needed. |
| — | `services/common/dynamo.py::set_analysis_key` does `SET analysis_keys.#category = :val` — a **whole-category overwrite**, not a per-source merge. Harmless today (always one writer per category). Under Phase 8's per-source Map, N concurrent writers to the same category will race and clobber each other's entries | Real bug, must fix before Probe/Analyze become Map states (T2). |

### Target architecture (Step Functions)

```
RouteMode (Choice, unchanged: mode=="rerender" -> PrepareCut)
  |
  v
ProbeMap  (Type: Map, ItemsPath: $.source_items, MaxConcurrency 5)
  each item: {job_id, src_id}  -- built by whichever Lambda starts the
  execution (trigger or job_api's /start), which already has job.sources
  in hand from the conditional-flip read; no new "PrepareProbe" Lambda
  -> invokes probe_arn once per source, writes source metadata + kind-aware
     audio extraction per source (see Probe below)
  ResultPath: $.probe_results (preserves $.job_id / $.source_items, same
  pattern CutMap already uses -- NOT ResultPath "$" like today's Probe)
  |
  v
SessionValidate  (new Lambda, Task)
  re-checks session caps now that every source's duration/kind is known
  (defense in depth: presign only knew declared size/count, not decoded
  duration), computes target_profile (informational, ADR-1), and emits
  video_source_items for the Analyze fan-out
  ResultSelector: {job_id, subtitles_enabled, video_source_items}
  ResultPath: $
  |
  v
AnalyzeParallel (Parallel, unchanged shape, each branch's inner Task
  becomes a Map over $.video_source_items, MaxConcurrency 5):
    - TranscribeChoice (unchanged, session-wide gate on subtitles_enabled)
        -> TranscribeMap -> analyze_transcribe per video source
    - LoudnessMap -> analyze_loudness per video source
    - ScenesMap -> analyze_scenes per video source
  |
  v
Plan  (rewritten: merges all video sources' evidence, tags every
  phrase/point/scene with its source id)
  |
  v
PrepareCut -> CutMap -> AcquireRenderSlot -> RenderSpot/RenderOnDemand ->
  ReleaseSlot -> Finish        (all unchanged)
```

Audio-only (`kind: "audio"`) sources never enter Analyze — they have no
video stream to select clips from and no editorial evidence value; they
exist solely as `audio.music_track: "user:<src_id>"` candidates, resolved
at Render.

### Data flow for a 3-video + 1-music-file session

1. `POST /jobs` with `files: [4 entries]` → job created with
   `sources: {src1: video, src2: video, src3: video, src4: audio}`, 4
   presigned upload targets returned.
2. Client uploads all 4, calls `POST /jobs/{id}/start` → HeadObject-verifies
   all 4 → conditional `UPLOADING → ANALYZING` flip → `StartExecution` with
   `source_items: [{src1},{src2},{src3},{src4}]`.
3. `ProbeMap` runs 4 Lambda invocations in parallel: src1–3 get the
   existing 16 kHz mono FLAC (Whisper input) plus duration/w/h/fps written
   back; src4 (audio-only) gets a *separate* 48 kHz stereo FLAC extraction
   for music use, no video-stream checks applied.
4. `SessionValidate` sums src1+src2+src3 durations, rejects if > 300 s,
   computes `target_profile`, emits `video_source_items: [src1,src2,src3]`.
5. `AnalyzeParallel` runs 3×3=9 Lambda invocations (transcribe/loudness/
   scenes × 3 video sources); src4 is never touched.
6. `Plan` merges 3 sources' evidence (every phrase/point/scene tagged
   `source: "src1"` etc.), includes `src4` in `evidence.music_tracks` as
   `user:src4`; the LLM picks clips from any of src1–3 and may pick
   `user:src4` as the track.
7. `Cut` normalizes every selected clip to the plan's output profile,
   exactly as it does today for one source.
8. `Render` resolves `music_track: "user:src4"` by downloading the
   work-bucket asset and mixing it in — no raw-bucket access needed (the
   asset is already an ffmpeg-produced work-bucket intermediate from step 3).

## Decision Records (ADRs)

### ADR-1: `target_profile` is informational; Cut's normalization stays fixed-constant

**Context.** `phase-8.md` describes a "target-profile function (common
resolution ≤1080p, majority fps, 48kHz stereo)" that Cut normalizes to.
The current `renderer/compile.py` already scales/crops every clip to
`output.resolution`/`output.aspect` (a **user pref**, not a per-session
computed value) and hardcodes `fps=30` and the audio format filter,
unconditionally per clip — this already produces uniform `xfade`/`concat`
inputs for any mix of source profiles, with zero source-profile awareness
needed.

**Decision.** Do not change `compile.py`/`segments.py`/`cutcache.py`.
Compute `target_profile` in the new `SessionValidate` Lambda purely as
descriptive metadata stored on the job record (`resolution`, `aspect`,
`fps: 30`, `sample_rate: 48000`, `channels: 2`, `session_duration`,
`video_source_count`) — useful for the UI/README/cost accounting, not
consumed by Cut.

**Alternatives considered.**
- *Compute a real per-session target profile (highest common resolution,
  majority fps) and thread it into `compile.py` to replace the hardcoded
  constants.* Matches the doc's literal wording. Rejected: strictly more
  code for an equivalent (arguably worse — "majority fps" from 3 phone
  clips is a less predictable guarantee than "always 30") outcome, and it
  would be new code introduced without a driving requirement — against
  CLAUDE.md's "no unnecessary abstractions."

**Consequences.** Simpler, less code, matches "boring solution" bias.
Cost: this is a **deviation from `phase-8.md`'s literal wording** that
needs an explicit nod before implementation — flagged again in Risks.

### ADR-2: unknown `clip.source` / out-of-bounds timestamps reject at the validator, not the "repair" layer

**Context.** `services/plan/handler.py::_remap_unknown_sources` today
silently remaps *any* invented source name to "the sole source" when
`len(known_sources) == 1`, and explicitly no-ops for N>1 ("a bad source
stays invalid" — but nothing currently makes it *actually* invalid; it
just crashes downstream in Cut).

**Decision.** Add structural checks to `renderer/edit_plan/validate.py`
(`_check_structure`, or a sibling function folded into the same error
list): unknown `clip.source`, `clip.source` pointing at an audio-only
source, `clip.end` beyond that source's probed duration, and
`audio.music_track: "user:X"` where `X` isn't a known audio asset of the
job — all become validation errors, feeding the *existing*
one-retry-then-fallback machinery. No new repair path.

**Alternatives considered.**
- *Extend the remap heuristic to N sources* (e.g., nearest-name match).
  Rejected: guessing which of 5 sources the model meant is exactly the
  kind of silent repair the user's own project memory
  (`phase4-validator-stays-strict.md`) says not to do for structural
  correctness.

**Consequences.** Reuses the existing retry/fallback path, no new
machinery. `_remap_unknown_sources`'s N==1 behavior is untouched
(preserves existing Phase 4 test coverage and behavior).

### ADR-3: session fan-out payload is `[{job_id, src_id}, ...]`, not a bare id list + ASL `ItemSelector`

**Context.** Step Functions Map states can shape each iteration's input
either via `ItemSelector`/`$$.Map.Item.Value` at the state level, or by
having the upstream producer emit already-correctly-shaped items (the
pattern `cut_prepare` already uses for `CutMap`'s `batches`).

**Decision.** Follow the existing `cut_prepare`/`CutMap` convention:
whichever Lambda starts the execution (`trigger` or job_api's `/start`)
emits `source_items: [{"job_id": ..., "src_id": "src1"}, ...]` directly;
`SessionValidate` emits `video_source_items` the same way. `ProbeMap` and
the three Analyze Maps use plain `ItemsPath`, no `ItemSelector`.

**Alternatives considered.** `ItemSelector` + `$$.Map.Item.Value` — more
"idiomatic modern ASL," but introduces a second Map-shaping convention
into a codebase that already has one working pattern. Rejected for
consistency (CLAUDE.md: prefer the boring, already-established solution).

### ADR-4: user-uploaded music accepts `mp3` alongside `aac`/`opus`

**Context.** Real users will bring `.mp3` files. The current ffmpeg decoder
allowlist (`ALLOWED_AUDIO_CODECS = {aac, opus}`) exists specifically to
bound the "ffmpeg parses untrusted media" attack surface (DESIGN §10
platform layer, risk #2). mp3 demuxing has its own CVE history, so widening
the allowlist is a real, not free, trade-off.

**Decision (2026-07-30, explicit sign-off).** Add `mp3` to
`ALLOWED_AUDIO_CODECS`; `POST /jobs` accepts `audio/mpeg` uploads alongside
`audio/mp4`, `audio/aac`, `audio/ogg`. Chosen over this plan's original
recommendation because "upload your own song" is a materially weaker demo
pitch if the most common consumer export format is rejected outright.

**Alternatives considered.** *Keep the allowlist unchanged, reject mp3.*
The more conservative default this plan originally proposed — smaller
attack surface, but a real UX cost (users must convert files before
upload). Superseded by the explicit decision above.

**Consequences.** Probe (the sole place raw attacker-controlled bytes are
decoded, VPC-isolated/no-egress) now exercises one more demuxer/decoder
path against untrusted input. No other mitigation added in this phase —
if this surface becomes a problem later, revisit via sandboxing/decoder
hardening, not by silently narrowing the allowlist back.

## Constitution & Design-Doc Alignment

No `.specify/memory/constitution.md` exists in this repository (it was
found only in an unrelated sibling project on this machine) — treating
`CLAUDE.md` + `docs/DESIGN.md` + `docs/EFFECTS.md` as the operative
governing docs for this repo, per the instruction that ambiguity should be
surfaced rather than silently resolved.

- **Schema-validated boundary.** Directly strengthened: ADR-2 closes a real
  gap where an invalid `source` reference reached ffmpeg-adjacent code
  (Cut) as an unhandled `KeyError` instead of being rejected at the plan
  validator. `audio.music_track: "user:X"` is validated the same way
  clip sources are — never trusted as a free string once it takes the
  `user:` form.
- **Capped effects catalog (EFFECTS.md).** Untouched — no new Edit Plan
  capability, only a new *namespace* for an existing field
  (`audio.music_track`). Does not count against the 12-capability cap
  (same reasoning EFFECTS.md already gives for the bundled-vs-user-uploaded
  distinction under capability #5).
  Effects catalog music-preset line already says "from v1.2, user-uploaded
  tracks as job-scoped assets" — this phase is exactly that, tracked, not
  new.
- **No file references (DESIGN §10 layer 2).** `user:<src_id>` is resolved
  by looking `src_id` up in the *current job's own* `sources` map (fetched
  by `job_id`, never by a client-supplied path) — a malicious
  `music_track: "user:<another-job's-src-id>"` cannot cross jobs because
  the lookup is always scoped to the job record already fetched by the
  authenticated caller's own `job_id`. Verified in Implementation Details.
- **Defense in depth for untrusted media.** Probe remains the sole
  ffmpeg-touches-raw-bytes boundary; Render still never gets raw-bucket
  IAM access (verified against `infra/envs/dev/iam.tf` — no change needed,
  the existing work-bucket-wide `s3:GetObject` grant already covers the
  new `work/<job_id>/assets/<src_id>.flac` key). ADR-4 is exactly this
  principle applied to the new audio-upload surface.
- **Cost-bounded-by-design.** Session caps re-checked post-Probe
  (`SessionValidate`) close the gap where presign-time checks can't see
  actual decoded duration; Analyze Map `MaxConcurrency: 5` bounds fan-out
  cost per job to the same shape the file-count cap already implies.
- **Test/fixture discipline.** Addressed in Testing Strategy — mixed-profile
  fixture, cross-file validation tests, audio-only handling tests are
  explicit phase-8.md requirements, mapped to concrete test files below.
- **Capability-based access.** Unaffected — `/start` and the N-file presign
  responses ride the same Cognito-JWT-owned-job model Phase 7 already
  built (`_owned_job`); no new capability surface introduced.

## Data Model

### DynamoDB `jobs` table changes

`services/common/models.py::SourceRef` — extended (backward compatible,
all new fields optional):

```python
@dataclass
class SourceRef:
    key: str
    kind: str                    # "video" | "audio" -- declared at presign,
                                  # confirmed by Probe
    size: int
    uploaded: bool
    duration: float | None = None   # populated by Probe
    width: int | None = None        # populated by Probe, video only
    height: int | None = None       # populated by Probe, video only
    fps: float | None = None        # populated by Probe, video only
```

New top-level job attribute (already reserved in DESIGN §6, unpopulated
until now):

```json
"target_profile": {
  "resolution": "1080p", "aspect": "16:9", "fps": 30,
  "sample_rate": 48000, "channels": 2,
  "session_duration": 47.3, "video_source_count": 3, "audio_asset_count": 1
}
```

Written once by `SessionValidate` via `dynamo.update_job(..., target_profile={...})`.

### S3 key layout

New key function only (all others already multi-source-ready):

```python
# services/common/s3keys.py
def work_asset_key(job_id: str, src_id: str) -> str:
    return f"work/{job_id}/assets/{src_id}.flac"   # 48kHz stereo, music-quality
```

`raw_key(job_id, src_id)` unchanged in shape; `src_id` now ranges over
`src1..srcN` instead of the single literal `"source"`.

### New shared module: session caps

```python
# services/common/session_caps.py  (new file)
MAX_FILES = 5
MAX_SESSION_VIDEO_SECONDS = 300.0
```

Replaces the drift-prone pattern already visible today (job_api's
`MAX_DURATION_SECONDS` and `renderer.probe.MAX_DURATION_SECONDS` are two
independently-defined 300s constants for *different* things, linked only
by a comment). Imported by `services/job_api/logic.py` (file-count check
at presign) and `services/session_profile/handler.py` (aggregate duration
re-check).

## Interfaces & Contracts

### `POST /jobs` (rewritten)

Request:
```json
{
  "files": [
    {"content_type": "video/mp4", "size": 104857600},
    {"content_type": "video/quicktime", "size": 52428800},
    {"content_type": "audio/mp4", "size": 8388608}
  ],
  "prefs": {"vibe": "energetic", "aspect": "16:9", "subtitles_enabled": true}
}
```
Validation (`services/job_api/logic.py::validate_create_request`,
rewritten): `1 <= len(files) <= session_caps.MAX_FILES`; each
`content_type` in `ALLOWED_VIDEO_CONTENT_TYPES | ALLOWED_AUDIO_CONTENT_TYPES`;
each `size` in `(0, MAX_FILE_BYTES]`; `kind = "video" if content_type in
ALLOWED_VIDEO_CONTENT_TYPES else "audio"`. At least one file must be
`kind == "video"` (a session with only audio files has nothing to cut) —
new validation rule, 400 if violated.

Response `201`:
```json
{
  "job_id": "…", "status": "UPLOADING",
  "sources": [
    {"src_id": "src1", "kind": "video", "upload_type": "single", "upload_url": "…", "upload_method": "PUT", "upload_headers": {…}, "expires_in": 3600},
    {"src_id": "src2", "kind": "video", "upload_type": "multipart", "upload_id": "…", "part_size": …, "parts": […], "expires_in": 3600},
    {"src_id": "src3", "kind": "audio", "upload_type": "single", …}
  ]
}
```
`build_job_item` builds `sources` as `{src1: {...}, src2: {...}, ...}`
keyed by declaration order; existing IP/user daily-job-count quota logic
unchanged (one quota slot per **session**, not per file).

### `POST /jobs/{id}/complete` (src_id-aware)

Request adds `src_id`:
```json
{"src_id": "src2", "upload_id": "…", "parts": [{"part_number": 1, "etag": "…"}, …]}
```
`validate_complete_request` gains a `src_id` check: must be a key of
`job.sources`; `_complete_upload` uses `job.sources[src_id].key` instead of
the old hardcoded `s3keys.raw_key(job_id, logic.SRC_ID)`.

### `POST /jobs/{id}/start` (new)

No request body. Contract, in order (precise ordering matters — see
Implementation Details for why):

1. `_owned_job` (404 if not owned/found, existing pattern).
2. If `job.status != "UPLOADING"`: respond `200 {"job_id", "status": job.status}`
   — idempotent no-op (covers double-`start`, and a `start` after the
   pipeline already moved on).
3. HeadObject-verify every `job.sources[src_id].key` in the raw bucket:
   object exists, `ContentLength == declared size`, `ContentType ==
   declared content_type`. Any failure → `409` with
   `{"error": "...", "missing_or_mismatched": ["src2"]}` — **job stays in
   UPLOADING**, this is a client error, not a job failure; no `mark_failed`.
4. `dynamo.update_source(..., src_id, uploaded=True)` for every source.
5. `dynamo.conditional_status_flip(UPLOADING → ANALYZING)`. If it returns
   `False` here (lost a race to a concurrent `/start`), treat as the same
   idempotent no-op as step 2 and respond `200`.
6. Build `source_items = [{"job_id": job_id, "src_id": sid} for sid in
   sorted(job.sources)]`; `sfn.start_execution(name=start_execution_name(job_id,
   etags, prefs_hash), input={"job_id": job_id, "mode": "new", "source_items": [...]})`,
   swallowing `ExecutionAlreadyExists` exactly as `trigger` does.
7. Respond `202 {"job_id": job_id, "status": "ANALYZING"}`.

`etags` in step 6 come from the HeadObject responses in step 3 (not cached
from job creation) — a user who re-uploads to the same presigned URL
before calling `/start` gets a fresh ETag baked into the execution-name
backstop, consistent with the rationale DESIGN §2 already gives for why
the hash must reflect the *current* object, not a stale one.

### `POST /jobs/{id}/rerender`, `GET /jobs/{id}`, `GET /jobs`

Unchanged contracts. `GET /jobs/{id}` response's `edit_plan.clips[].source`
now may be any of `src1..srcN` — frontend already renders `clip.source` as
opaque text (`frontend/app.js::renderPlanEditor`), no frontend contract
change required there.

### Step Functions execution input (new shape)

```json
{"job_id": "...", "mode": "new", "source_items": [{"job_id": "...", "src_id": "src1"}, ...]}
```
Produced identically by `services/trigger/handler.py` (single-file dev
path — `source_items` has exactly one entry) and `services/job_api`'s
`/start` handler (N entries). This is the one payload-shape change both
entry points must agree on.

### `renderer/edit_plan/validate.py::validate_plan` (signature change)

```python
@dataclass
class SourceBounds:
    kind: str        # "video" | "audio"
    duration: float

def validate_plan(
    plan: EditPlan,
    prefs: dict | None = None,
    sources: dict[str, SourceBounds] | None = None,
) -> EditPlan: ...
```
When `sources` is `None` (renderer CLI / local usage with no job context),
cross-file checks are skipped — preserves `python -m renderer render …`
working without a DynamoDB job. When provided (both plan-Lambda call sites
below), the new checks in ADR-2 run.

Call sites passing `sources=`:
- `services/plan/handler.py::_try_validate` — builds `sources` from
  `job.sources` (`kind` + `duration`, both now populated by Probe).
- `services/job_api/handler.py::_rerender` — same construction from
  `job.sources`. (Today `_rerender` calls `validate_plan(plan)` with *no*
  `prefs` either — a pre-existing gap, flagged in Risks, not silently
  folded in here.)

### `services/session_profile/handler.py` (new Lambda)

```python
def run_session_profile(job_id: str, jobs_table: str) -> dict:
    """Post-ProbeMap aggregation: re-checks the session video-duration cap,
    computes target_profile (informational, ADR-1), and returns the
    video-only source_items list for the Analyze fan-out."""
```
Raises `SessionCapExceeded(errors: list[str])` → `dynamo.mark_failed(...)`,
same "never retried" pattern as `ProbeRejected` in `services/probe/prepare.py`
today (n.b. current `probe/handler.py` raises directly, no separate
`prepare.py` exists for probe — see Implementation Details).

## Implementation Details

### Probe (`renderer/probe.py`, `services/probe/handler.py`)

`renderer/probe.py`:
- `ProbeResult` gains `kind: Literal["video", "audio"]`.
- `probe_file()`: if no video stream, look for an audio stream; if found,
  return `kind="audio"`, `width=height=0`, `fps=0.0`, `video_codec=""`. If
  neither stream exists, raise `ProbeError` (unchanged — a file with no
  usable stream is rejected either way).
- `validate_probe(result, file_size_bytes, declared_kind: str | None = None)`:
  - new: if `declared_kind is not None and declared_kind != result.kind`,
    error `"declared kind '{declared_kind}' does not match probed kind
    '{result.kind}'"` — defense against `content_type` spoofing at presign.
  - pixel-rate and `ALLOWED_VIDEO_CODECS` checks only run when
    `result.kind == "video"`.
  - `ALLOWED_AUDIO_CODECS` check applies regardless of kind (both a video's
    audio track and a standalone audio upload must decode with an allowed
    codec) — per ADR-4, widened to `{aac, opus, mp3}`.
- `extract_audio_flac` unchanged (still 16 kHz mono, still the Whisper/
  loudness/scenes input) — only invoked for `kind == "video"` sources now.
- new: `extract_audio_asset(input_path, output_path)` — 48 kHz stereo FLAC,
  invoked only for `kind == "audio"` sources, writing to `work_asset_key`.

`services/probe/handler.py::run_probe` — signature gains `src_id`
(previously derived via `next(iter(job.sources.items()))`, the "single
source in Phase 2" shortcut):

```python
def run_probe(job_id: str, src_id: str, jobs_table: str, raw_bucket: str, work_bucket: str) -> dict:
```
Body: download `job.sources[src_id]`, `probe_file`, `validate_probe(...,
declared_kind=job.sources[src_id].kind)`; on failure `dynamo.mark_failed`
+ raise (unchanged, never retried into the rest of the pipeline — Map's
`Catch: States.ALL -> JobFailed` at the `ProbeMap` level covers this the
same way the single Probe task's `Catch` does today). On success:
`dynamo.update_source(jobs_table, job_id, src_id, duration=..., width=...,
height=..., fps=..., kind=result.kind)`; extract the appropriate audio
artifact per kind; `dynamo.set_analysis_key(jobs_table, job_id, "audio",
src_id, audio_key)` (new 4-arg signature, see below) only for video
sources (music-asset FLAC is referenced via `work_asset_key(job_id,
src_id)` directly at Render time — it doesn't need an `analysis_keys` entry
since nothing analyzes it).

`handler()` reads `src_id` from `event["src_id"]` (the Map item shape).
`subtitles_enabled` is no longer returned per-invocation from Probe — it's
a job-level pref, moved to `SessionValidate`'s output (computed once, not
N times).

### `services/common/dynamo.py::set_analysis_key` (breaking signature change)

```python
def set_analysis_key(table_name: str, job_id: str, category: str, src_id: str, key) -> None:
    # nested-leaf SET on one (category, src_id) pair -- safe for concurrent
    # per-source Map iterations writing different categories/sources on the
    # same item (same pattern as set_cut_key)
    table = _table(table_name)
    pk_key = {PK: job_pk(job_id)}
    table.update_item(Key=pk_key,
        UpdateExpression="SET analysis_keys = if_not_exists(analysis_keys, :empty)",
        ExpressionAttributeValues={":empty": {}})
    table.update_item(Key=pk_key,
        UpdateExpression="SET analysis_keys.#category = if_not_exists(analysis_keys.#category, :empty)",
        ExpressionAttributeNames={"#category": category},
        ExpressionAttributeValues={":empty": {}})
    table.update_item(Key=pk_key,
        UpdateExpression="SET analysis_keys.#category.#src = :val",
        ExpressionAttributeNames={"#category": category, "#src": src_id},
        ExpressionAttributeValues={":val": to_decimal(key)})
```
Call sites to update (4): `services/probe/handler.py`,
`services/analyze_loudness/handler.py`, `services/analyze_scenes/handler.py`,
`services/analyze_transcribe/handler.py` — each currently calls
`dynamo.set_analysis_key(jobs_table, job_id, "<category>", {src_id: key})`;
change to `dynamo.set_analysis_key(jobs_table, job_id, "<category>", src_id, key)`.

New helper alongside it:
```python
def update_source(table_name: str, job_id: str, src_id: str, **fields) -> None:
    # nested-leaf SET on sources.<src_id>'s sub-attributes -- safe for
    # concurrent ProbeMap iterations on different src_id keys. Assumes
    # sources.<src_id> already exists (job_api pre-populates all declared
    # sources at job creation), so no if_not_exists init needed here.
```

### Trigger (`services/trigger/handler.py`, `logic.py`)

Guard against firing on multi-file jobs (the dev EventBridge convenience
path fires per uploaded object; for N>1 the first uploaded file's
`ObjectCreated` would start the pipeline before the others exist):

```python
job = dynamo.get_job(jobs_table, job_id)
if len(job.sources) != 1:
    log.info("multi-file session, ignoring EventBridge convenience trigger")
    return {"job_id": job_id, "started": False, "reason": "multi-file session"}
```
Placed before the conditional flip (must not consume the flip for a job
it's not actually starting). `execution_name`/`hash_prefs` unchanged;
`source_items` built as the single-entry list described above.

### `services/session_profile/handler.py` (new)

```python
def run_session_profile(job_id: str, jobs_table: str) -> dict:
    job = dynamo.get_job(jobs_table, job_id)
    video_sources = {sid: s for sid, s in job.sources.items() if s.kind == "video"}
    audio_sources = {sid: s for sid, s in job.sources.items() if s.kind == "audio"}
    total_video_seconds = sum(s.duration for s in video_sources.values())
    if total_video_seconds > session_caps.MAX_SESSION_VIDEO_SECONDS:
        dynamo.mark_failed(jobs_table, job_id, f"session video duration {total_video_seconds:.1f}s exceeds {session_caps.MAX_SESSION_VIDEO_SECONDS}s")
        raise SessionCapExceeded([...])
    target_profile = {...}  # ADR-1, informational
    dynamo.update_job(jobs_table, job_id, target_profile=target_profile)
    return {
        "job_id": job_id,
        "subtitles_enabled": job.prefs.get("subtitles_enabled", True),
        "video_source_items": [{"job_id": job_id, "src_id": sid} for sid in sorted(video_sources)],
    }
```
Packaged as a plain zip Lambda (boto3 from the runtime, no ffmpeg, no
`renderer` import needed) — same posture as `services/trigger`,
`services/semaphore`.

### Analyze handlers (`services/analyze_{transcribe,loudness,scenes}/handler.py`)

Each drops its `next(iter(...))` single-source shortcut in favor of an
explicit `src_id` from the event, mirroring Probe's change:

```python
def run_analyze_loudness(job_id: str, src_id: str, jobs_table: str, work_bucket: str) -> None:
    audio_key = job.analysis_keys["audio"][src_id]   # was next(iter(...))
    ...
    dynamo.set_analysis_key(jobs_table, job_id, "loudness", src_id, loudness_key)
```
`analyze_scenes` still downloads from the **raw** bucket (needs real
frames, per its existing comment) using `job.sources[src_id].key` — no
change to that part.

### Evidence merge (`services/plan/evidence.py`)

```python
def build_evidence(
    per_source: dict[str, dict],   # {src_id: {"loudness": {...}|None, "scenes": {...}|None, "transcript": {...}|None}}
    music_tracks: list[dict],
) -> dict:
    sources = []
    loudness_points, scene_cuts, phrases = [], [], []
    for src_id, artifacts in per_source.items():
        duration = _source_duration((artifacts["loudness"] or {}).get("points", []))
        sources.append({"id": src_id, "duration": duration})
        loudness_points += [{"source": src_id, **p} for p in ...]
        scene_cuts += [{"source": src_id, **c} for c in _top_scenes(..., duration)]
        phrases += [{"source": src_id, **ph} for ph in _phrases_from_words(...)]
    return {
        "sources": sources,             # was: source_ids: list[str] (flat)
        "music_tracks": music_tracks,
        "loudness_points": loudness_points,
        "scene_cuts": scene_cuts,
        "phrases": phrases,
    }
```
`MAX_SCENES`/`MIN_SECONDS_PER_SCENE` capping (`_top_scenes`) now applies
**per source** (each source capped proportional to its own duration), not
globally — otherwise one long source could starve the others' scene
evidence. Token-cap accounting (Phase 4) is on the *serialized* evidence
size overall, unaffected in mechanism, just now naturally larger with more
sources — worth a quick sanity check against `plan_max_output_tokens`
input-side budget during testing (T-Test, not a code task).

### Plan (`services/plan/handler.py`)

```python
def run_plan(job_id, jobs_table, work_bucket, ...):
    job = dynamo.get_job(jobs_table, job_id)
    video_sources = sorted(job.analysis_keys.get("loudness", {}))   # only sources Analyze actually touched
    audio_assets = {sid: s for sid, s in job.sources.items() if s.kind == "audio"}

    per_source = {
        sid: {
            "loudness": _load(job.analysis_keys, "loudness", sid, work_bucket, tmp_dir),
            "scenes": _load(job.analysis_keys, "scenes", sid, work_bucket, tmp_dir),
            "transcript": _load(job.analysis_keys, "transcript", sid, work_bucket, tmp_dir),
        }
        for sid in video_sources
    }
    music_catalog = [{"id": k, "mood": v.get("mood", "")} for k, v in manifest.items()]
    music_catalog += [{"id": f"user:{sid}", "mood": "user-uploaded"} for sid in audio_assets]

    evidence = evidence_mod.build_evidence(per_source, music_catalog)
    ...
    sources_for_validation = {
        sid: SourceBounds(kind=s.kind, duration=s.duration) for sid, s in job.sources.items()
    }
    plan, meta = _plan_with_llm(planner, evidence, prompt_prefs, job.prefs, video_sources, {**bundled_track_ids, *(f"user:{s}" for s in audio_assets)}, sources_for_validation, log)
```
`_try_validate` threads `sources_for_validation` into `validate_plan(plan,
prefs, sources=sources_for_validation)`.

`_drop_unknown_music` — `valid_tracks` set extended to include
`f"user:{sid}"` for every `kind=="audio"` source of the job (in addition
to the bundled manifest ids it already checks).

`_remap_unknown_sources` — **unchanged** (ADR-2: still only fires for
`len(known_sources) == 1`; for N>1, unknown sources now get caught by the
new validator check instead, triggering the existing retry-then-fallback).

Fallback path (`_plan_with_llm` returns `None`):
```python
raw = fallback_planner.build_plan(
    {sid: {"points": (per_source[sid]["loudness"] or {}).get("points", []), "duration": job.sources[sid].duration}
     for sid in video_sources},
    job.prefs,
)
```

### Fallback planner (`services/plan/fallback_planner.py`)

`build_plan(src_id, loudness_points, prefs)` → `build_plan(sources:
dict[str, {"points": list[dict], "duration": float}], prefs)`. Ranks
loudness peaks **globally across all sources** (single sorted-by-level_db
pass over the union of all sources' points, each point remembering its
own `src_id`), builds each candidate clip's `±3s` window clamped to *that
point's own source* duration, and keeps the existing per-selected-clip
overlap check but scoped **per source** (two windows from different
sources never "overlap" — matches the validator's own per-source overlap
rule). `_fallback_single_clip` used only if the union of all sources
yields zero usable peaks (extremely short/silent session) — anchors to
the first video source in sorted order.

### Renderer music resolution (`renderer/compile.py`, `services/render/main.py`)

`renderer/compile.py`:
```python
def compile_plan(
    plan: EditPlan,
    sources: dict[str, Path],
    output_path: Path,
    ass_path: Path | None = None,
    music_override: Path | None = None,   # new
) -> CompiledCommand: ...

def _compile_music(plan, labels, cur_a, cur_duration, clip_count, music_override: Path | None):
    if music_override is None and not plan.audio.music_track:
        return [], [], cur_a
    music_path = music_override or music_presets.resolve_track(plan.audio.music_track)
    ...
```
Still AWS-free — `music_override` is just a `Path`, resolution of *what*
that path is (S3 download) happens entirely in `services/render/main.py`:

```python
def run_render_job(job_id, jobs_table, work_bucket, output_bucket):
    ...
    music_override = None
    track = plan.audio.music_track
    if track and track.startswith("user:"):
        asset_id = track.removeprefix("user:")
        music_override = storage.download(work_bucket, s3keys.work_asset_key(job_id, asset_id), tmp_dir)
    command = compile_plan(concat_plan, sources, out_path, music_override=music_override)
```
No new IAM grant needed — `render_task`'s existing `s3:GetObject` on
`${work bucket}/*` already covers `work/<job_id>/assets/*` (verified
against `infra/envs/dev/iam.tf`).

### Cut (`services/cut/handler.py`)

No functional change. Confirm (via T-Cut in Task Breakdown, test-only) that
`job.sources[clip.source]` can no longer `KeyError` in practice, because
ADR-2's validator check now rejects unknown sources before a plan ever
reaches Cut. `cutcache.profile_key(aspect, resolution)` unchanged — already
sufficient (ADR-1): the cache key already includes the source's raw S3 key,
so cache correctness across sources was never actually at risk; the
existing code comment pointing at "Phase 8's target_profile" should be
updated to stop implying a functional dependency that isn't coming.

## Non-Functional Requirements & Cross-Cutting Concerns

### Security (DESIGN §10)

- **No file references**: verified end-to-end in Constitution Alignment
  above — `user:<id>` resolution is always job-scoped-lookup, never a raw
  path.
- **ffmpeg/ffprobe untrusted-media surface**: ADR-4 keeps the audio codec
  allowlist unchanged rather than widening it for user-uploaded music;
  Probe remains the only place raw, attacker-controlled bytes are decoded
  (video *and* now audio-only uploads), still VPC-isolated/no-egress per
  the existing Phase 2 posture — no infra change needed there, `probe`'s
  IAM/network posture already applies uniformly to whatever `src_id` it's
  handed.
- **Denial-of-wallet**: session-level (not per-file) daily job quota —
  `claim_user_slot`/`claim_ip_slot` unchanged, called once per `POST
  /jobs` regardless of file count, so a 5-file session costs exactly one
  quota slot, same as a 1-file job. `SessionValidate`'s post-hoc duration
  re-check is the backstop against a session that lies at presign time
  (declares small files, they decode to more total video-seconds than
  size implied — unlikely given the size cap, but the doc explicitly asks
  for a Probe-time re-check, and this is where it lands with real N-source
  visibility).
- **Capability URLs**: unchanged model (Cognito-owned jobs, `_owned_job`);
  N presigned URLs per job instead of 1, same 60-min TTL, same
  `content-length-range` binding per object.

### Cost

- Analyze fan-out cost scales linearly with **video** file count (up to
  5×3=15 Lambda invocations vs. today's 3), bounded by the existing
  session cap. Per-job Bedrock cost line grows with merged-evidence size
  (more sources → more phrases/points/scenes in the prompt) — still
  bounded by the existing per-job token cap (Phase 4, `plan_max_output_tokens`
  + evidence compression), no new cap needed, but worth one measured data
  point in the README cost table (5-file session vs 1-file job) — folded
  into the Testing Strategy, not a separate infra task.
- No new idle cost: `SessionValidate` is a near-zero-invocation-time
  Lambda (DynamoDB read + arithmetic), same idle-cost class as `trigger`/
  `semaphore`.

### Observability

- `dynamo.start_step/finish_step` timing pattern extended to
  `"session_validate"` as a new named step, consistent with existing
  `"probe"`/`"cut"`/etc. entries in `job.timings`.
- X-Ray segments (`services/common/tracing.py::segment`) wrap the new
  Lambda the same way every other handler does — no new tracing code
  needed beyond the existing `with segment(__name__, job_id):` idiom.

### Backward compatibility

- Single-file jobs (dev EventBridge convenience path) continue to work
  unchanged — they're just a `source_items` list of length 1 flowing
  through the same Map states.
- `SourceRef`'s new fields are `Optional`, so any code path that
  constructs one without them (existing tests) keeps working.
- `set_analysis_key`'s signature change is breaking but internal-only (no
  external contract); all 4 call sites are enumerated above and must land
  in the same change.

### Quantified NFRs

| NFR | Target | Verified by |
|---|---|---|
| ProbeMap + SessionValidate wall-clock for a 5-file session | < 2 min added over today's single-file Probe latency (parallel Map, not serial) | manual timing during e2e test run, noted in README |
| Analyze fan-out for a 3-video session | completes within the existing per-Lambda timeouts unchanged (no timeout bump needed — each invocation still processes exactly one file) | existing Lambda timeout config unchanged, confirmed by T13/T15 |
| Session cap rejection | a session summing to >300s of video is rejected by `SessionValidate` before Analyze starts (not after burning transcription cost) | `tests/test_services_session_profile.py` (new) |

## Task Breakdown

Grouped by dependency; tasks within a group are largely parallelizable,
groups are ordered.

**Group A — shared foundations**

- [x] T1: Extend `SourceRef` (`services/common/models.py`) with
      `duration/width/height/fps`; add `dynamo.update_source` helper
      (depends on: none) — DoD: unit test writes/reads a source's new
      fields via `get_job`/`update_source` round-trip under `moto`.
- [x] T2: Fix `dynamo.set_analysis_key` to per-source nested writes (new
      4-arg signature); update all 4 call sites (`probe`,
      `analyze_loudness`, `analyze_scenes`, `analyze_transcribe`) and their
      existing tests (depends on: none) — DoD: a test asserts two
      concurrent `set_analysis_key` calls for different `src_id`s on the
      same category both survive (regression test for the race found
      during grounding).
- [x] T3: `services/common/s3keys.py::work_asset_key`; `services/common/
      session_caps.py` new module (`MAX_FILES`, `MAX_SESSION_VIDEO_SECONDS`)
      (depends on: none) — DoD: importable, used by T7/T12, unit-tested
      key-shape only.
- [x] T4: `renderer/edit_plan/validate.py` — `SourceBounds` dataclass;
      `validate_plan(plan, prefs, sources=None)` cross-file checks (ADR-2)
      (depends on: none) — DoD: unit tests for unknown source, audio-kind
      source used as a clip source, out-of-bounds timestamp, unknown
      `user:` music asset — all reject with a structural error string; a
      `sources=None` call preserves today's behavior exactly (regression).
- [x] T5: `renderer/probe.py` — `kind` field, audio-only `probe_file` path,
      `declared_kind` check in `validate_probe`, `ALLOWED_AUDIO_CODECS`
      widened to `{aac, opus, mp3}` (ADR-4), `extract_audio_asset`
      (48kHz stereo) (depends on: none) — DoD: unit tests with an
      audio-only fixture (see T-Fixtures) assert `kind="audio"`, no
      pixel-rate/video-codec errors, `mp3` content decodes to `mp3` codec
      and is **accepted**.

**Group B — upload/session API** (depends on Group A: T1, T3)

- [x] T6: `services/job_api/logic.py` — N-file `validate_create_request`,
      `ALLOWED_VIDEO_CONTENT_TYPES`/`ALLOWED_AUDIO_CONTENT_TYPES`,
      `build_job_item` for N sources, `src1..srcN` assignment, "at least
      one video source" rule — DoD: unit tests for 0/6-file rejection,
      per-file size rejection, mixed video+audio acceptance, all-audio
      rejection.
- [x] T7: `services/job_api/handler.py` — `_create_job` returns N presign
      targets; `_complete_upload` becomes `src_id`-aware; new `_start_job`
      implementing the 7-step contract above; route dispatch for `POST
      /jobs/{id}/start` — DoD: unit tests (moto) for: full happy path
      (create → upload → start → execution started), double-start no-op,
      start-before-upload-complete → 409 with correct missing list,
      start on an already-ANALYZING job → idempotent 200.
- [x] T8: `infra/envs/dev/apigateway.tf` — add `"POST /jobs/{id}/start"` to
      `job_api_routes` (authenticated) (depends on: T7) — DoD: `terraform
      plan` in dev shows the new route only, no drift elsewhere.
- [x] T9: `services/trigger/handler.py`/`logic.py` — multi-file guard
      (no-op on `len(job.sources) != 1`), `source_items` payload shape
      (depends on: T1) — DoD: unit test with a 3-source job's
      `ObjectCreated` event asserts no status flip, no `StartExecution`.

**Group C — Probe & session validation as Step Functions Map** (depends on
Group A, B)

- [x] T10: `services/probe/handler.py::run_probe` — explicit `src_id` param,
      kind-aware audio extraction, per-source metadata write (depends on:
      T1, T2, T5) — DoD: unit test probes a video source and an
      audio-only source from the same job, asserts correct S3 keys
      written for each (`work_audio_key` vs `work_asset_key`) and correct
      `SourceRef` fields persisted.
- [x] T11: `services/session_profile/handler.py` (new) —
      `run_session_profile`, `SessionCapExceeded` (depends on: T3, T10) —
      DoD: unit test with 3 video sources summing to 310s asserts
      `mark_failed` + no `video_source_items` returned; a 3+1(audio)
      session under cap returns exactly the 3 video `src_id`s and writes
      `target_profile`.
- [x] T12: `infra/envs/dev/statemachine.asl.json.tpl` — `ProbeMap`
      (replacing the single `Probe` task), `SessionValidate` task, wiring
      `$.source_items`/`$.video_source_items` per the Architecture diagram
      (depends on: T10, T11) — DoD: `terraform validate` passes; a
      hand-traced walk of the ASL JSON against the target diagram confirms
      `ResultPath` choices preserve `$.job_id` through every stage (no
      repeat of today's `Probe`'s `ResultPath: "$"` data-loss pattern).
- [x] T13: `infra/envs/dev/lambda.tf` + `iam.tf` — new `session_profile`
      Lambda (zip, boto3-only), IAM role (`dynamodb:GetItem/UpdateItem` on
      the jobs table only) (depends on: T11) — DoD: `terraform plan` shows
      exactly one new function + one new role + no other resource diffs.

**Group D — Analyze fan-out** (depends on Group C: T12)

- [x] T14: `services/analyze_{transcribe,loudness,scenes}/handler.py` —
      explicit `src_id` param, drop `next(iter(...))` (depends on: T2) —
      DoD: each handler's existing single-source test still passes
      unmodified in behavior, plus a new 2-source test per handler
      asserting both sources' artifacts land at distinct S3 keys.
- [x] T15: `infra/envs/dev/statemachine.asl.json.tpl` — convert
      `AnalyzeParallel`'s three branch Tasks into Maps over
      `$.video_source_items`, preserving the `TranscribeChoice` gate as a
      pre-Map Choice (depends on: T12, T14) — DoD: same
      `terraform validate` + traced-walk DoD as T12, plus explicit check
      that `TranscribeChoice`'s `SkipTranscribe` path still short-circuits
      the whole branch (not per-source) when `subtitles_enabled=false`.

**Group E — evidence & planning** (depends on Group D: T14)

- [x] T16: `services/plan/evidence.py::build_evidence` — multi-source merge
      with source-id tagging, per-source scene capping (depends on: none,
      parallelizable with D) — DoD: unit test with 2 sources' loudness/
      scenes/transcript asserts every emitted point/cut/phrase carries the
      correct `source` field and `evidence.sources` lists both with
      correct durations.
- [x] T17: `services/plan/fallback_planner.py::build_plan` — multi-source
      peak ranking (depends on: T16's shape) — DoD: unit test with 2
      sources' loudness points asserts selected clips reference the
      correct source per point, windows clamp to each source's own
      duration, no cross-source overlap false-positives.
- [x] T18: `services/plan/handler.py::run_plan` — load-all-video-sources,
      merged evidence call, `sources_for_validation` threading into
      `validate_plan`, `_drop_unknown_music` extended for `user:`
      namespace (depends on: T4, T16, T17) — DoD: `test_services_plan.py`
      extended with a 2-video-source + 1-audio-asset job: LLM plan
      referencing `src2` and `user:src3` validates; LLM plan referencing
      an invented `src9` is rejected pre-fallback (triggers the retry
      path, not a silent remap).
- [x] T19: `services/plan/prompt.py::SYSTEM_PROMPT` — multi-source framing,
      `user:` music mention (depends on: none) — DoD:
      `test_services_plan_prompt.py` snapshot updated; a manual read
      confirms the prompt still fits comfortably under the existing token
      budget with a 3-source evidence bundle (measured, not just eyeballed
      — log token count in the test).

**Group F — render-side music resolution** (depends on: T3)

- [x] T20: `renderer/compile.py` — `music_override` param threaded through
      `compile_plan`/`_compile_music` — DoD: unit test compiles a plan
      with a `Path` override and asserts the ffmpeg command references
      that literal path, no manifest lookup attempted.
- [x] T21: `services/render/main.py::run_render_job` — `user:` prefix
      detection, work-bucket asset download, override wiring (depends on:
      T20) — DoD: `test_services_render.py` extended with a `user:srcN`
      music track, asserting the correct `work_asset_key` is downloaded
      and passed through.

**Group G — Cut verification (no functional change)**

- [x] T22: Confirm/verify — add a regression test asserting an
      ADR-2-rejected plan never reaches `services/cut/handler.py` (i.e.
      the `KeyError` class of bug is now provably unreachable in the
      normal flow); update the stale "Phase 8's target_profile" comment
      in `services/common/cutcache.py` (depends on: T4) — DoD: test +
      comment update only, no logic change.

**Group H — frontend** (depends on: T7)

- [x] T23: `frontend/index.html`/`app.js` — multi-file `<input multiple>`
      picker (max 5 enforced client-side, server-side is the real
      boundary), per-file progress bars, "Start session" button calling
      `POST /jobs/{id}/start` once all files report upload-complete,
      session status view listing each source's `kind`/status — DoD:
      manual smoke test against a local/dev stack: pick 3 files, watch 3
      progress bars, click start, see status transition to ANALYZING.

**Group I — tests & fixtures**

- [x] T24: Mixed-profile normalization fixture — depends on the ADR-1
      decision being confirmed (blocks on Risk #1) — DoD: a small
      committed fixture pair under `assets/sample/` (e.g. `clip_1080p30.mp4`
      already exists as `clip_a.mp4`; add a second, deliberately
      different-profile clip — practically a modest resolution/fps
      difference, not a literal 4K60 file, to keep the repo small; note
      the substitution explicitly in the test's docstring/comment) proves
      a 2-source plan concatenates cleanly through `compile_plan` with no
      `xfade`/`concat` dimension-mismatch ffmpeg error.
- [x] T25: Cross-file plan validation tests — `tests/test_renderer_edit_plan_validate.py`
      (new, or extend existing validate tests) (depends on: T4) — DoD:
      covers every case listed in T4's DoD, plus a full multi-source
      `EditPlan` that validates cleanly.
- [x] T26: Audio-only source handling tests — `tests/test_services_probe.py`
      extended, `tests/test_services_plan.py` extended (depends on: T5,
      T18) — DoD: an audio-only source probes successfully, is excluded
      from `video_source_items`, and appears in `evidence.music_tracks` as
      `user:<id>`.
- [x] T27: End-to-end multi-file test — `tests/test_pipeline_end_to_end.py`
      extended with a 2-video + 1-audio session run through
      `probe → session_profile → analyze × 2 → plan (fallback) → cut →
      render → finish` under `moto` (depends on: T10, T11, T14, T18, T21)
      — DoD: asserts the final montage's clips reference both video
      sources and the music track resolves to the uploaded audio asset.
- [x] T28: Update all existing tests broken by signature changes
      (`set_analysis_key` call sites, `run_probe`, analyze handlers'
      `next(iter(...))` removal, `job_api` create/complete request/response
      shape) (depends on: T1–T21 as applicable) — DoD: full existing test
      suite green, no skips introduced.

## Testing Strategy

- **Unit, no ffmpeg/AWS**: `renderer/edit_plan/validate.py` cross-file
  checks (T25), `evidence.py` merge logic (T16), `fallback_planner.py`
  multi-source ranking (T17) — pure Python, fast, no `moto`.
- **Unit, `moto`-backed (no real AWS, matches existing `aws_stack` fixture
  pattern in `tests/conftest.py`)**: every Lambda handler change (T7, T9,
  T10, T11, T14, T18, T21) — same pattern as today's
  `test_services_plan.py`/`test_pipeline_end_to_end.py`.
- **Integration/e2e, `media` marker (real ffmpeg, per existing convention)**:
  T24's mixed-profile fixture run through `compile_plan`, T27's full
  session pipeline test.
- **`renderer/` stays testable with no AWS credentials** (constitution
  principle V / CLAUDE.md's AWS-free rule): verified by construction —
  every renderer-side change in this plan (T4, T5's `probe.py` bits, T20)
  takes plain dataclasses/`Path`s, never a `boto3` client; only
  `services/*` handlers touch AWS, consistent with the existing split.
- Explicit phase-8.md test list mapped: **cross-file plan validation** →
  T25; **mixed-profile normalization fixture (1080p30 + 4K60)** → T24
  (with the fixture-size caveat noted); **audio-only source handling** →
  T26.

## Risks & Open Questions

**Decisions (2026-07-30) — items 1-4 below are resolved, kept for record:**

1. **ADR-1: resolved — informational only, as recommended.** `target_profile`
   is computed/stored metadata; Cut keeps its existing fixed-constant
   normalization. No change to `compile.py`/`segments.py`/`cutcache.py`.
2. **ADR-4: resolved — mp3 accepted, overriding this plan's original
   recommendation.** `ALLOWED_AUDIO_CODECS` widens to `{aac, opus, mp3}`;
   `POST /jobs` accepts `audio/mpeg`. Accepted trade-off: wider untrusted-
   media decoder surface in Probe, for demo/UX value ("upload your own
   song" without a conversion step).
3. **Cap scope: resolved — video-only, as recommended (Assumption #1).**
   The ≤300s session cap sums only `kind == "video"` source durations;
   music files still count toward the 5-file and 500MB/file caps.
4. **Rerender prefs gap: resolved — fix bundled into this phase.**
   `services/job_api/handler.py::_rerender` will pass `prefs` to
   `validate_plan` in the same change that threads `sources=` through that
   call site (T18/T-adjacent), closing the pre-existing `output.aspect`/
   `output.max_duration` field-authority gap (DESIGN §3) as part of Phase 8
   rather than as a separate follow-up.

**Still open / worth tracking during implementation:**

5. **Phone-video rotation metadata is untested.** `_compile_clips`'s
   scale/crop chain relies on ffmpeg's default autorotate behavior for
   files carrying a `displaymatrix` rotation side-data (common for
   portrait phone footage). This has never been exercised by the existing
   single-landscape-clip fixture set. Recommend T24's new fixture
   deliberately include a portrait/rotated clip to catch this rather than
   discover it live at the demo.
6. **Evidence bundle size at N=5 sources vs. the Phase 4 token cap** is
   asserted-but-not-yet-measured (T19's DoD asks for a logged count, not a
   guaranteed pass) — if a full 5-video-source session's evidence
   routinely approaches `plan_max_output_tokens`'s *input*-side budget,
   the existing per-source scene/phrase capping in `evidence.py` may need
   tightening (smaller `MAX_SCENES`, coarser phrase gap) as a follow-up,
   not blocking this phase but worth flagging now.
7. **No `.specify/memory/constitution.md` exists in this repository** —
   confirmed by filesystem search; a file of that name exists only in an
   unrelated sibling project on this machine. This plan treats
   `CLAUDE.md` + `docs/DESIGN.md` + `docs/EFFECTS.md` as the operative
   constraints instead, per the instruction to surface rather than
   silently paper over such a mismatch.
