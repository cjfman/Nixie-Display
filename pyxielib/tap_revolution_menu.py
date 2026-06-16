"""Menu classes for the Tap Revolution rhythm game.

Kept separate from menu_library.py so the game's interactive UI lives near
its config and game-logic siblings rather than in the general menu module.

Public classes: TapRevolutionMenu (the top-level sub-menu entry in the Nixie
Menu), plus the three children it creates: TapRevolutionLevelsItem (Play),
TapRevolutionSettingsItem (editable settings), and ResetSettingsItem.
"""

import array
import collections
import copy
import dataclasses
import io
import logging
import math
import os
import tempfile
import time
import wave
from typing import List, Optional

from pyxielib import tap_revolution as taplib
from pyxielib.animation import Animation, MarqueeAnimation
from pyxielib.navigator import CycleItem, ListItem, Menu, MenuItem
from pyxielib.tap_revolution_config import TapRevolutionConfig
from pyxielib.tap_revolution_leaderboard import Leaderboard
from pyxielib.tube_manager import cmdLen

logger = logging.getLogger(__name__)

## ─────────────────────────────────────────────────────────────────────────── ##
## Calibration helpers                                                          ##
## ─────────────────────────────────────────────────────────────────────────── ##

_CALIB_BPM        = 30   ## 2s per beat; handles BT delays up to ~2s without beat-matching ambiguity
_CALIB_BEATS      = 6    ## measurement beats (taps are recorded for these)
_CALIB_LEAD_IN    = 3    ## extra beats at the start before measurement begins
_CALIB_CLICK_FREQ = 880
_CALIB_CLICK_DUR  = 0.05 ## seconds per click burst


def _make_click_wav() -> bytes:
    """WAV bytes: (_CALIB_LEAD_IN + _CALIB_BEATS) clicks at _CALIB_BPM."""
    sample_rate = 44100
    beat_secs   = 60.0 / _CALIB_BPM
    n_beats     = _CALIB_LEAD_IN + _CALIB_BEATS
    n_total     = int(sample_rate * beat_secs * n_beats)
    n_click     = int(sample_rate * _CALIB_CLICK_DUR)
    samples     = array.array('h', bytes(2 * n_total))
    for i in range(n_beats):
        start = int(i * beat_secs * sample_rate)
        for j in range(n_click):
            if start + j < n_total:
                samples[start + j] = int(32767 * math.sin(
                    2 * math.pi * _CALIB_CLICK_FREQ * j / sample_rate
                ))
    buf = io.BytesIO()
    with wave.open(buf, 'w') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


## ─────────────────────────────────────────────────────────────────────────── ##
## Settings list helpers                                                        ##
## ─────────────────────────────────────────────────────────────────────────── ##

## Named tuple for one row in the settings list.
## prefix is the short title shown before ' | ' in edit mode.
## set is None for readonly and submenu entries.
_SettingEntry = collections.namedtuple(
    '_SettingEntry', ['tag', 'type', 'label', 'prefix', 'get', 'set', 'options'],
    defaults=[None],
)

## Seconds the cursor is ON within each 0.5 s blink period.
_CURSOR_ON_SECS = 0.25
## Seconds the directory '>' marker is ON within each 0.5 s blink period.
_DIR_BLINK_ON_SECS = 0.3
## Duration to show validation-failure flash messages.
_SETTINGS_FLASH_SECS = 1.0
## High-score 'high score' banner: half a second per on/off phase, three flashes.
_HS_FLASH_HALF  = 0.5
_HS_FLASH_TOTAL = _HS_FLASH_HALF * 6
## Longest player name accepted in the high-score entry field.
_HS_NAME_MAX = 8
## Seconds to count down before a leaderboard reset commits.
_RESET_COUNTDOWN = 3

_ARROW_NAMES = {'left', 'right', 'up', 'down'}


def _set_difficulty(config, value):
    config.settings['difficulty'] = value
    config.save()


def _s_entry(tag, kind, label_fn, prefix, get_fn, set_fn=None, options=None):
    return _SettingEntry(tag, kind, label_fn, prefix, get_fn, set_fn, options)


def _difficulty_display(draft) -> str:
    """Return the display_name for the active difficulty, falling back to the name."""
    current = str(draft.get('difficulty', ''))
    for diff in draft.get('difficulties', []):
        if str(diff.get('name', '')) == current:
            return str(diff.get('display_name', current))
    return current


def _underline_str(s):
    """Underline each character in s using the wire-format '!' modifier."""
    return ''.join(c + '!' for c in s)


def _key_display(val) -> str:
    """Format a bound key for display: arrow names get ' key' appended."""
    v = str(val).lower()
    return (v + ' key') if v in _ARROW_NAMES else v.upper()


def _build_score_items(draft):
    """Sub-list for the Score Ranges sub-menu: one entry per scoring bucket."""
    return [
        _s_entry(f'bucket_{i}', 'bucket', lambda d, n=b['name']: n, None,
                 lambda d, j=i: sorted(d['score_buckets'], key=lambda x: float(x['threshold']))[j])
        for i, b in enumerate(sorted(draft['score_buckets'], key=lambda b: float(b['threshold'])))
    ]


