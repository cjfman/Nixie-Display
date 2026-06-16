"""Tap Revolution settings: a defaults file plus a persistent file.

Kept out of ``tap_revolution.py`` so that module stays pure game logic. The active
settings are the code defaults, overlaid by the (shipped, version-controlled)
defaults file, overlaid by the user's persistent file. In-game changes (a future
pass) call ``save``; ``reset`` restores the defaults into the persistent file so a
"Reset Settings" menu action can undo experimentation.

Settings schema (see config/tap_revolution.defaults.yaml):
    keys:          {left, right, up, down}   # arrow names ('left') or single chars
    score_buckets: [{name, threshold, points}, ...]   # -> hit_windows, ascending
    bad_tap:       {cooldown, penalty}       # cooldown 0 disables the lockout
    grace, flash_secs, score_width, judge_flash, results_secs
    hit_flash:     {frames, frame_secs}
"""

import collections
import copy
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

from pyxielib import tap_revolution as tr
from pyxielib.config import load_config

logger = logging.getLogger(__name__)

ARROW_TOKENS = {'left': 'LEFT', 'right': 'RIGHT', 'up': 'UP', 'down': 'DOWN'}
LANE_LABEL = {'left': 'L', 'right': 'R', 'up': 'U', 'down': 'D'}

## Resolved difficulty levers. ``scroll_time`` is None to keep the chart's own.
Difficulty = collections.namedtuple('Difficulty', ['gap', 'scroll_time', 'window_scale'])

## The ultimate fallback when no files are present, derived from the code constants
## so the shipped defaults file and these never drift.
CODE_DEFAULTS: Dict[str, Any] = {
    'keys': {'left': 'left', 'right': 'right', 'up': 'up', 'down': 'down'},
    'score_buckets': [
        {'name': name, 'threshold': window, 'points': points}
        for name, window, points in tr.DEFAULT_HIT_WINDOWS
    ],
    'bad_tap': {'enabled': True, 'cooldown': tr.DEFAULT_COOLDOWN, 'penalty': tr.DEFAULT_BAD_PENALTY},
    'grace': tr.DEFAULT_GRACE,
    'flash_secs': 0.6,
    'score_width': 4,
    'judge_flash': True,
    'hit_flash': {'frames': list(tr.HIT_FLASH_FRAMES), 'frame_secs': tr.HIT_FLASH_FRAME_SECS},
    'results_secs': 6,
    'difficulties': [
        {'name': 'hard',   'display_name': 'Hard',   'gap': 0,   'scroll_time': None, 'window_scale': 1.0},
        {'name': 'medium', 'display_name': 'Medium', 'gap': 200, 'scroll_time': 2.5,  'window_scale': 1.4},
        {'name': 'easy',   'display_name': 'Easy',   'gap': 400, 'scroll_time': 3.0,  'window_scale': 1.8},
    ],
    'difficulty': 'hard',
    'audio_dir':         '',
    'audio_offset_ms':   0,
    'calibration_track': '',
    'leaderboard_path':  '~/.nixie/leaderboard.yaml',
}



def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively overlay ``override`` onto ``base``; lists/scalars replace."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


