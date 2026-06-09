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

import copy
import logging
import os
from typing import Any, Dict, List, Tuple

import yaml

from pyxielib import tap_revolution as tr
from pyxielib.config import load_config

logger = logging.getLogger(__name__)

ARROW_TOKENS = {'left': 'LEFT', 'right': 'RIGHT', 'up': 'UP', 'down': 'DOWN'}
LANE_LABEL = {'left': 'L', 'right': 'R', 'up': 'U', 'down': 'D'}

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

    def _load(self):
        """Build active settings; seed the persistent file from defaults if absent."""
        self._defaults = deep_merge(copy.deepcopy(CODE_DEFAULTS), self._read(self.defaults_path))
        persistent: Dict[str, Any] = {}
        if self.persistent_path and os.path.exists(self.persistent_path):
            persistent = self._read(self.persistent_path)
        elif self.persistent_path:
            self._write(self._defaults)

        self.settings = deep_merge(copy.deepcopy(self._defaults), persistent)

    def save(self):
        """Persist the current settings (for in-game edits)."""
        self._write(self.settings)

    def reset(self):
        """Restore the defaults into the persistent file and the active settings."""
        self.settings = copy.deepcopy(self._defaults)
        self._write(self._defaults)

    def _hit_windows(self) -> Tuple[Tuple[str, float, int], ...]:
        """Score buckets as ascending (name, threshold, points) for the animation."""
        buckets = sorted(self.settings['score_buckets'], key=lambda b: float(b['threshold']))
        return tuple((b['name'], float(b['threshold']), int(b['points'])) for b in buckets)

    def results_secs(self) -> int:
        return int(self.settings['results_secs'])

    def animation_kwargs(self) -> Dict[str, Any]:
        """Keyword args for ``TapRevolutionAnimation`` from the active settings."""
        s = self.settings
        return {
            'hit_windows':          self._hit_windows(),
            'grace':                float(s['grace']),
            'cooldown':             float(s['bad_tap']['cooldown']),
            'bad_penalty':          int(s['bad_tap']['penalty']),
            'bad_enabled':          bool(s['bad_tap'].get('enabled', True)),
            'flash_secs':           float(s['flash_secs']),
            'score_width':          int(s['score_width']),
            'judge_flash':          bool(s['judge_flash']),
            'hit_flash_frames':     tuple(s['hit_flash']['frames']),
            'hit_flash_frame_secs': float(s['hit_flash']['frame_secs']),
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
        return lines