def _build_items(draft):
    """Main browse list. Key bindings, score ranges, and bad-tap settings live in sub-menus."""
    return [
        _s_entry('difficulty', 'select',
                 lambda d: "DIFFICULTY", None,
                 lambda d: d.get('difficulty', ''),
                 lambda d, v: d.__setitem__('difficulty', v),
                 options=lambda d: [(str(x['name']), str(x.get('display_name', x['name'])))
                                    for x in d.get('difficulties', [])]),
        _s_entry('key_mappings',  'submenu', lambda d: "KEY MAPPINGS",  None, None),
        _s_entry('score_ranges',  'submenu', lambda d: "SCORE RANGES",  None, None),
        _s_entry('bad_submenu',   'submenu', lambda d: "BAD/GHOST HIT", None, None),
        _s_entry('grace', 'ms',
                 lambda d: f"GRACE {round(float(d['grace']) * 1000)}MS", "GRACE",
                 lambda d: d['grace'], lambda d, v: d.__setitem__('grace', v)),
        _s_entry('flash_secs', 'ms',
                 lambda d: f"FLASH {round(float(d['flash_secs']) * 1000)}MS", "FLASH",
                 lambda d: d['flash_secs'], lambda d, v: d.__setitem__('flash_secs', v)),
        _s_entry('judge_flash', 'bool',
                 lambda d: "JUDGE " + ("ON" if d['judge_flash'] else "OFF"), "JUDGE",
                 lambda d: d['judge_flash'], lambda d, v: d.__setitem__('judge_flash', v)),
        _s_entry('results_secs', 'int',
                 lambda d: f"RESULTS {int(d['results_secs'])}S", "RESULTS",
                 lambda d: d['results_secs'], lambda d, v: d.__setitem__('results_secs', v)),
        _s_entry('score_width', 'readonly',
                 lambda d: f"SCORE WIDTH {int(d['score_width'])}", None, lambda d: d['score_width']),
        _s_entry('hit_flash', 'readonly',
                 lambda d: f"HIT FLASH {d['hit_flash']['frame_secs']*1000:.0f}MS", None,
                 lambda d: d['hit_flash']),
        _s_entry('audio_offset_ms', 'signed_int',
                 lambda d: f"OFFSET {int(d.get('audio_offset_ms', 0))}MS", "OFFSET",
                 lambda d: d.get('audio_offset_ms', 0),
                 lambda d, v: d.__setitem__('audio_offset_ms', v)),
        _s_entry('audio_dir', 'readonly',
                 lambda d: f"AUDIO DIR {d.get('audio_dir') or '(none)'}", None,
                 lambda d: d.get('audio_dir', '')),
    ]


def _build_key_items():
    """Sub-list for the Key Mappings sub-menu (labels use title-case direction names)."""
    return [
        _s_entry(f'key_{lane}', 'key_binding',
                 lambda d, ln=lane, t=title: f"{t} | {_key_display(d['keys'][ln])}", title,
                 lambda d, ln=lane: d['keys'][ln],
                 lambda d, v, ln=lane: d['keys'].__setitem__(ln, v))
        for lane, title in (('left', 'Left'), ('right', 'Right'), ('up', 'Up'), ('down', 'Down'))
    ]


def _build_bad_items(draft):
    """Sub-list for the Bad/Ghost Hit sub-menu (conditionally shows penalty/cooldown)."""
    items = [
        _s_entry('bad_enabled', 'bool',
                 lambda d: "BAD " + ("ON" if d['bad_tap'].get('enabled', True) else "OFF"), "BAD",
                 lambda d: d['bad_tap'].get('enabled', True),
                 lambda d, v: d['bad_tap'].__setitem__('enabled', v)),
    ]
    if draft['bad_tap'].get('enabled', True):
        items += [
            _s_entry('bad_penalty', 'int',
                     lambda d: f"PENALTY -{int(d['bad_tap']['penalty'])}PTS", "PENALTY",
                     lambda d: d['bad_tap']['penalty'],
                     lambda d, v: d['bad_tap'].__setitem__('penalty', v)),
            _s_entry('bad_cooldown', 'ms',
                     lambda d: f"COOLDOWN {round(float(d['bad_tap']['cooldown']) * 1000)}MS", "COOLDOWN",
                     lambda d: d['bad_tap']['cooldown'],
                     lambda d, v: d['bad_tap'].__setitem__('cooldown', v)),
        ]
    return items


## ─────────────────────────────────────────────────────────────────────────── ##
## Menu classes                                                                 ##
## ─────────────────────────────────────────────────────────────────────────── ##

class TapRevolutionMenu(Menu):
    """The Tap Revolution game menu: Play (levels), Settings (view), Reset Settings.

    All three children share one ``TapRevolutionConfig`` so a reset (or a future
    in-game edit) is reflected the next time a level is launched.
    """
    def __init__(self, config, levels_path, *, watcher=None, size=16, **kwargs):
        self.config = config
        leaderboard = Leaderboard(config.leaderboard_path())
        options_fn = lambda: [
            (d['name'], d.get('display_name', d['name']))
            for d in config.settings.get('difficulties', [])
        ]
        difficulty_item = CycleItem(
            "Difficulty",
            options_fn,
            get_fn=lambda: config.settings.get('difficulty', ''),
            set_fn=lambda v: _set_difficulty(config, v),
            size=size,
        )
        super().__init__("Tap Revolution", [
            TapRevolutionLevelsItem(config, levels_path, watcher=watcher, size=size,
                                    leaderboard=leaderboard),
            difficulty_item,
            LeaderBoardMenu(leaderboard, size=size),
            TapRevolutionSettingsItem(config),
            TapRevolutionCalibrationItem(config, watcher=watcher),
            ResetSettingsItem(config),
            ResetScoresItem(leaderboard),
        ], **kwargs)

    def activate(self):
        super().activate()
        logger.info("Tap Revolution menu opened (difficulty=%s)",
                    self.config.settings.get('difficulty'))