class TapRevolutionConfig:
    """Loads/merges/persists Tap Revolution settings and adapts them for the game."""
    def __init__(self, defaults_path=None, persistent_path=None):
        self.defaults_path = defaults_path
        self.persistent_path = persistent_path
        self._defaults: Dict[str, Any] = {}
        self.settings: Dict[str, Any] = {}
        self._load()

    @staticmethod
    def _read(path) -> Dict[str, Any]:
        """Read a YAML file into a dict; empty dict if it doesn't exist."""
        if not path or not os.path.exists(path):
            return {}

        return load_config(path)

    def _write(self, data: Dict[str, Any]):
        """Write settings to the persistent file (creating parent dirs)."""
        if not self.persistent_path:
            return
        os.makedirs(os.path.dirname(self.persistent_path) or '.', exist_ok=True)
        with open(self.persistent_path, 'w') as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        logger.debug("Wrote Tap Revolution settings to %s", self.persistent_path)

    def _load(self):
        """Build active settings; seed the persistent file from defaults if absent."""
        self._defaults = deep_merge(copy.deepcopy(CODE_DEFAULTS), self._read(self.defaults_path))
        persistent: Dict[str, Any] = {}
        if self.persistent_path and os.path.exists(self.persistent_path):
            persistent = self._read(self.persistent_path)
        elif self.persistent_path:
            self._write(self._defaults)

        self.settings = deep_merge(copy.deepcopy(self._defaults), persistent)
        logger.debug("Loaded Tap Revolution config (defaults=%s, persistent=%s)",
                     self.defaults_path, self.persistent_path)

    def save(self):
        """Persist the current settings (for in-game edits)."""
        self._write(self.settings)

    def reset(self):
        """Restore the defaults into the persistent file and the active settings."""
        self.settings = copy.deepcopy(self._defaults)
        self._write(self._defaults)

    @staticmethod
    def validate_buckets(buckets) -> bool:
        """True iff thresholds strictly increase and points strictly decrease."""
        ordered = sorted(buckets, key=lambda b: float(b['threshold']))
        for i in range(len(ordered) - 1):
            if float(ordered[i]['threshold']) >= float(ordered[i + 1]['threshold']):
                return False
            if int(ordered[i]['points']) <= int(ordered[i + 1]['points']):
                return False
        return True

    def _hit_windows(self) -> Tuple[Tuple[str, float, int], ...]:
        """Score buckets as ascending (name, threshold, points) for the animation."""
        buckets = sorted(self.settings['score_buckets'], key=lambda b: float(b['threshold']))
        return tuple((b['name'], float(b['threshold']), int(b['points'])) for b in buckets)

    def results_secs(self) -> int:
        return int(self.settings['results_secs'])

    def leaderboard_path(self) -> str:
        """Filesystem path of the high-score leaderboard YAML."""
        return os.path.expanduser(str(self.settings.get('leaderboard_path', '~/.nixie/leaderboard.yaml')))

    def resolve_audio(self, audio_field, trl_path) -> str:
        """Resolve an audio field from a .trl header to a filesystem path.

        Absolute paths (and ``~``) are returned as-is. A bare filename (no '/') is
        looked up in audio_dir, falling back to the .trl file's directory. A relative
        path *with* a '/' is ambiguous — it may be written relative to the .trl file
        or relative to the working directory (repo root) — so each candidate is tried
        and the first that exists wins; if none exist, the .trl-relative form is
        returned so the caller's not-found logging has a sensible path to report.
        """
        audio_field = os.path.expanduser(audio_field)
        if os.path.isabs(audio_field):
            return audio_field
        trl_dir = os.path.dirname(trl_path)
        if '/' not in audio_field:
            audio_dir = os.path.expanduser(str(self.settings.get('audio_dir', '') or ''))
            return os.path.join(audio_dir or trl_dir, audio_field)
        candidates = [os.path.join(trl_dir, audio_field), os.path.abspath(audio_field)]
        return next((c for c in candidates if os.path.exists(c)), candidates[0])

    def animation_kwargs(self, window_scale=1.0) -> Dict[str, Any]:
        """Keyword args for ``TapRevolutionAnimation`` from the active settings.

        ``window_scale`` widens every hit-window threshold (a difficulty lever).
        """
        s = self.settings
        windows = self._hit_windows()
        if window_scale != 1.0:
            windows = tuple((name, w * window_scale, points) for name, w, points in windows)
        return {
            'hit_windows':          windows,
            'grace':                float(s['grace']),
            'cooldown':             float(s['bad_tap']['cooldown']),
            'bad_penalty':          int(s['bad_tap']['penalty']),
            'bad_enabled':          bool(s['bad_tap'].get('enabled', True)),
            'flash_secs':           float(s['flash_secs']),
            'score_width':          int(s['score_width']),
            'judge_flash':          bool(s['judge_flash']),
            'hit_flash_frames':     tuple(s['hit_flash']['frames']),
            'hit_flash_frame_secs': float(s['hit_flash']['frame_secs']),
            'audio_offset_secs':    float(s.get('audio_offset_ms', 0)) / 1000.0,
        }

    def key_lane_map(self) -> Dict[str, str]:
        """Map each incoming key token ('LEFT'/'UP'/.../'a') to a lane (L/R/U/D)."""
        mapping = {}
        for lane_name, key in self.settings['keys'].items():
            lane = tr.LANE_NAMES.get(lane_name.lower())
            token = self._key_token(key)
            if lane and token:
                mapping[token] = lane

        return mapping

    @staticmethod
    def _key_token(key) -> str:
        """Normalize a configured key to how the navigator delivers it, or '' if invalid."""
        key = str(key).strip()
        if key.lower() in ARROW_TOKENS:
            return ARROW_TOKENS[key.lower()]
        if len(key) == 1:
            return key

        return ''

    def difficulty_settings(self) -> Difficulty:
        """Resolve the active difficulty into (gap_secs, scroll_time, window_scale)."""
        name = str(self.settings.get('difficulty', '')).lower()
        for diff in self.settings.get('difficulties', []):
            if str(diff.get('name', '')).lower() == name:
                return self._difficulty(diff)
        return Difficulty(0.0, None, 1.0)

    @staticmethod
    def _difficulty(diff) -> Difficulty:
        """One difficulties entry as a Difficulty (gap in seconds; scroll_time None = keep chart's)."""
        gap = max(0, int(diff.get('gap', 0))) / 1000.0
        scroll = diff.get('scroll_time')
        scroll_time: Optional[float] = float(scroll) if scroll is not None else None
        return Difficulty(gap, scroll_time, float(diff.get('window_scale', 1.0)))

    def summary_lines(self) -> List[str]:
        """Printable, scrollable lines describing the settings for the menu view."""
        s = self.settings
        lines = [f"KEY {LANE_LABEL[lane]} {str(s['keys'][lane]).upper()}"
                 for lane in ('left', 'right', 'up', 'down') if lane in s['keys']]
        for name, window, points in self._hit_windows():
            lines.append(f"{name} {round(window * 1000)}MS {points}PTS")
        if s['bad_tap'].get('enabled', True):
            lines.append(f"BAD -{int(s['bad_tap']['penalty'])}PTS")
            lines.append(f"COOLDOWN {round(float(s['bad_tap']['cooldown']) * 1000)}MS")
        else:
            lines.append("BAD OFF")
        lines.append(f"GRACE {round(float(s['grace']) * 1000)}MS")
        lines.append(f"AUDIO OFFSET {int(s.get('audio_offset_ms', 0))}MS")
        lines.append(f"AUDIO DIR {s.get('audio_dir') or '(none)'}")
        return lines
