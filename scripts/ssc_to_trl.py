#!/usr/bin/env python3
"""Convert a StepMania SSC file to a Tap Revolution .trl chart.

Only dance-single (4-lane) step types are supported. Hold heads (type 2/4) are
converted to taps; hold/roll tails (3) and mines (M) are dropped. Full hold and
mine support is tracked in future work.

Usage:
    python scripts/ssc_to_trl.py song/steps.ssc -o levels/song.trl
    python scripts/ssc_to_trl.py song/steps.ssc -l          # list charts
    python scripts/ssc_to_trl.py song/steps.ssc -d Hard      # pick difficulty
"""

import argparse
import glob
import os
import re
import shutil
import sys
from typing import Dict, List, Optional, Tuple


# SSC dance-single column order → our lane identifiers
_SSC_LANE = {0: 'L', 1: 'D', 2: 'U', 3: 'R'}
_LANE_ORDER = ['L', 'D', 'U', 'R']

# SSC note characters that become taps (hold heads treated as tap-on-arrival)
_TAP_CHARS = frozenset('124')


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert a StepMania SSC file to Tap Revolution .trl format."
    )
    p.add_argument('input', help="SSC file to read, or a directory containing a .ssc and .ogg")
    p.add_argument('-o', '--output',
                   help="Output .trl file or directory (required when input is a directory; "
                        "if a directory, filename is inferred from the track name)")
    p.add_argument('--audio-dir',
                   help="Directory to copy the .ogg into (required when input is a directory)")
    p.add_argument('--overwrite', action='store_true',
                   help="Overwrite existing output files (default: error if any file exists)")
    p.add_argument(
        '-d', '--difficulty',
        help="Chart difficulty to convert (e.g. Challenge, Hard, Medium, Easy)",
    )
    p.add_argument(
        '-l', '--list', action='store_true',
        help="List available charts and exit",
    )
    p.add_argument(
        '--time-mode', action='store_true',
        help="Force time-mode output even when BPM is constant",
    )
    p.add_argument(
        '--scroll-time', type=float, default=2.0, metavar='SECS',
        help="Seconds for an arrow to cross the display (default: 2.0)",
    )
    return p


# ---------------------------------------------------------------------------
# SSC parsing
# ---------------------------------------------------------------------------

def parse_ssc(content) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """Return (global_fields, charts).

    global_fields holds top-level #KEY:VALUE; pairs (TITLE, ARTIST, BPMS,
    OFFSET, …). charts is a list of per-NOTEDATA dicts (STEPSTYPE, DIFFICULTY,
    NOTES, …).
    """
    pairs = re.findall(r'#([^:]+):(.*?);', content, re.DOTALL)
    global_fields: Dict[str, str] = {}
    charts: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None

    for raw_key, raw_val in pairs:
        key = raw_key.strip().upper()
        val = raw_val.strip()
        if key == 'NOTEDATA':
            if current is not None:
                charts.append(current)
            current = {}
        elif current is not None:
            current[key] = val
        else:
            global_fields[key] = val

    if current is not None:
        charts.append(current)

    return global_fields, charts


def parse_bpms(bpms_str) -> List[Tuple[float, float]]:
    """Parse 'beat=BPM,…' into a sorted list of (beat, bpm) segments."""
    segments = []
    for seg in bpms_str.split(','):
        seg = seg.strip()
        if '=' in seg:
            beat_s, bpm_s = seg.split('=', 1)
            segments.append((float(beat_s.strip()), float(bpm_s.strip())))
    return sorted(segments)