class TapRevolutionLevelsItem(ListItem):
    """Dance-Dance-Revolution-style rhythm game.

    Lists levels (``.trl`` files in ``levels_path`` plus the built-in
    programmatic charts); selecting one launches a ``TapRevolutionAnimation`` built
    from the shared config. Subdirectories of ``levels_path`` appear as their own
    entries (a blinking ``>`` pinned to the last tube) and act like nested menus:
    Enter descends into one, Left/Backspace/Esc ascends to the parent, and the
    same keys exit the item only at the root. While playing, the configured action
    keys are taps routed into the animation — timestamped by the key watcher so hit
    timing is accurate regardless of poll latency — and list navigation is
    suspended. When the chart finishes (or the player backs out early) a results
    marquee plays before returning to the level list.
    """
    def __init__(self, config, levels_path, *, watcher=None, size=16, leaderboard=None, **kwargs):
        super().__init__("Play", **kwargs)
        self.config      = config
        self.levels_path = levels_path
        self.watcher     = watcher
        self.size        = size
        self.leaderboard = leaderboard
        self.cur_path    = levels_path
        self.dir_stack   = []
        self.subdirs     = {}
        self.level_files = {}
        self.animation     = None
        self.results       = None
        self.key_lane      = {}
        self._quit_confirm = False
        self._reset_hiscore()

    def _reset_hiscore(self):
        self._cur_level_name = ''
        self._cur_level_file = ''
        self._hs_active      = False
        self._hs_done        = False
        self._hs_flash_start = 0.0
        self._hs_name        = ''

    def activate(self):
        self.cur_path  = self.levels_path
        self.dir_stack = []
        self.idx       = 0
        self._refresh()

    def _refresh(self):
        """Rescan the current directory and rebuild the visible list."""
        self.subdirs, self.level_files = self._scan(self.cur_path)
        names = sorted(self.subdirs)
        if self.cur_path == self.levels_path:
            names += sorted(taplib.BUILTIN_LEVELS)
        names += sorted(self.level_files)
        self.set_values(names)

    def _scan(self, path):
        """One directory as (subdirs, files) dicts mapping display name -> path.

        Subdirectories are keyed by basename; ``.trl`` files by their 'name:' title.
        Built-in programmatic charts are added by ``_refresh`` (root only).
        """
        try:
            entries = sorted(os.listdir(path))
        except OSError as e:
            logger.debug("Could not scan levels dir '%s': %s", path, e)
            return {}, {}

        counts, subdirs, files = {}, {}, {}
        for entry in entries:
            full = os.path.join(path, entry)
            if entry.startswith('.'):
                continue
            if os.path.isdir(full):
                subdirs[self._unique_name(entry, counts)] = full
            elif entry.endswith('.trl'):
                files[self._unique_name(taplib.Level.read_title(full), counts)] = full

        return subdirs, files

    @staticmethod
    def _unique_name(title, counts) -> str:
        """Disambiguate a repeated name by appending '<N>' (first keeps the bare name)."""
        seen = counts.get(title, 0)
        counts[title] = seen + 1
        return title if seen == 0 else f"{title}<{seen + 1}>"

    def reset(self):
        super().reset()
        if self.animation is not None:
            self.animation.stop_audio()
        self.set_values(None)
        self.cur_path    = self.levels_path
        self.dir_stack   = []
        self.subdirs     = {}
        self.level_files = {}
        self.animation     = None
        self.results       = None
        self.key_lane      = {}
        self._quit_confirm = False
        self._reset_hiscore()

    def _playing(self) -> bool:
        return self.animation is not None and self.results is None

    def _browsing(self) -> bool:
        return self.animation is None and self.results is None

    def for_display(self):
        if self.results is not None:
            if self.results.done():
                self.animation = None
                self.results = None
                self._reset_hiscore()
                return self._browse_display()
            return self.results
        if self.animation is not None:
            if self.animation.done():
                self._quit_confirm = False
                return self._complete()
            ## The quit prompt no longer pauses play: it's overlaid on the *same*
            ## running animation (taps swallowed as misses, see _play_key), so the
            ## timeline never resets and the song stays in place.
            self.animation.set_prompt("QUIT Y/N" if self._quit_confirm else None)
            return self.animation

        return self._browse_display()

    def _complete(self):
        """On game end: run high-score entry if the score qualifies, else results."""
        if not self._hs_active and not self._hs_done:
            score = self.animation.results().get('SCORE', 0)
            if self.leaderboard is not None and self.leaderboard.qualifies(self._cur_level_file, score):
                self._hs_active      = True
                self._hs_flash_start = time.time()
                self._hs_name        = ''
            else:
                self._hs_done = True
        if self._hs_active:
            return self._hs_display()
        return self._finish()

    def _hs_in_flash(self) -> bool:
        return time.time() - self._hs_flash_start < _HS_FLASH_TOTAL

    def _hs_display(self) -> str:
        """Flash 'high score' three times, then show the blinking name-entry field."""
        elapsed = time.time() - self._hs_flash_start
        if elapsed < _HS_FLASH_TOTAL:
            return "high score" if int(elapsed / _HS_FLASH_HALF) % 2 == 0 else ""
        field = f"Name|{self._hs_name}"
        if time.time() % 0.5 < _CURSOR_ON_SECS:
            return field + ' !'
        return field

    def _hs_commit(self, name):
        """Record the score under ``name`` and fall through to the results scroll."""
        results = self.animation.results()
        score   = results.get('SCORE', 0)
        self.leaderboard.add(self._cur_level_file, self._cur_level_name, name, score, results)
        self._hs_active = False
        self._hs_done   = True

    def _browse_display(self) -> str:
        """The current list entry; directories get a blinking '>' on the next tube."""
        name = self.current_value()
        if name in self.subdirs:
            return self._dir_label(name)
        return name

    def _dir_label(self, name) -> str:
        """A directory entry: the name with a blinking '>' pinned to the last tube."""
        arrow = '>' if time.time() % 0.5 < _DIR_BLINK_ON_SECS else ' '
        if cmdLen(name) >= self.size - 1:
            return f"{name[:self.size-1]} {arrow}"
        return f"{name}{arrow}"

    def _descend(self, path):
        """Enter a subdirectory, remembering the parent for ascent."""
        self.dir_stack.append(self.cur_path)
        self.cur_path = path
        self.idx = 0
        self._refresh()

    def _go_back(self):
        """Ascend to the parent directory, or exit the item when already at the root."""
        if self.dir_stack:
            self.cur_path = self.dir_stack.pop()
            self.idx = 0
            self._refresh()
        else:
            self.set_done()

    def _finish(self) -> Animation:
        """Stop audio, log the completed game with its score, and build the results marquee."""
        res = self.animation.results()
        breakdown = ', '.join(f"{k}={v}" for k, v in res.items() if k != 'SCORE')
        logger.info("Level '%s' complete: score=%d (%s)",
                    self.animation.level.name, res.get('SCORE', 0), breakdown)
        self.animation.stop_audio()
        self.results = self._make_results()
        return self.results

    def _make_results(self) -> Animation:
        return MarqueeAnimation.fromText(self.animation.results_text(), self.size,
                                         freeze=self.config.results_secs())

    def _load_level(self, name):
        """Resolve a menu name to a Level, or None if it can't be loaded."""
        if name in taplib.BUILTIN_LEVELS:
            return taplib.BUILTIN_LEVELS[name]
        path = self.level_files.get(name)
        if path is None:
            return None
        try:
            logger.debug("Loading level '%s' from %s", name, path)
            return taplib.Level.from_file(path)
        except Exception as e:
            logger.error(f"Failed to load level '{name}': {e}")
            return None

    def _play_key(self, token):
        """Route a key press to the lane it's bound to (no-op if unbound).

        While the quit prompt is pending the game keeps running, but taps are
        swallowed (consumed without scoring, so the note auto-misses) rather than
        passed through to menu navigation.
        """
        if self._hs_active or not self._playing():
            return False
        if self._quit_confirm:
            return True
        lane = self.key_lane.get(token)
        if lane is None:
            return False
        when = self.watcher.last_pop_time if self.watcher is not None else None
        self.animation.tap(lane, when)
        return True

    def _hs_key_char(self, c):
        if not self._hs_in_flash() and len(self._hs_name) < _HS_NAME_MAX:
            self._hs_name += c

    def key_enter(self):
        if self._hs_active:
            if not self._hs_in_flash() and len(self._hs_name) >= 1:
                self._hs_commit(self._hs_name)
            return
        if self.animation is not None or self.results is not None:
            return
        name = self.current_value()
        if name in self.subdirs:
            self._descend(self.subdirs[name])
            return
        level = self._load_level(name)
        if level is not None:
            diff = self.config.difficulty_settings()
            if diff.gap > 0:
                level = level.thinned(diff.gap)
            if diff.scroll_time is not None:
                level = dataclasses.replace(level, scroll_time=diff.scroll_time)
            self.key_lane = self.config.key_lane_map()
            level_path    = self.level_files.get(name, '')
            self._reset_hiscore()
            self._cur_level_name = level.name
            self._cur_level_file = os.path.basename(level_path) or level.name
            resolved_audio = (
                self.config.resolve_audio(level.audio, level_path)
                if level.audio and level_path else None
            )
            self.animation = taplib.TapRevolutionAnimation(
                level,
                audio_path=resolved_audio,
                size=self.size,
                **self.config.animation_kwargs(window_scale=diff.window_scale))
            logger.info(
                "Starting level '%s' (notes=%d, difficulty=%s, audio=%s)",
                level.name, len(level.notes),
                self.config.settings.get('difficulty'), resolved_audio or '(none)')

    def key_up(self):
        if self._hs_active:
            return
        if not self._play_key('UP'):
            super().key_up()

    def key_down(self):
        if self._hs_active:
            return
        if not self._play_key('DOWN'):
            super().key_down()

    def key_left(self):
        if not self._play_key('LEFT') and self._browsing():
            self._go_back()

    def key_right(self):
        self._play_key('RIGHT')

    def key_char(self, c):
        if self._hs_active:
            self._hs_key_char(c)
        elif self._quit_confirm:
            if c.lower() == 'y':
                logger.info("Level '%s' cancelled at score=%d",
                            self.animation.level.name, self.animation.score)
                self.animation.stop_audio()
                self.animation     = None
                self._quit_confirm = False
            elif c.lower() == 'n':
                self._quit_confirm = False
        else:
            self._play_key(c)

    def key_esc(self):
        if self._hs_active:
            if not self._hs_in_flash():
                self._hs_commit('')
        elif self._quit_confirm:
            self._quit_confirm = False
        elif self._playing():
            self._quit_confirm = True
        elif self.results is not None:
            self.animation = None
            self.results   = None
        else:
            self._go_back()

    def key_backspace(self):
        if self._hs_active:
            if not self._hs_in_flash() and self._hs_name:
                self._hs_name = self._hs_name[:-1]
        elif self._quit_confirm:
            self._quit_confirm = False
        else:
            self.key_esc()


