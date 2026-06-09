"""Menu classes for the Tap Revolution rhythm game.

Kept separate from menu_library.py so the game's interactive UI lives near
its config and game-logic siblings rather than in the general menu module.

Public classes: TapRevolutionMenu (the top-level sub-menu entry in the Nixie
Menu), plus the three children it creates: TapRevolutionLevelsItem (Play),
TapRevolutionSettingsItem (editable settings), and ResetSettingsItem.
"""

import collections
import copy
import logging
import os
import time
from typing import List, Optional

from pyxielib import tap_revolution as taplib
from pyxielib.animation import Animation, MarqueeAnimation
from pyxielib.navigator import ListItem, Menu, MenuItem
from pyxielib.tap_revolution_config import TapRevolutionConfig

logger = logging.getLogger(__name__)

## ─────────────────────────────────────────────────────────────────────────── ##
## Settings list helpers                                                        ##
## ─────────────────────────────────────────────────────────────────────────── ##

## Named tuple for one row in the settings list.
## prefix is the short title shown before ' | ' in edit mode.
## set is None for readonly and submenu entries.
_SettingEntry = collections.namedtuple('_SettingEntry', ['tag', 'type', 'label', 'prefix', 'get', 'set'])

## Seconds the cursor is ON within each 0.5 s blink period.
_CURSOR_ON_SECS = 0.25
## Duration to show validation-failure flash messages.
_SETTINGS_FLASH_SECS = 1.0

_ARROW_NAMES = {'left', 'right', 'up', 'down'}


def _s_entry(tag, kind, label_fn, prefix, get_fn, set_fn=None):
    return _SettingEntry(tag, kind, label_fn, prefix, get_fn, set_fn)


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
        super().__init__("Tap Revolution", [
            TapRevolutionLevelsItem(config, levels_path, watcher=watcher, size=size),
            TapRevolutionSettingsItem(config),
            ResetSettingsItem(config),
        ], **kwargs)


class TapRevolutionLevelsItem(ListItem):
    """Dance-Dance-Revolution-style rhythm game.

    Lists levels (``.trl`` files in ``levels_path`` plus the built-in
    programmatic charts); selecting one launches a ``TapRevolutionAnimation`` built
    from the shared config. While playing, the configured action keys are taps
    routed into the animation — timestamped by the key watcher so hit timing is
    accurate regardless of poll latency — and list navigation is suspended. When
    the chart finishes (or the player backs out early) a results marquee plays
    before returning to the level list.
    """
    def __init__(self, config, levels_path, *, watcher=None, size=16, **kwargs):
        super().__init__("Play", **kwargs)
        self.config      = config
        self.levels_path = levels_path
        self.watcher     = watcher
        self.size        = size
        self.level_files = {}
        self.animation   = None
        self.results     = None
        self.key_lane    = {}

    def activate(self):
        self.level_files = self._scan_levels()
        self.set_values(sorted(taplib.BUILTIN_LEVELS) + sorted(self.level_files))

    def _scan_levels(self):
        """Map each .trl file's 'name:' title to its path; built-ins added separately."""
        try:
            files = sorted(x for x in os.listdir(self.levels_path) if x.endswith('.trl'))
        except OSError:
            return {}

        levels = {}
        counts = {}
        for f in files:
            path = os.path.join(self.levels_path, f)
            levels[self._unique_name(taplib.Level.read_title(path), counts)] = path

        return levels

    @staticmethod
    def _unique_name(title, counts) -> str:
        """Disambiguate a repeated title by appending '<N>' (first keeps the bare title)."""
        seen = counts.get(title, 0)
        counts[title] = seen + 1
        return title if seen == 0 else f"{title}<{seen + 1}>"

    def reset(self):
        super().reset()
        self.set_values(None)
        self.level_files = {}
        self.animation   = None
        self.results     = None
        self.key_lane    = {}

    def _playing(self) -> bool:
        return self.animation is not None and self.results is None

    def for_display(self):
        if self.results is not None:
            if self.results.done():
                self.animation = None
                self.results = None
                return super().for_display()
            return self.results
        if self.animation is not None:
            if self.animation.done():
                self.results = self._make_results()
                return self.results
            return self.animation

        return super().for_display()

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
            return taplib.Level.from_file(path)
        except Exception as e:
            logger.error(f"Failed to load level '{name}': {e}")
            return None

    def _play_key(self, token):
        """Route a key press to the lane it's bound to (no-op if unbound)."""
        if not self._playing():
            return False
        lane = self.key_lane.get(token)
        if lane is None:
            return False
        when = self.watcher.last_pop_time if self.watcher is not None else None
        self.animation.tap(lane, when)
        return True

    def key_enter(self):
        if self.animation is not None or self.results is not None:
            return
        level = self._load_level(self.current_value())
        if level is not None:
            self.key_lane = self.config.key_lane_map()
            self.animation = taplib.TapRevolutionAnimation(level, size=self.size, **self.config.animation_kwargs())

    def key_up(self):
        if not self._play_key('UP'):
            super().key_up()

    def key_down(self):
        if not self._play_key('DOWN'):
            super().key_down()

    def key_left(self):
        self._play_key('LEFT')

    def key_right(self):
        self._play_key('RIGHT')

    def key_char(self, c):
        self._play_key(c)

    def key_esc(self):
        if self._playing():
            self.results = self._make_results()
        elif self.results is not None:
            self.animation = None
            self.results = None
        else:
            self.set_done()

    def key_backspace(self):
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

    def key_up(self):
        if self.state == 'browse':
            self._idx = max(0, self._idx - 1)
        elif self.state == 'sub_browse':
            self._sub_idx = max(0, self._sub_idx - 1)
        elif self.state == 'edit':
            if self._edit_entry and self._edit_entry.type == 'bool':
                self._edit_bool = not self._edit_bool
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
        elif self.state == 'key_capture':
            self._key_capture_commit('down')
        elif self.state == 'bucket':
            self._bucket_sub = min(1, self._bucket_sub + 1)

    def key_left(self):
        if self.state == 'edit' and self._edit_entry and self._edit_entry.type == 'bool':
            self._edit_bool = not self._edit_bool
        elif self.state == 'key_capture':
            self._key_capture_commit('left')

    def key_right(self):
        if self.state == 'edit' and self._edit_entry and self._edit_entry.type == 'bool':
            self._edit_bool = not self._edit_bool
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
        elif entry.type == 'ms':
            self._edit_buffer = str(round(float(entry.get(self._draft)) * 1000))
        elif entry.type == 'int':
            self._edit_buffer = str(abs(int(entry.get(self._draft))))
        else:
            self._edit_buffer = ''
        self.state = 'edit'

    def _edit_char(self, c):
        if c.isdigit() and len(self._edit_buffer) < 5:
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
        elif entry.type in ('ms', 'int') and self._edit_buffer:
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
            self.config.settings = copy.deepcopy(self._draft)
            self.config.save()
            self._flash_msg   = "SAVED"
            self._flash_until = time.time() + _SETTINGS_FLASH_SECS
        elif c.lower() == 'n':
            self._flash_msg   = "CANCELED"
            self._flash_until = time.time() + _SETTINGS_FLASH_SECS


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
            self._flash_until = time.time() + _SETTINGS_FLASH_SECS
        elif c.lower() == 'n':
            self.set_done()

    def key_enter(self):
        if self._flash_until:
            self.set_done()
