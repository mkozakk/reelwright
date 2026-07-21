from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .edit_plan.models import EditPlan
from .presets import color as color_presets
from .presets import music as music_presets

REPO_ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = REPO_ROOT / "assets" / "fonts"

VIDEO_CODEC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20"]
AUDIO_CODEC = ["-c:a", "aac", "-b:a", "192k"]
AUDIO_FORMAT = "aformat=sample_rates=48000:channel_layouts=stereo"

RESOLUTION_BASE = {"1080p": 1080, "720p": 720}
MUSIC_FADE_SECONDS = 2.0


class CompileError(RuntimeError):
    pass


def target_dimensions(aspect: str, resolution: str) -> tuple[int, int]:
    base = RESOLUTION_BASE[resolution]
    ar_w, ar_h = (int(part) for part in aspect.split(":"))
    if ar_w >= ar_h:
        height = base
        width = round(base * ar_w / ar_h / 2) * 2
    else:
        width = base
        height = round(base * ar_h / ar_w / 2) * 2
    return width, height


def escape_filter_path(path: Path) -> str:
    text = str(path)
    text = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return text


@dataclass
class _Labeler:
    counter: int = 0

    def next(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"


@dataclass
class CompiledCommand:
    args: list[str] = field(default_factory=list)


def _compile_clips(plan: EditPlan, sources: dict[str, Path], labels: _Labeler):
    inputs: list[str] = []
    filters: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []
    durations: list[float] = []

    width, height = target_dimensions(plan.output.aspect, plan.output.resolution)

    for i, clip in enumerate(plan.clips):
        source_path = sources.get(clip.source)
        if source_path is None:
            raise CompileError(f"no source registered for id '{clip.source}'")
        inputs += ["-ss", str(clip.start), "-to", str(clip.end), "-i", str(source_path)]

        v_in, a_in = f"{i}:v", f"{i}:a"
        v_out, a_out = labels.next("v"), labels.next("a")

        if clip.speed != 1.0:
            v_chain = f"setpts={1 / clip.speed:.6f}*PTS,fps=30"
            a_chain = f"atempo={clip.speed:.6f},{AUDIO_FORMAT}"
        else:
            v_chain = "setpts=PTS-STARTPTS,fps=30"
            a_chain = f"asetpts=PTS-STARTPTS,{AUDIO_FORMAT}"
        v_chain += f",scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"

        filters.append(f"[{v_in}]{v_chain}[{v_out}]")
        filters.append(f"[{a_in}]{a_chain}[{a_out}]")

        duration = (clip.end - clip.start) / clip.speed

        transition_in = plan.clips[i - 1].transition_out if i > 0 else None
        if transition_in and transition_in.type == "fade_to_black" and transition_in.duration > 0:
            d = transition_in.duration
            fv, fa = labels.next("vfin"), labels.next("afin")
            filters.append(f"[{v_out}]fade=t=in:st=0:d={d:.3f}[{fv}]")
            filters.append(f"[{a_out}]afade=t=in:st=0:d={d:.3f}[{fa}]")
            v_out, a_out = fv, fa

        transition_out = clip.transition_out
        if transition_out and transition_out.type == "fade_to_black" and transition_out.duration > 0:
            d = transition_out.duration
            st = max(0.0, duration - d)
            fv, fa = labels.next("vfout"), labels.next("afout")
            filters.append(f"[{v_out}]fade=t=out:st={st:.3f}:d={d:.3f}[{fv}]")
            filters.append(f"[{a_out}]afade=t=out:st={st:.3f}:d={d:.3f}[{fa}]")
            v_out, a_out = fv, fa

        video_labels.append(v_out)
        audio_labels.append(a_out)
        durations.append(duration)

    return inputs, filters, video_labels, audio_labels, durations


def _compile_junctions(plan: EditPlan, labels: _Labeler, video_labels, audio_labels, durations):
    filters: list[str] = []
    cur_v, cur_a = video_labels[0], audio_labels[0]
    cur_duration = durations[0]

    for i in range(1, len(plan.clips)):
        transition = plan.clips[i - 1].transition_out
        transition_type = transition.type if transition else "cut"
        v_next, a_next = video_labels[i], audio_labels[i]

        if transition_type == "crossfade" and transition and transition.duration > 0:
            d = transition.duration
            offset = max(0.0, cur_duration - d)
            v_out, a_out = labels.next("vx"), labels.next("ax")
            filters.append(
                f"[{cur_v}][{v_next}]xfade=transition=fade:duration={d:.3f}:offset={offset:.3f}[{v_out}]"
            )
            filters.append(f"[{cur_a}][{a_next}]acrossfade=d={d:.3f}[{a_out}]")
            cur_duration = cur_duration + durations[i] - d
        else:
            v_out, a_out = labels.next("vc"), labels.next("ac")
            filters.append(
                f"[{cur_v}][{cur_a}][{v_next}][{a_next}]concat=n=2:v=1:a=1[{v_out}][{a_out}]"
            )
            cur_duration = cur_duration + durations[i]

        cur_v, cur_a = v_out, a_out

    return filters, cur_v, cur_a, cur_duration


def _compile_color(plan: EditPlan, labels: _Labeler, cur_v: str):
    filters: list[str] = []
    adjust = plan.color.adjust
    needs_eq = adjust.contrast != 1.0 or adjust.saturation != 1.0 or adjust.brightness != 0.0

    if plan.color.preset != "none":
        lut_path = color_presets.lut_path_for(plan.color.preset)
        v_out = labels.next("vlut")
        filters.append(f"[{cur_v}]lut3d=file='{escape_filter_path(lut_path)}'[{v_out}]")
        cur_v = v_out

    if needs_eq:
        v_out = labels.next("veq")
        filters.append(
            f"[{cur_v}]eq=contrast={adjust.contrast:.4f}:"
            f"saturation={adjust.saturation:.4f}:"
            f"brightness={adjust.brightness:.4f}[{v_out}]"
        )
        cur_v = v_out

    return filters, cur_v


def _compile_subtitles(labels: _Labeler, cur_v: str, ass_path: Path | None):
    if ass_path is None:
        return [], cur_v
    v_out = labels.next("vsub")
    option = (
        f"filename='{escape_filter_path(ass_path)}':fontsdir='{escape_filter_path(FONTS_DIR)}'"
    )
    return [f"[{cur_v}]subtitles={option}[{v_out}]"], v_out


def _compile_music(plan: EditPlan, labels: _Labeler, cur_a: str, cur_duration: float, clip_count: int):
    if not plan.audio.music_track:
        return [], [], cur_a

    music_path = music_presets.resolve_track(plan.audio.music_track)
    music_index = clip_count
    inputs = ["-stream_loop", "-1", "-i", str(music_path)]

    fade_start = max(0.0, cur_duration - MUSIC_FADE_SECONDS)
    fade_dur = min(MUSIC_FADE_SECONDS, cur_duration)

    m_in = f"{music_index}:a"
    m_trim = labels.next("mtrim")
    filters = [
        f"[{m_in}]atrim=0:{cur_duration:.3f},asetpts=PTS-STARTPTS,{AUDIO_FORMAT},"
        f"volume={plan.audio.music_gain_db:.2f}dB,"
        f"afade=t=out:st={fade_start:.3f}:d={fade_dur:.3f}[{m_trim}]"
    ]

    if plan.audio.duck_under_speech:
        split_key, split_dry = labels.next("asplit"), labels.next("asplit")
        filters.append(f"[{cur_a}]asplit=2[{split_key}][{split_dry}]")
        m_duck = labels.next("mduck")
        filters.append(
            f"[{m_trim}][{split_key}]sidechaincompress="
            f"threshold=0.05:ratio=8:attack=5:release=250[{m_duck}]"
        )
        a_out = labels.next("amix")
        filters.append(
            f"[{split_dry}][{m_duck}]amix=inputs=2:duration=first:dropout_transition=0[{a_out}]"
        )
    else:
        a_out = labels.next("amix")
        filters.append(
            f"[{cur_a}][{m_trim}]amix=inputs=2:duration=first:dropout_transition=0[{a_out}]"
        )

    return inputs, filters, a_out


def compile_plan(
    plan: EditPlan,
    sources: dict[str, Path],
    output_path: Path,
    ass_path: Path | None = None,
) -> CompiledCommand:
    if not plan.clips:
        raise CompileError("plan has no clips")

    labels = _Labeler()
    all_filters: list[str] = []

    clip_inputs, clip_filters, video_labels, audio_labels, durations = _compile_clips(
        plan, sources, labels
    )
    all_filters += clip_filters

    junction_filters, cur_v, cur_a, cur_duration = _compile_junctions(
        plan, labels, video_labels, audio_labels, durations
    )
    all_filters += junction_filters

    color_filters, cur_v = _compile_color(plan, labels, cur_v)
    all_filters += color_filters

    subtitle_filters, cur_v = _compile_subtitles(labels, cur_v, ass_path)
    all_filters += subtitle_filters

    music_inputs, music_filters, cur_a = _compile_music(
        plan, labels, cur_a, cur_duration, len(plan.clips)
    )
    all_filters += music_filters

    args = ["-y", *clip_inputs, *music_inputs]
    args += ["-filter_complex", ";".join(all_filters)]
    args += ["-map", f"[{cur_v}]", "-map", f"[{cur_a}]"]
    args += VIDEO_CODEC + AUDIO_CODEC
    args += ["-movflags", "+faststart", "-t", f"{cur_duration:.3f}", str(output_path)]

    return CompiledCommand(args=args)