class TapRevolutionSettingsItem(MenuItem):
    """Editable, scrollable settings for Tap Revolution.

    State machine: browse → edit/key_capture/bucket/sub_browse
    All edits stage into a draft; ESC from browse asks SAVE Y/N (skipped if
    nothing changed). Only a 'y' at that point calls config.save().

    States
    ------
    browse       main list
    edit         scalar field edit (numeric or bool)
    key_capture  waiting for the next key to bind
    sub_browse   inside a sub-menu (Key Mappings or Bad/Ghost Hit)
    bucket       inside a bucket sub-list (threshold / points)
    bucket_edit  numeric edit of a bucket field
    save_confirm SAVE Y/N prompt
    """
    def __init__(self, config, **kwargs):
        super().__init__("Settings", **kwargs)
        self.config = config
        self._reset_state()

    ## ------------------------------------------------------------------ ##
    ## Lifecycle                                                            ##
    ## ------------------------------------------------------------------ ##

    def activate(self):
        self._draft       = copy.deepcopy(self.config.settings)
        self._original    = copy.deepcopy(self.config.settings)
        self._items       = _build_items(self._draft)
        self._idx         = max(0, min(self._idx, len(self._items) - 1))
        self.state        = 'browse'
        self._flash_msg   = ''
        self._flash_until = 0.0

    def reset(self):
        super().reset()
        self._reset_state()

    def _reset_state(self):
        self.state          = 'browse'
        self._draft         = copy.deepcopy(self.config.settings)
        self._original      = copy.deepcopy(self.config.settings)
        self._items         = []
        self._idx           = 0
        self._sub_items     = []
        self._sub_tag       = ''
        self._sub_idx       = 0
        self._edit_entry    = None
        self._return_state  = 'browse'
        self._edit_buffer   = ''
        self._edit_bool     = False
        self._select_options: List = []
        self._select_idx    = 0
        self._bucket_idx    = 0
        self._bucket_sub    = 0
        self._bucket_orig   = None
        self._flash_msg     = ''
        self._flash_until   = 0.0

    ## ------------------------------------------------------------------ ##
    ## Display                                                              ##
    ## ------------------------------------------------------------------ ##

    def for_display(self) -> str:
        if self.state == 'browse':
            return self._display_browse()
        if self.state == 'sub_browse':
            return self._display_sub_browse()
        if self.state == 'edit':
            return self._display_edit()
        if self.state == 'key_capture':
            return "PRESS KEY"
        if self.state == 'bucket':
            return self._display_bucket()
        if self.state == 'bucket_edit':
            return self._display_bucket_edit()
        if self.state == 'save_confirm':
            if self._flash_msg:
                if time.time() >= self._flash_until:
                    self.set_done()
                return self._flash_msg
            return "SAVE Y/N"
        return ''

    def _cursor(self, s) -> str:
        if time.time() % 0.5 < _CURSOR_ON_SECS:
            return s + ' !'
        return s

    def _display_browse(self) -> str:
        if self._flash_msg and time.time() < self._flash_until:
            return self._flash_msg
        if not self._items:
            return "No Settings"
        return self._items[self._idx].label(self._draft)

    def _display_sub_browse(self) -> str:
        if self._flash_msg and time.time() < self._flash_until:
            return self._flash_msg
        if not self._sub_items:
            return "Empty"
        return self._sub_items[self._sub_idx].label(self._draft)

    def _display_edit(self) -> str:
        entry = self._edit_entry
        if entry is None:
            return ''
        if entry.type == 'bool':
            val = "ON" if self._edit_bool else "OFF"
            if time.time() % 0.5 < _CURSOR_ON_SECS:
                return f"{entry.prefix} | {_underline_str(val)}"
            return f"{entry.prefix} | {val}"
        if entry.type == 'select':
            display = self._select_options[self._select_idx][1] if self._select_options else '?'
            if entry.prefix is None:
                if time.time() % 0.5 < _CURSOR_ON_SECS:
                    return _underline_str(display)
                return display
            if time.time() % 0.5 < _CURSOR_ON_SECS:
                return f"{entry.prefix} | {_underline_str(display)}"
            return f"{entry.prefix} | {display}"
        buf = self._edit_buffer
        val_str = f"-{buf}" if entry.tag == 'bad_penalty' else buf
        return self._cursor(f"{entry.prefix} | {val_str}")

    def _display_bucket(self) -> str:
        bucket = self._current_bucket()
        if self._bucket_sub == 0:
            return f"THRESH {round(float(bucket['threshold']) * 1000)}MS"
        return f"POINTS {int(bucket['points'])}"

    def _display_bucket_edit(self) -> str:
        if self._bucket_sub == 0:
            return self._cursor(f"THRESH | {self._edit_buffer}")
        return self._cursor(f"POINTS | {self._edit_buffer}")

    ## ------------------------------------------------------------------ ##
    ## Key dispatch                                                         ##
    ## ------------------------------------------------------------------ ##

    def key_enter(self):
        if self.state == 'browse':
            self._browse_enter()
        elif self.state == 'sub_browse':
            self._sub_enter()
        elif self.state == 'edit':
            self._edit_commit()
        elif self.state == 'bucket':
            self._bucket_enter()
        elif self.state == 'bucket_edit':
            self._bucket_edit_commit()

    def key_esc(self):
        if self.state == 'browse':
            if not self._is_dirty():
                self.set_done()
            else:
                self.state = 'save_confirm'
        elif self.state == 'edit':
            self._edit_buffer = ''
            self.state = self._return_state
        elif self.state == 'key_capture':
            self._edit_entry = None
            self.state = self._return_state
        elif self.state == 'sub_browse':
            self.state = 'browse'
        elif self.state == 'bucket':
            self._bucket_exit()
        elif self.state == 'bucket_edit':
            self._edit_buffer = ''
            self.state = 'bucket'
        elif self.state == 'save_confirm':
            self.state = 'browse'

    def key_backspace(self):
        if self.state == 'edit':
            if self._edit_buffer:
                self._edit_buffer = self._edit_buffer[:-1]
        elif self.state == 'bucket_edit':
            if self._edit_buffer:
                self._edit_buffer = self._edit_buffer[:-1]
        else:
            self.key_esc()

    def _select_cycle(self, delta):
        n = len(self._select_options)
        if n:
            self._select_idx = (self._select_idx + delta) % n

    def key_up(self):
        if self.state == 'browse':
            self._idx = max(0, self._idx - 1)
        elif self.state == 'sub_browse':
            self._sub_idx = max(0, self._sub_idx - 1)
        elif self.state == 'edit':
            if self._edit_entry and self._edit_entry.type == 'bool':
                self._edit_bool = not self._edit_bool
            elif self._edit_entry and self._edit_entry.type == 'select':
                self._select_cycle(-1)
        elif self.state == 'key_capture':
            self._key_capture_commit('up')
        elif self.state == 'bucket':
            self._bucket_sub = max(0, self._bucket_sub - 1)

    def key_down(self):
        if self.state == 'browse':
            self._idx = min(len(self._items) - 1, self._idx + 1)
        elif self.state == 'sub_browse':
            self._sub_idx = min(len(self._sub_items) - 1, self._sub_idx + 1)
        elif self.state == 'edit':
            if self._edit_entry and self._edit_entry.type == 'bool':
                self._edit_bool = not self._edit_bool
            elif self._edit_entry and self._edit_entry.type == 'select':
                self._select_cycle(1)
        elif self.state == 'key_capture':
            self._key_capture_commit('down')
        elif self.state == 'bucket':
            self._bucket_sub = min(1, self._bucket_sub + 1)

    def key_left(self):
        if self.state == 'edit' and self._edit_entry:
            if self._edit_entry.type == 'bool':
                self._edit_bool = not self._edit_bool
            elif self._edit_entry.type == 'select':
                self._select_cycle(-1)
        elif self.state == 'key_capture':
            self._key_capture_commit('left')

    def key_right(self):
        if self.state == 'edit' and self._edit_entry:
            if self._edit_entry.type == 'bool':
                self._edit_bool = not self._edit_bool
            elif self._edit_entry.type == 'select':
                self._select_cycle(1)
        elif self.state == 'key_capture':
            self._key_capture_commit('right')

    def key_char(self, c):
        if self.state == 'save_confirm':
            self._save_confirm_char(c)
        elif self.state == 'edit':
            self._edit_char(c)
        elif self.state == 'bucket_edit':
            if c.isdigit() and len(self._edit_buffer) < 5:
                self._edit_buffer += c
        elif self.state == 'key_capture':
            self._key_capture_commit(c)

    ## ------------------------------------------------------------------ ##
    ## Browse helpers                                                       ##
    ## ------------------------------------------------------------------ ##

    def _browse_enter(self):
        if not self._items:
            return
        entry = self._items[self._idx]
        if entry.type == 'readonly':
            return
        if entry.type == 'submenu':
            self._sub_tag = entry.tag
            if entry.tag == 'key_mappings':
                self._sub_items = _build_key_items()
            elif entry.tag == 'score_ranges':
                self._sub_items = _build_score_items(self._draft)
            else:
                self._sub_items = _build_bad_items(self._draft)
            self._sub_idx = 0
            self.state = 'sub_browse'
        elif entry.type == 'bucket':
            self._enter_bucket()
        else:
            self._enter_edit(entry, 'browse')

    ## ------------------------------------------------------------------ ##
    ## Sub-menu helpers                                                     ##
    ## ------------------------------------------------------------------ ##

    def _sub_enter(self):
        if not self._sub_items:
            return
        entry = self._sub_items[self._sub_idx]
        if entry.type == 'key_binding':
            self._edit_entry   = entry
            self._return_state = 'sub_browse'
            self.state = 'key_capture'
        elif entry.type == 'bucket':
            self._enter_bucket()
        else:
            self._enter_edit(entry, 'sub_browse')

    def _key_capture_commit(self, value):
        if self._edit_entry:
            self._edit_entry.set(self._draft, value)
        self._edit_entry = None
        self.state = self._return_state

    ## ------------------------------------------------------------------ ##
    ## Edit helpers (scalar)                                                ##
    ## ------------------------------------------------------------------ ##

    def _enter_edit(self, entry, return_to):
        self._edit_entry   = entry
        self._return_state = return_to
        if entry.type == 'bool':
            self._edit_bool   = bool(entry.get(self._draft))
            self._edit_buffer = ''
        elif entry.type == 'select':
            self._select_options = entry.options(self._draft) if entry.options else []
            current = entry.get(self._draft)
            self._select_idx = next(
                (i for i, (v, _) in enumerate(self._select_options) if v == current), 0
            )
            self._edit_buffer = ''
        elif entry.type == 'ms':
            self._edit_buffer = str(round(float(entry.get(self._draft)) * 1000))
        elif entry.type == 'int':
            self._edit_buffer = str(abs(int(entry.get(self._draft))))
        elif entry.type == 'signed_int':
            self._edit_buffer = str(int(entry.get(self._draft)))
        else:
            self._edit_buffer = ''
        self.state = 'edit'

    def _edit_char(self, c):
        entry = self._edit_entry
        if entry and entry.type == 'signed_int':
            if c == '-' and self._edit_buffer in ('', '0'):
                self._edit_buffer = '-'
            elif c.isdigit() and len(self._edit_buffer) < 6:
                self._edit_buffer += c
        elif c.isdigit() and len(self._edit_buffer) < 5:
            self._edit_buffer += c

    def _edit_commit(self):
        entry = self._edit_entry
        if entry is None:
            self.state = self._return_state
            return
        if entry.type == 'bool':
            entry.set(self._draft, self._edit_bool)
            if self._return_state == 'sub_browse' and self._sub_tag == 'bad_submenu':
                self._sub_items = _build_bad_items(self._draft)
                self._sub_idx = max(0, min(self._sub_idx, len(self._sub_items) - 1))
        elif entry.type == 'select':
            if self._select_options:
                entry.set(self._draft, self._select_options[self._select_idx][0])
        elif entry.type in ('ms', 'int', 'signed_int') and self._edit_buffer not in ('', '-'):
            raw = int(self._edit_buffer)
            entry.set(self._draft, raw / 1000.0 if entry.type == 'ms' else raw)
        self._edit_buffer = ''
        self._edit_entry  = None
        self.state = self._return_state

    def _is_dirty(self) -> bool:
        return self._draft != self._original

    ## ------------------------------------------------------------------ ##
    ## Bucket helpers                                                       ##
    ## ------------------------------------------------------------------ ##

    def _current_bucket(self):
        return sorted(self._draft['score_buckets'], key=lambda b: float(b['threshold']))[self._bucket_idx]

    def _current_bucket_global_idx(self) -> int:
        target = self._current_bucket()
        for i, b in enumerate(self._draft['score_buckets']):
            if b is target:
                return i
        return 0

    def _enter_bucket(self):
        ## Bucket index is encoded in the tag (e.g. 'bucket_1' → 1)
        self._bucket_idx  = int(self._sub_items[self._sub_idx].tag.split('_')[1])
        self._bucket_sub  = 0
        self._bucket_orig = copy.deepcopy(self._current_bucket())
        self.state = 'bucket'

    def _bucket_enter(self):
        bucket = self._current_bucket()
        self._edit_buffer = (str(round(float(bucket['threshold']) * 1000))
                             if self._bucket_sub == 0 else str(int(bucket['points'])))
        self.state = 'bucket_edit'

    def _bucket_edit_commit(self):
        if self._edit_buffer:
            bucket = self._current_bucket()
            raw = int(self._edit_buffer)
            if self._bucket_sub == 0:
                bucket['threshold'] = raw / 1000.0
            else:
                bucket['points'] = raw
        self._edit_buffer = ''
        self.state = 'bucket'

    def _bucket_exit(self):
        if not TapRevolutionConfig.validate_buckets(self._draft['score_buckets']):
            gi = self._current_bucket_global_idx()
            if self._bucket_orig is not None:
                self._draft['score_buckets'][gi] = self._bucket_orig
            self._flash_msg   = "INVALID"
            self._flash_until = time.time() + _SETTINGS_FLASH_SECS
        self.state = 'sub_browse'  ## return to Score Ranges sub-menu

    ## ------------------------------------------------------------------ ##
    ## Save-confirm helpers                                                 ##
    ## ------------------------------------------------------------------ ##

    def _save_confirm_char(self, c):
        if c.lower() == 'y':
            changed = sorted(k for k in self._draft
                             if self._draft.get(k) != self._original.get(k))
            self.config.settings = copy.deepcopy(self._draft)
            self.config.save()
            logger.info("Tap Revolution settings saved; changed: %s",
                        ', '.join(changed) or '(none)')
            self._flash_msg   = "SAVED"
            self._flash_until = time.time() + _SETTINGS_FLASH_SECS
        elif c.lower() == 'n':
            logger.debug("Tap Revolution settings edit canceled")
            self._flash_msg   = "CANCELED"
            self._flash_until = time.time() + _SETTINGS_FLASH_SECS


