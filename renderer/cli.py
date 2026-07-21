from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compile import compile_plan
from .edit_plan.validate import EditPlanValidationError, load_plan, validate_plan
from .ffmpeg_run import FfmpegError, run_ffmpeg
from .subtitles import build_ass


def _parse_kv(items: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"expected id=path, got '{item}'")
        key, _, value = item.partition("=")
        result[key] = Path(value)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="renderer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render")
    render.add_argument("plan", type=Path)
    render.add_argument("output", type=Path)
    render.add_argument("--source", action="append", default=[], metavar="ID=PATH")
    render.add_argument("--transcript", action="append", default=[], metavar="ID=PATH")
    render.add_argument("--aspect", choices=["16:9", "9:16", "1:1"], default=None)
    render.add_argument("--max-duration", type=float, default=None)

    return parser


def _run_render(args: argparse.Namespace) -> int:
    sources = _parse_kv(args.source)
    transcripts = _parse_kv(args.transcript)

    if not sources:
        print("error: at least one --source ID=PATH is required", file=sys.stderr)
        return 2

    try:
        plan = load_plan(args.plan)
        prefs: dict = {}
        if args.aspect is not None:
            prefs["aspect"] = args.aspect
        if args.max_duration is not None:
            prefs["max_duration"] = args.max_duration
        plan = validate_plan(plan, prefs)
    except EditPlanValidationError as exc:
        for error in exc.errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    ass_path = None
    if plan.subtitles.enabled:
        transcript_id = plan.clips[0].source
        transcript_path = transcripts.get(transcript_id)
        if transcript_path is None:
            print(
                f"error: subtitles enabled but no --transcript for '{transcript_id}'",
                file=sys.stderr,
            )
            return 2
        ass_path = args.output.with_suffix(".ass")
        build_ass(transcript_path, plan.subtitles.style, plan.subtitles.mode, ass_path)

    try:
        command = compile_plan(plan, sources, args.output, ass_path)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(command.args)
    except FfmpegError as exc:
        print(f"error: ffmpeg failed: {exc}", file=sys.stderr)
        return 1

    print(f"rendered {args.output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "render":
        return _run_render(args)

    parser.error("unknown command")
    return 2
