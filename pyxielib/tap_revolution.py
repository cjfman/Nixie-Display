"""Tap Revolution — a Dance Dance Revolution facsimile for the nixie display.

Arrows scroll across the tubes toward a hit zone; the player presses the matching
arrow key as each arrow arrives. This module is pure game logic with no menu or
I/O dependencies so it can be unit tested in isolation. The menu integration lives
in ``menu_library.TapRevolutionMenu`` (settings come from
``tap_revolution_config.TapRevolutionConfig``).

A note's source of truth is its *absolute target time in seconds*. Beats are just
one convenient way to express that time (``offset + beat * 60 / bpm``); the engine
and scoring only ever use the absolute time. This keeps the door open for audio
sync later: the chart is already on the same wall-clock timeline audio would use.
"""

import logging
import os
import threading
import time

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from pyxielib.animation import Animation
from pyxielib.pyxieutil import PyxieError
from pyxielib.tube_manager import cmdDecodePrint

logger = logging.getLogger(__name__)


class TapRevolutionError(PyxieError):
    pass


## Each lane maps to a wire-format glyph. '<','>','^' are native decoder.py
## characters; down is a custom bitmap ('\ /' chevron on the top line) that mirrors
## up's '^' ('/ \' on the bottom line). Each value is one tube in the code string
## (a {0xHHHH} token decodes to a single bitmap), so the rendering logic is glyph
## agnostic and a trailing '!' still underlines the token in the hit zone.
LANE_GLYPH = {'L': '<', 'R': '>', 'U': '^', 'D': '{0x0140}'}

## The raw segment bitmap behind each glyph, so simultaneous notes (a chord) that
## land on the same tube can be OR'd into one combined symbol instead of one note
## hiding the other. Derived from LANE_GLYPH so the two never drift apart.
LANE_BITMAP = {lane: cmdDecodePrint(glyph)[0] for lane, glyph in LANE_GLYPH.items()}

## Accepted spellings for a lane in files and programmatic charts.
LANE_NAMES = {
    'left':  'L', 'l': 'L',
    'right': 'R', 'r': 'R',
    'up':    'U', 'u': 'U',
    'down':  'D', 'd': 'D',
}

## Judgement brackets as (word, max_abs_error_seconds, score), ascending by window.
## A tap within the last (widest) window always lands; anything wider is a miss.
DEFAULT_HIT_WINDOWS: Tuple[Tuple[str, float, int], ...] = (
    ('BEST', 0.045, 100),
    ('GOOD', 0.090, 70),
    ('OK',   0.140, 40),
)
MISS_WORD = 'MISS'
## A tap with no note in the hit window: counts as 'BAD' and docks this many points.
BAD_WORD = 'BAD'
DEFAULT_BAD_PENALTY = 5
## Extra time past the widest window before a note is auto-missed and purged, so a
## tap captured in time but processed a poll-cycle late still claims its note.
DEFAULT_GRACE = 0.12

## After an arrow key registers, that lane ignores further presses for this long,
## so a player can't spam/mash a key to clear a cluster of same-lane notes.
DEFAULT_COOLDOWN = 0.15

## Hit-zone tap feedback. The target tube normally shows an underline marker; a
## tap drops it, and a successful hit plays this quick glyph burst in its place.
HIT_FLASH_FRAMES = ('x', '+', 'x')
HIT_FLASH_FRAME_SECS = 0.05


def parse_lane(name) -> str:
    """Normalize a lane spelling ('left', 'L', 'l', ...) to one of L/R/U/D."""
    lane = LANE_NAMES.get(name.strip().lower())
    if lane is None:
        raise TapRevolutionError(f"Unknown arrow '{name}'")

    return lane


@dataclass
class Note:
    """A single arrow to hit. ``time`` is absolute seconds from chart start."""
    time: float
    lane: str