class TapRevolutionCalibrationItem(MenuItem):
    """Tap-along calibration that measures BT audio latency and saves it as audio_offset_ms.

    Tap *on the beat* (predictively, the way a musician keeps time) rather than reactively
    after hearing the click. The lead-in beats let you internalize the tempo; your predictive
    taps then encode the BT pipeline delay without adding your audio reaction time on top.
    Reactive tapping would over-compensate by including that reaction time.

    States: idle → playing → result → idle
    """
    def __init__(self, config, *, watcher=None, **kwargs):
        super().__init__("Calibrate", **kwargs)
        self.config  = config
        self.watcher = watcher
        self._audio    = taplib.TapAudioPlayer()
        self._tmp_file = None
        self._reset_state()

    ## ------------------------------------------------------------------ ##
    ## Lifecycle                                                            ##
    ## ------------------------------------------------------------------ ##

    def activate(self):
        self._cleanup()
        self._reset_state()

    def reset(self):
        super().reset()
        self._cleanup()
        self._reset_state()

    def _reset_state(self):
        self.state       = 'idle'
        self._play_start = 0.0
        self._beat_times: List[float] = []
        self._beat_secs  = 60.0 / _CALIB_BPM
        self._n_beats    = _CALIB_BEATS
        self._errors: List[float] = []
        self._result_ms  = 0

    def _cleanup(self):
        self._audio.stop()
        if self._tmp_file is not None:
            try:
                self._tmp_file.close()
                os.unlink(self._tmp_file.name)
            except Exception:
                pass
        self._tmp_file = None

    ## ------------------------------------------------------------------ ##
    ## Display                                                              ##
    ## ------------------------------------------------------------------ ##

    def for_display(self) -> str:
        if self.state == 'idle':
            return "ENTER TO START"
        if self.state == 'playing':
            return self._poll_playing()
        if self.state == 'result':
            sign = '+' if self._result_ms >= 0 else ''
            return f"SET {sign}{self._result_ms}MS Y/N"
        return ''

    def _poll_playing(self) -> str:
        elapsed  = time.time() - self._play_start
        first_t  = self._beat_times[0] if self._beat_times else 0.0
        last_t   = self._beat_times[-1] if self._beat_times else 0.0
        if elapsed < first_t:
            countdown = max(1, math.ceil((first_t - elapsed) / self._beat_secs))
            return f"{countdown} - Tap to beat"
        taps_made = len(self._errors)
        ## Wait for all taps or a 2-beat timeout after the last beat window — whichever
        ## comes first.  The 2-beat window accommodates BT delays up to ~beat_secs.
        if taps_made < self._n_beats and elapsed <= last_t + self._beat_secs * 2:
            return f"BEAT {taps_made + 1}/{self._n_beats}"
        ## All taps registered (or timed out) — wrap up.
        self._result_ms = round(sum(self._errors) / len(self._errors) * 1000) if self._errors else 0
        logger.info("Calibration measured audio_offset_ms=%d from %d/%d taps",
                    self._result_ms, len(self._errors), self._n_beats)
        self._cleanup()
        self.state = 'result'
        sign = '+' if self._result_ms >= 0 else ''
        return f"SET {sign}{self._result_ms}MS Y/N"

    ## ------------------------------------------------------------------ ##
    ## Key dispatch                                                         ##
    ## ------------------------------------------------------------------ ##

    def key_enter(self):
        if self.state == 'idle':
            self._start_calibration()
        elif self.state == 'result':
            self._save_and_exit()

    def key_left(self):
        self._tap()

    def key_right(self):
        self._tap()

    def key_up(self):
        self._tap()

    def key_down(self):
        self._tap()

    def key_char(self, c):
        if self.state == 'result':
            if c.lower() == 'y':
                self._save_and_exit()
            elif c.lower() == 'n':
                self._reset_state()

    def key_esc(self):
        if self.state in ('playing', 'result'):
            logger.debug("Calibration cancelled from state '%s'", self.state)
            self._cleanup()
            self._reset_state()
        else:
            self.set_done()

    def key_backspace(self):
        self.key_esc()

    ## ------------------------------------------------------------------ ##
    ## Calibration helpers                                                  ##
    ## ------------------------------------------------------------------ ##

    def _tap(self):
        if self.state != 'playing':
            return
        ## Reject taps that land in the lead-in before measurement beats begin.
        ## The WAV has clicks for the lead-in too; tapping on those would be
        ## assigned to measurement beats with large negative errors.
        elapsed = time.time() - self._play_start
        if self._beat_times and elapsed < self._beat_times[0]:
            return
        when = self.watcher.last_pop_time if self.watcher is not None else time.time()
        ## Assign this tap to the next unrecorded beat in sequence.  Sequential
        ## assignment (not nearest-beat) is required when BT delay ≈ beat spacing:
        ## nearest-beat would match the tap to beat N+1 instead of beat N, giving
        ## ~0ms error instead of the true delay.
        tap_idx = len(self._errors)
        if tap_idx < len(self._beat_times):
            self._errors.append(when - (self._play_start + self._beat_times[tap_idx]))

    def _load_calibration(self):
        """Return (audio_path, beat_times) from the calibration track or generated WAV."""
        cal_track = str(self.config.settings.get('calibration_track', '') or '')
        if cal_track:
            cal_path = os.path.expanduser(cal_track)
            try:
                level = taplib.Level.from_file(cal_path)
                audio_path = (
                    self.config.resolve_audio(level.audio, cal_path)
                    if level.audio else None
                )
                return audio_path, [n.time for n in level.notes]
            except Exception as e:
                logger.error("Failed to load calibration track '%s': %s", cal_path, e)
        ## Fallback: generated click WAV with lead-in beats.
        wav = _make_click_wav()
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp.write(wav)
        tmp.flush()
        self._tmp_file = tmp
        beat_secs  = 60.0 / _CALIB_BPM
        beat_times = [(i + _CALIB_LEAD_IN) * beat_secs for i in range(_CALIB_BEATS)]
        return tmp.name, beat_times

    def _start_calibration(self):
        self._cleanup()
        self._reset_state()
        audio_path, beat_times = self._load_calibration()
        self._beat_times = beat_times
        self._beat_secs  = (beat_times[1] - beat_times[0]) if len(beat_times) > 1 else 1.0
        self._n_beats    = len(beat_times)
        logger.info("Calibration started (%d beats, audio=%s)",
                    self._n_beats, audio_path or '(click track)')
        if audio_path:
            self._audio.start(audio_path)
        self._play_start = time.time()
        self.state = 'playing'

    def _save_and_exit(self):
        self.config.settings['audio_offset_ms'] = self._result_ms
        self.config.save()
        logger.info("Calibration saved audio_offset_ms=%d", self._result_ms)
        self._reset_state()
        self.set_done()


