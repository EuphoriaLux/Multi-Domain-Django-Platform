"""Silence a stretch of an audio file you already have, for a quiz music round.

Upload the result as the question's audio (Media > Audio > Upload) and put
{{word}} in the question text. Guests hear the song with a gap where the word
is, then the word appears on screen at the reveal.

This only edits a file you pass in. It does not download anything: get the
track yourself (a purchase, your own library, a licensed source).

    python scripts/mute_audio_segment.py toxic.mp3 --mute 1:12.4 1:13.6
    python scripts/mute_audio_segment.py toxic.mp3 --mute 1:12.4 1:13.6 \\
        --clip 0:55 1:30 --out toxic_quiz.mp3

Times are seconds (72.4) or [HH:]MM:SS(.ms) (1:12.4). --mute can be repeated.
--clip keeps only that window, so a long song becomes a short clip.
Needs ffmpeg on PATH (Windows: ``winget install Gyan.FFmpeg``).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def parse_time(value):
    """Seconds from ``72.4``, ``1:12.4`` or ``0:01:12.4``."""
    seconds = 0.0
    for part in value.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def build_command(source, out, mutes, clip):
    # volume=0 between the timestamps; one filter per muted stretch. A tiny
    # fade would soften the cut, but a hard cut is clearer for a guessing game.
    filters = [f"volume=enable='between(t,{a:.3f},{b:.3f})':volume=0" for a, b in mutes]
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if clip:
        command += ["-ss", f"{clip[0]:.3f}", "-to", f"{clip[1]:.3f}"]
    command += ["-i", str(source)]
    if filters:
        command += ["-af", ",".join(filters)]
    command += ["-c:a", "libmp3lame", "-q:a", "3", str(out)]
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", type=Path, help="audio file you already have")
    parser.add_argument(
        "--mute",
        nargs=2,
        action="append",
        metavar=("START", "END"),
        required=True,
        help="stretch to silence (repeatable)",
    )
    parser.add_argument(
        "--clip",
        nargs=2,
        metavar=("START", "END"),
        help="keep only this window (the mute times are in the ORIGINAL file)",
    )
    parser.add_argument("--out", type=Path, help="default: <name>_quiz.mp3")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        sys.exit(f"File not found: {args.source}")
    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found. Install it (Windows: winget install Gyan.FFmpeg).")

    mutes = [(parse_time(a), parse_time(b)) for a, b in args.mute]
    for a, b in mutes:
        if b <= a:
            sys.exit(f"Mute end must be after its start ({a}s to {b}s).")
    clip = tuple(parse_time(t) for t in args.clip) if args.clip else None
    if clip and clip[1] <= clip[0]:
        sys.exit("Clip end must be after its start.")

    out = args.out or args.source.with_name(args.source.stem + "_quiz.mp3")
    if out.resolve() == args.source.resolve():
        sys.exit("Output would overwrite the source file; choose another --out.")

    # With -ss before -i the filter timestamps restart at 0, so shift the
    # mutes to be relative to the clip start.
    if clip:
        mutes = [(a - clip[0], b - clip[0]) for a, b in mutes]
        mutes = [(max(a, 0.0), b) for a, b in mutes if b > 0]

    subprocess.run(build_command(args.source, out, mutes, clip), check=True)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