@dataclass
class Level:
    """A note chart. ``bpm`` is only needed to author notes by beat."""
    name: str
    notes: List[Note] = field(default_factory=list)
    bpm: Optional[float] = None
    offset: float = 0.0
    scroll_time: float = 2.0
    audio: Optional[str] = None  ## reserved for future audio sync

    def __post_init__(self):
        self.notes.sort(key=lambda n: n.time)

    @classmethod
    def from_beats(cls, name, bpm, beats: Sequence[Tuple[float, str]], **kwargs) -> 'Level':
        """Build a level from (beat, lane) pairs at a known tempo."""
        offset = kwargs.get('offset', 0.0)
        notes = [Note(offset + beat * 60.0 / bpm, parse_lane(lane)) for beat, lane in beats]
        return cls(name, notes, bpm=bpm, **kwargs)

    @classmethod
    def from_times(cls, name, times: Sequence[Tuple[float, str]], **kwargs) -> 'Level':
        """Build a level from (seconds, lane) pairs — for charts made off a recording."""
        notes = [Note(float(t), parse_lane(lane)) for t, lane in times]
        return cls(name, notes, **kwargs)

    @classmethod
    def from_file(cls, path) -> 'Level':
        """Parse a .trl file (see module docs / levels/demo.trl)."""
        with open(path) as f:
            return cls.from_string(f.read(), name=os.path.splitext(os.path.basename(path))[0])

    @staticmethod
    def read_title(path) -> str:
        """Read just the 'name:' header, falling back to the filename stem.

        Cheap enough to call while listing a directory of levels for a menu.
        """
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path) as f:
                for raw in f:
                    line = raw.split('#', 1)[0].strip()
                    if line.lower().startswith('name:'):
                        return line.split(':', 1)[1].strip() or stem
        except OSError:
            pass

        return stem

    @classmethod
    def from_string(cls, text, *, name='Level') -> 'Level':
        """Parse .trl content. Header 'key: value' lines, then note lines."""
        header, note_lines = _split_chart(text)
        bpm = float(header['bpm']) if 'bpm' in header else None
        offset = float(header.get('offset', 0.0))
        scroll_time = float(header.get('scroll_time', 2.0))
        mode = header.get('mode') or ('beat' if bpm is not None else 'time')
        if mode == 'beat' and bpm is None:
            raise TapRevolutionError("Beat-mode level needs a 'bpm' header")

        notes = _parse_notes(note_lines, mode, bpm, offset)
        return cls(header.get('name', name), notes, bpm=bpm, offset=offset,
                   scroll_time=scroll_time, audio=header.get('audio') or None)


def _split_chart(text) -> Tuple[Dict[str, str], List[Tuple[float, str]]]:
    """Split .trl text into a header dict and a list of (value, arrows) note lines."""
    header: Dict[str, str] = {}
    note_lines: List[Tuple[float, str]] = []
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if ':' in line and line.split(':', 1)[0].replace('_', '').isalpha():
            key, value = line.split(':', 1)
            header[key.strip().lower()] = value.strip()
        else:
            value, _, arrows = line.partition(' ')
            note_lines.append((float(value), arrows.strip()))

    return header, note_lines


def _parse_notes(note_lines: Sequence[Tuple[float, str]], mode, bpm, offset) -> List[Note]:
    """Turn parsed note lines into absolute-time Notes; arrows may be comma-listed."""
    notes = []
    for value, arrows in note_lines:
        when = offset + value * 60.0 / bpm if mode == 'beat' else value
        for arrow in arrows.split(','):
            notes.append(Note(when, parse_lane(arrow)))

    return notes


class _NoteState:
    """Mutable per-note play state: absolute target time, lane, and judgement."""
    def __init__(self, target, lane):
        self.target = target
        self.lane = lane
        self.judged: Optional[str] = None


