from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

WORD_GAP_BREAK_SECONDS = 0.6
MAX_WORDS_PER_LINE = 7

STYLE_BLOCKS = {
    "bold-bottom": (
        "Style: Default,DejaVu Sans,72,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,1,2,60,60,60,1"
    ),
    "lower-third": (
        "Style: Default,DejaVu Sans,48,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2,1,1,80,80,140,1"
    ),
}


@dataclass
class Word:
    start: float
    end: float
    text: str


def sanitize_text(text: str, max_len: int | None = None) -> str:
    cleaned = "".join(ch for ch in text if ch.isprintable())
    cleaned = cleaned.replace("\\", "").replace("{", "").replace("}", "")
    cleaned = cleaned.strip()
    if max_len is not None:
        cleaned = cleaned[:max_len]
    return cleaned


def load_words(transcript_path: Path) -> list[Word]:
    data = json.loads(Path(transcript_path).read_text())
    words: list[Word] = []
    for entry in data["words"]:
        text = sanitize_text(entry["text"])
        if not text:
            continue
        words.append(Word(start=float(entry["start"]), end=float(entry["end"]), text=text))
    return words


def group_lines(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    current: list[Word] = []
    previous_end: float | None = None
    for word in words:
        breaks_line = current and (
            len(current) >= MAX_WORDS_PER_LINE
            or (previous_end is not None and word.start - previous_end > WORD_GAP_BREAK_SECONDS)
        )
        if breaks_line:
            lines.append(current)
            current = []
        current.append(word)
        previous_end = word.end
    if current:
        lines.append(current)
    return lines


def format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    centis = round((secs - int(secs)) * 100)
    whole_secs = int(secs)
    if centis == 100:
        centis = 0
        whole_secs += 1
    return f"{hours}:{minutes:02d}:{whole_secs:02d}.{centis:02d}"


def build_header(style: str) -> str:
    style_line = STYLE_BLOCKS[style]
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style_line}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def build_phrase_line(line: list[Word]) -> str:
    text = " ".join(word.text for word in line)
    start = format_time(line[0].start)
    end = format_time(line[-1].end)
    return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"


def build_karaoke_line(line: list[Word]) -> str:
    parts = []
    for word in line:
        duration_cs = max(1, round((word.end - word.start) * 100))
        parts.append(f"{{\\k{duration_cs}}}{word.text}")
    text = " ".join(parts)
    start = format_time(line[0].start)
    end = format_time(line[-1].end)
    return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"


def build_ass(transcript_path: Path, style: str, mode: str, output_path: Path) -> Path:
    words = load_words(transcript_path)
    lines = group_lines(words)
    builder = build_karaoke_line if mode == "word_highlight" else build_phrase_line

    body = build_header(style)
    for line in lines:
        body += builder(line)

    output_path = Path(output_path)
    output_path.write_text(body, encoding="utf-8")
    return output_path