class ResetSettingsItem(MenuItem):
    """Restore Tap Revolution settings to the defaults file, behind a y/n confirm."""
    def __init__(self, config, **kwargs):
        super().__init__("Reset Settings", **kwargs)
        self.config       = config
        self._flash_until = 0.0

    def activate(self):
        self._flash_until = 0.0

    def reset(self):
        super().reset()
        self._flash_until = 0.0

    def for_display(self) -> str:
        if self._flash_until:
            if time.time() < self._flash_until:
                return "DONE"
            self.set_done()
            return "DONE"
        return "Reset Y/N"

    def key_char(self, c):
        if self._flash_until:
            self.set_done()
        elif c.lower() == 'y':
            self.config.reset()
            logger.info("Tap Revolution settings reset to defaults")
            self._flash_until = time.time() + _SETTINGS_FLASH_SECS
        elif c.lower() == 'n':
            self.set_done()

    def key_enter(self):
        if self._flash_until:
            self.set_done()


class LeaderBoardItem(ListItem):
    """Shows the top scores for one board (``file=None`` ⇒ all-time), one per line.

    Each line is ``"<rank>.<score> <name>"`` (score right-aligned in 4 columns, or
    shown in full when it exceeds 9999) truncated to the display width — never
    marquee'd. Up/Down scroll through the top ten.
    """
    def __init__(self, name, leaderboard, *, file=None, size=16, **kwargs):
        super().__init__(name, **kwargs)
        self.leaderboard = leaderboard
        self.file        = file
        self.size        = size

    def activate(self):
        rows = self.leaderboard.all_time() if self.file is None else self.leaderboard.top(self.file)
        self.idx = 0
        self.set_values([self._format(rank, score, name)
                         for rank, (name, score) in enumerate(rows, 1)] or ["No Scores"])

    def _format(self, rank, score, name) -> str:
        scorefield = f"{score:>4}" if score <= 9999 else str(score)
        return f"{rank}.{scorefield} {name}"[:self.size]