class TapRevolutionAnimation(Animation):
    """Self-contained, wall-clock driven playfield + scoring for one Level.

    Handed to the Assembler, whose 1ms poll calls ``updateFrameSet`` for smooth
    scrolling. ``tap`` is called from the menu/scheduler thread; a lock guards the
    shared note/score state. ``__eq__`` is identity so ``Program.update`` never
    resets a game in progress.
    """
    def __init__(self, level: Level, *, size=16, score_width=4, judge_flash=True,
                 flash_secs=0.6, hit_windows=None, grace=DEFAULT_GRACE,
                 cooldown=DEFAULT_COOLDOWN, bad_penalty=DEFAULT_BAD_PENALTY, bad_enabled=True,
                 hit_flash_frames=HIT_FLASH_FRAMES, hit_flash_frame_secs=HIT_FLASH_FRAME_SECS,
                 lead_in=None):
        super().__init__()
        self.level = level
        self.size = size
        self.score_width = score_width
        self.track_width = size - score_width
        self.span = max(1, self.track_width - 1)  ## tube travel distance, in tubes
        self.scroll_time = level.scroll_time
        ## Seconds an arrow spends in each tube. Quantizing scroll position by this
        ## (rather than rounding each note's position independently) makes the whole
        ## field step left in lockstep instead of arrows nudging one at a time.
        self.sec_per_tube = self.scroll_time / self.span
        self.judge_flash = judge_flash
        self.flash_secs = flash_secs
        self.hit_windows = hit_windows or DEFAULT_HIT_WINDOWS
        self.ok_window = max(w for _, w, _ in self.hit_windows)
        self.grace = grace
        self.cooldown = cooldown
        self.bad_penalty = bad_penalty
        self.bad_enabled = bad_enabled
        self.hit_flash_frames = tuple(hit_flash_frames)
        self.hit_flash_frame_secs = hit_flash_frame_secs
        ## Shift every note so the earliest one scrolls in cleanly from the edge.
        self.lead_in = lead_in if lead_in is not None else self.scroll_time
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        """(Re)anchor the timeline and clear all play state — replayable."""
        with self.lock:
            self.start_time = time.time()
            self.note_states = [_NoteState(n.time + self.lead_in, n.lane) for n in self.level.notes]
            self.score = 0
            self.combo = 0
            self.bad_taps = 0   ## ghost taps (key pressed with nothing in the hit box)
            self.flash_word: Optional[str] = None
            self.flash_until = 0.0
            self.hit_flash_at = 0.0      ## wall-clock time of the last tap's feedback
            self.hit_flash_hit = False   ## whether that tap landed a note (-> x+x burst)
            self.bad_cooldown_until: Dict[str, float] = {}  ## per-lane lockout after a BAD tap
            self._last_code: Optional[str] = None

    def tubeCount(self) -> int:
        return self.size

    def length(self) -> float:
        return self._last_target() + self.scroll_time

    def _last_target(self) -> float:
        return max((ns.target for ns in self.note_states), default=self.lead_in)

    def tap(self, lane, when=None):
        """Score a tap on ``lane`` against the nearest unjudged note.

        ``when`` is the key's capture timestamp (epoch seconds), so accuracy is
        independent of how late the tap is processed. Defaults to now.
        """
        if when is None:
            when = time.time()
        with self.lock:
            elapsed = when - self.start_time
            note, error = self._nearest_unjudged(lane, elapsed)
            if note is not None:
                ## A real hit always scores — never gated by the bad-tap cooldown.
                self._register_hit(note, error)
            elif self.bad_enabled and not (self.cooldown and when < self.bad_cooldown_until.get(lane, 0.0)):
                ## Nothing in the hit box, and the lane isn't on its bad-tap cooldown.
                ## When bad taps are disabled a ghost tap is a complete no-op.
                self._register_bad(lane, when)

    def _register_hit(self, note, error):
        """Score a landed note and fire the x+x hit-zone burst."""
        self.hit_flash_at = time.time()
        self.hit_flash_hit = True
        word, points = self._judge(error)
        note.judged = word
        self.score += points
        self.combo += 1
        self._flash(word)

    def _register_bad(self, lane, when):
        """A tap with nothing in the hit box: dock points, break combo, start the
        lane's bad-tap cooldown so a flurry of mashes can't rack up the penalty.
        A ``cooldown`` of 0 disables the lockout (every ghost tap is BAD)."""
        self.hit_flash_at = time.time()
        self.hit_flash_hit = False
        self.bad_taps += 1
        self.score = max(0, self.score - self.bad_penalty)
        self.combo = 0
        self._flash(BAD_WORD)
        if self.cooldown:
            self.bad_cooldown_until[lane] = when + self.cooldown

    def _nearest_unjudged(self, lane, elapsed) -> Tuple[Optional[_NoteState], float]:
        """The closest unjudged note on ``lane`` within the OK window, and its error."""
        best, best_error = None, self.ok_window
        for ns in self.note_states:
            if ns.judged is not None or ns.lane != lane:
                continue
            error = abs(ns.target - elapsed)
            if error <= best_error:
                best, best_error = ns, error

        return best, best_error

    def _judge(self, error) -> Tuple[str, int]:
        """Map a timing error (within ok_window) to a (word, score)."""
        for word, window, points in self.hit_windows:
            if error <= window:
                return word, points

        return self.hit_windows[-1][0], self.hit_windows[-1][2]

    def _flash(self, word):
        self.flash_word = word
        self.flash_until = time.time() + self.flash_secs

    def updateFrameSet(self) -> Optional[bool]:
        """Auto-miss stale notes, re-render, and report whether the frame changed."""
        now = time.time()
        with self.lock:
            self._auto_miss(now - self.start_time)
            code = self._render(now)
            if code == self._last_code:
                return False
            self._last_code = code

        return True

    def _auto_miss(self, elapsed):
        """Mark notes whose hit window (plus grace) has fully passed as missed."""
        deadline = elapsed - self.ok_window - self.grace
        for ns in self.note_states:
            if ns.judged is None and ns.target < deadline:
                ns.judged = MISS_WORD
                self.combo = 0
                if self.judge_flash:
                    self._flash(MISS_WORD)

    def getCode(self) -> Optional[str]:
        return self._last_code

    def _render(self, now) -> str:
        """Build the wire-format code: hit-zone tube + track + score."""
        cells = self._render_track(now - self.start_time)
        head = self._render_hit_zone(cells[0], now)
        return head + ''.join(cells[1:]) + self._render_score(now)

    def _render_hit_zone(self, cell, now) -> str:
        """The target tube: underline marker at rest, dropped on a tap, x+x on a hit."""
        elapsed = now - self.hit_flash_at
        if elapsed >= len(self.hit_flash_frames) * self.hit_flash_frame_secs:
            return cell + '!'  ## resting: underline marks the target
        if not self.hit_flash_hit:
            return cell        ## tap acknowledged: underline off briefly

        return self.hit_flash_frames[int(elapsed / self.hit_flash_frame_secs)]

    def _render_track(self, elapsed) -> List[str]:
        """Place unjudged notes on the shared lockstep grid, merging chords per tube."""
        lanes_at: List[List[str]] = [[] for _ in range(self.track_width)]
        steps = round(elapsed / self.sec_per_tube)  ## one global scroll offset
        for ns in self.note_states:
            if ns.judged is not None:
                continue
            tube = round(ns.target / self.sec_per_tube) - steps
            if 0 <= tube < self.track_width:
                lanes_at[tube].append(ns.lane)

        return [self._tube_glyph(lanes) for lanes in lanes_at]

    @staticmethod
    def _tube_glyph(lanes: Sequence[str]) -> str:
        """One tube's glyph: blank, a single arrow, or chord notes OR'd together."""
        if not lanes:
            return ' '
        if len(lanes) == 1:
            return LANE_GLYPH[lanes[0]]

        bitmap = 0
        for lane in lanes:
            bitmap |= LANE_BITMAP[lane]

        return '{' + hex(bitmap) + '}'

    def _render_score(self, now) -> str:
        """The score section: the judgement word while flashing, else the score."""
        if self.judge_flash and self.flash_word and now < self.flash_until:
            text = self.flash_word
        else:
            text = str(self.score)

        return text[-self.score_width:].rjust(self.score_width)

    def done(self) -> bool:
        elapsed = time.time() - self.start_time
        if not self.note_states:
            return elapsed > self.lead_in
        return elapsed > self._last_target() + self.scroll_time + 0.5

    def results(self) -> Dict[str, int]:
        """Tally of judgement words plus the final score and best combo."""
        counts = {word: 0 for word, _, _ in self.hit_windows}
        counts[MISS_WORD] = 0
        for ns in self.note_states:
            if ns.judged is not None:
                counts[ns.judged] = counts.get(ns.judged, 0) + 1

        if self.bad_enabled:
            counts[BAD_WORD] = self.bad_taps
        counts['SCORE'] = self.score
        return counts

    def results_text(self) -> str:
        """A human-readable results summary for a marquee.

        The judgement breakdown comes first and the final SCORE comes last, so a
        left-scrolling marquee ends on the headline number rather than 'MISS'.
        """
        counts = self.results()
        parts = [f"{word} {counts.get(word, 0)}" for word, _, _ in self.hit_windows]
        parts.append(f"{MISS_WORD} {counts.get(MISS_WORD, 0)}")
        if self.bad_enabled:
            parts.append(f"{BAD_WORD} {counts.get(BAD_WORD, 0)}")
        parts.append(f"SCORE {counts['SCORE']}")
        return '  '.join(parts)

    def __eq__(self, other) -> bool:
        return self is other

    __hash__ = object.__hash__


def _demo_level() -> Level:
    """A short built-in chart, authored programmatically by beat."""
    beats = [
        (1, 'left'), (2, 'down'), (3, 'up'), (4, 'right'),
        (5, 'left'), (5.5, 'up'), (6, 'right'), (6.5, 'down'),
        (7, 'left'), (7, 'right'), (8, 'up'), (8, 'down'),
    ]
    ## Angle brackets, not parens: the nixie can render '<'/'>' but '('/')' are
    ## NOCODE (see decoder.py), matching the menu's existing naming convention.
    return Level.from_beats("Demo <builtin>", 120, beats, scroll_time=2.0)


## Programmatic levels, always available in the menu alongside any .trl files.
BUILTIN_LEVELS: Dict[str, Level] = {
    "Demo <builtin>": _demo_level(),
}