def parse_notes(notes_str) -> List[Tuple[float, str]]:
    """Parse SSC #NOTES value into a list of (beat, lane) pairs.

    Measures are separated by commas. The number of rows in a measure sets the
    subdivision (4 rows = quarter notes, 16 rows = 16ths, etc.).  Simultaneous
    notes in the same row are returned as separate entries with the same beat
    number; write_trl groups them into chord notation.

    Hold heads (2/4) → tap.  Tails (3) and mines (M) → dropped.
    """
    notes = []
    current_beat = 0.0

    for measure in notes_str.split(','):
        rows = [r.strip() for r in measure.strip().splitlines() if r.strip()]
        if not rows:
            current_beat += 4.0
            continue
        beat_per_row = 4.0 / len(rows)
        for i, row in enumerate(rows):
            beat = current_beat + i * beat_per_row
            for col, ch in enumerate(row[:4]):
                if ch in _TAP_CHARS:
                    notes.append((beat, _SSC_LANE[col]))
        current_beat += 4.0

    return notes


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def beat_to_time(beat, bpm_segments: List[Tuple[float, float]], offset) -> float:
    """Convert a beat number to absolute seconds given BPM change segments."""
    t = offset
    for i, (seg_beat, bpm) in enumerate(bpm_segments):
        next_beat = bpm_segments[i + 1][0] if i + 1 < len(bpm_segments) else float('inf')
        end = min(beat, next_beat)
        t += (end - seg_beat) * 60.0 / bpm
        if beat <= next_beat:
            break
    return t


def _fmt(value) -> str:
    """Format a numeric value: drop trailing zeros, keep enough precision."""
    return f'{value:.10g}'


def _clean_title(title) -> str:
    """Strip leading bracket tags like '[09] ' that ITL Online prepends to titles."""
    return re.sub(r'^\[\d+\]\s*', '', title).strip()


def _title_to_filename(title) -> str:
    """Convert a track title to a snake_case .trl filename."""
    return re.sub(r'\s+', '_', title).lower() + '.trl'


def write_trl(global_fields: Dict[str, str], bpm_segments: List[Tuple[float, float]],
              offset, notes: List[Tuple[float, str]], scroll_time, force_time_mode, out,
              audio=None):
    """Write .trl content to *out* (a writable file object)."""
    title = _clean_title(global_fields.get('TITLE', 'Unknown'))
    artist = global_fields.get('ARTIST', '')
    constant_bpm = len(bpm_segments) == 1
    use_beat_mode = constant_bpm and not force_time_mode

    out.write(f"name: {title}\n")
    if artist:
        out.write(f"artist: {artist}\n")
    if scroll_time != 2.0:
        out.write(f"scroll_time: {_fmt(scroll_time)}\n")

    if use_beat_mode:
        bpm = bpm_segments[0][1]
        out.write(f"mode: beat\n")
        out.write(f"bpm: {_fmt(bpm)}\n")
        if offset != 0.0:
            out.write(f"offset: {_fmt(offset)}\n")
    else:
        out.write(f"mode: time\n")

    if audio:
        out.write(f"audio: {audio}\n")

    out.write('\n')

    # Group same-beat notes into chords, preserving L/D/U/R order
    by_beat: Dict[float, List[str]] = {}
    for beat, lane in notes:
        by_beat.setdefault(beat, []).append(lane)

    for beat in sorted(by_beat):
        lanes = sorted(by_beat[beat], key=lambda l: _LANE_ORDER.index(l))
        if use_beat_mode:
            val = _fmt(beat)
        else:
            val = _fmt(beat_to_time(beat, bpm_segments, offset))
        out.write(f"{val} {', '.join(lanes)}\n")


# ---------------------------------------------------------------------------
# Chart selection
# ---------------------------------------------------------------------------