class LeaderBoardMenu(Menu):
    """High-score boards: an 'All Time' entry plus one per level with scores.

    Display name "High Scores". The child list is rebuilt on every ``activate`` so
    newly-scored levels appear without restarting.
    """
    def __init__(self, leaderboard, *, size=16, **kwargs):
        super().__init__("High Scores", [], **kwargs)
        self.leaderboard = leaderboard
        self.size        = size

    def activate(self):
        items = [LeaderBoardItem("All Time", self.leaderboard, size=self.size)]
        items += [LeaderBoardItem(name, self.leaderboard, file=file, size=self.size)
                  for name, file in self.leaderboard.levels()]
        self.items = items
        self.idx   = 0


class ResetScoresItem(MenuItem):
    """Wipe the leaderboard behind a typed 'yes' confirmation and a 3s countdown.

    State machine: confirm (type YES) → countdown (any key cancels) → flash. A wrong
    word, ESC, or a cancelled countdown flashes 'canceled' and returns to the menu;
    completing the countdown backs up the file (dated) and writes a blank board.
    """
    def __init__(self, leaderboard, **kwargs):
        super().__init__("Reset Scores", **kwargs)
        self.leaderboard = leaderboard
        self._reset_state()

    def _reset_state(self):
        self.state        = 'confirm'
        self._buffer      = ''
        self._count_start = 0.0
        self._flash_msg   = ''
        self._flash_until = 0.0

    def activate(self):
        self._reset_state()

    def reset(self):
        super().reset()
        self._reset_state()

    def for_display(self) -> str:
        if self.state == 'flash':
            if time.time() >= self._flash_until:
                self.set_done()
            return self._flash_msg
        if self.state == 'countdown':
            if time.time() - self._count_start >= _RESET_COUNTDOWN:
                self.leaderboard.reset()
                logger.info("Leaderboard reset")
                return self._flash("DELETED")
            remaining = max(1, math.ceil(_RESET_COUNTDOWN - (time.time() - self._count_start)))
            return f"Deleting in {remaining}"
        field = f"TYPE YES|{self._buffer}"
        if time.time() % 0.5 < _CURSOR_ON_SECS:
            return field + ' !'
        return field

    def _flash(self, msg) -> str:
        self.state        = 'flash'
        self._flash_msg   = msg
        self._flash_until = time.time() + _SETTINGS_FLASH_SECS
        return msg

    def key_char(self, c):
        if self.state == 'confirm':
            self._buffer += c
        elif self.state == 'countdown':
            self._flash("canceled")
        else:
            self.set_done()

    def key_enter(self):
        if self.state == 'confirm':
            if self._buffer.strip().lower() == 'yes':
                self.state        = 'countdown'
                self._count_start = time.time()
            else:
                self._flash("canceled")
        elif self.state == 'countdown':
            self._flash("canceled")
        else:
            self.set_done()

    def key_backspace(self):
        if self.state == 'confirm':
            self._buffer = self._buffer[:-1]
        elif self.state == 'countdown':
            self._flash("canceled")
        else:
            self.set_done()

    def key_esc(self):
        if self.state == 'flash':
            self.set_done()
        else:
            self._flash("canceled")

    def key_arrow(self, d):
        if self.state == 'countdown':
            self._flash("canceled")
        elif self.state == 'flash':
            self.set_done()
