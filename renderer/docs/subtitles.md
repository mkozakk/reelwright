# Subtitles (`subtitles.py`)

`build_ass(transcript_path, style, mode, output_path)` turns Whisper's word-level timestamps into a `.ass` file. It never uses the model's own wording, only what was actually said on camera, so a caption is always traceable back to the transcript.

## Sanitizing and grouping

`load_words` reads `{"words": [{start, end, text}, ...]}` and strips non-printable characters plus `\`, `{`, `}` via `sanitize_text`, so transcript text (which could contain anything a speaker said, including characters that mean something to the ASS format) can never inject an ASS override tag.

`group_lines` breaks words into caption lines on two triggers: a line reaches `MAX_WORDS_PER_LINE = 7` words, or the gap since the previous word exceeds `WORD_GAP_BREAK_SECONDS = 0.6`s, since a pause that long reads as a new thought.

## Two rendering modes

- `build_phrase_line` emits one `Dialogue:` line per caption group (`mode = phrase`).
- `build_karaoke_line` emits the same line with per-word `{\kNN}` karaoke tags, each sized from that word's actual spoken duration (`mode = word_highlight`), so the highlight timing matches the audio rather than an even split.

## Styling

`build_header` picks one of two hand-tuned `STYLE_BLOCKS`, `bold-bottom` or `lower-third`, differing in font size, colors, and margins, and prepends the fixed ASS `[Script Info]`/`[V4+ Styles]` boilerplate every `.ass` file needs.

## Related

- [[transcribe]]: produces the word-timestamped transcript this module consumes
- [[compile]]: burns the resulting `.ass` file into the final video via the `subtitles=` filter