def _dance_single_charts(charts: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [c for c in charts if c.get('STEPSTYPE', '').lower() == 'dance-single']


def list_charts(charts: List[Dict[str, str]], file=sys.stdout):
    for i, c in enumerate(charts):
        stype = c.get('STEPSTYPE', '?')
        diff  = c.get('DIFFICULTY', '?')
        meter = c.get('METER', '?')
        name  = c.get('CHARTNAME', '')
        line  = f"  [{i}] {stype} / {diff} (meter {meter})"
        if name:
            line += f" — {name}"
        print(line, file=file)


def select_chart(charts: List[Dict[str, str]], difficulty) -> Dict[str, str]:
    dance = _dance_single_charts(charts)
    if not dance:
        sys.exit("Error: no dance-single charts found in this file.")

    if difficulty:
        matches = [c for c in dance if c.get('DIFFICULTY', '').lower() == difficulty.lower()]
        if not matches:
            print(f"Error: no dance-single chart with difficulty '{difficulty}'.",
                  file=sys.stderr)
            print("Available dance-single charts:", file=sys.stderr)
            list_charts(dance, file=sys.stderr)
            sys.exit(1)
        if len(matches) > 1:
            print(f"Warning: {len(matches)} charts match '{difficulty}', using first.",
                  file=sys.stderr)
        return matches[0]

    if len(dance) == 1:
        return dance[0]

    print("Error: multiple dance-single charts found. Choose one with -d:", file=sys.stderr)
    list_charts(dance, file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def load_ssc(path) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    with open(path) as f:
        return parse_ssc(f.read())


def find_dir_files(dir_path) -> Tuple[str, str]:
    """Return (ssc_path, ogg_path) from a chart directory."""
    ssc_files = glob.glob(os.path.join(dir_path, '*.ssc'))
    ogg_files = glob.glob(os.path.join(dir_path, '*.ogg'))
    if not ssc_files:
        sys.exit(f"Error: no .ssc file found in {dir_path}")
    if len(ssc_files) > 1:
        sys.exit(f"Error: multiple .ssc files found in {dir_path}")
    if not ogg_files:
        sys.exit(f"Error: no .ogg file found in {dir_path}")
    return ssc_files[0], ogg_files[0]


def convert(args):
    ssc_path = args.input
    ogg_path = None
    audio_filename = None

    if os.path.isdir(args.input):
        if not args.output:
            sys.exit("Error: --output is required when input is a directory")
        if not args.audio_dir:
            sys.exit("Error: --audio-dir is required when input is a directory")
        ssc_path, ogg_path = find_dir_files(args.input)
        audio_filename = os.path.basename(ogg_path)

    global_fields, charts = load_ssc(ssc_path)

    if args.list:
        list_charts(charts)
        return

    chart = select_chart(charts, args.difficulty)

    bpm_segments = parse_bpms(global_fields.get('BPMS', '0=120'))
    if not bpm_segments:
        bpm_segments = [(0.0, 120.0)]
    if len(bpm_segments) > 1 and not args.time_mode:
        print("Note: BPM changes detected — using time mode.", file=sys.stderr)

    offset = float(global_fields.get('OFFSET', 0.0))
    notes  = parse_notes(chart.get('NOTES', ''))

    output_path = args.output
    if output_path and os.path.isdir(output_path):
        title = _clean_title(global_fields.get('TITLE', 'Unknown'))
        output_path = os.path.join(output_path, _title_to_filename(title))

    if not args.overwrite:
        conflicts = []
        if output_path and os.path.exists(output_path):
            conflicts.append(output_path)
        if ogg_path is not None:
            audio_dest = os.path.join(args.audio_dir, audio_filename)
            if os.path.exists(audio_dest):
                conflicts.append(audio_dest)
        if conflicts:
            for path in conflicts:
                print(f"Error: file already exists: {path}", file=sys.stderr)
            sys.exit(1)

    if ogg_path is not None:
        os.makedirs(args.audio_dir, exist_ok=True)
        shutil.copy2(ogg_path, os.path.join(args.audio_dir, audio_filename))

    if output_path:
        out = open(output_path, 'w')
        try:
            write_trl(global_fields, bpm_segments, offset, notes,
                      args.scroll_time, args.time_mode, out, audio=audio_filename)
        finally:
            out.close()
    else:
        write_trl(global_fields, bpm_segments, offset, notes,
                  args.scroll_time, args.time_mode, sys.stdout, audio=audio_filename)


def main():
    args = make_parser().parse_args()
    convert(args)


if __name__ == '__main__':
    main()
