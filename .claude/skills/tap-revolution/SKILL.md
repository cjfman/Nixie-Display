---
name: tap-revolution
description: Reference for the Tap Revolution rhythm game — architecture, data model, config files, menu structure, and the .trl level format. Use when reading, writing, or debugging files in pyxielib/tap_revolution.py, pyxielib/tap_revolution_config.py, pyxielib/menu_library.py (TapRevolutionMenu), levels/, or config/tap_revolution*.yaml.
---

# Tap Revolution

A Dance Dance Revolution-style rhythm game for the 16-tube nixie display. Arrows
scroll across the tubes toward a hit zone; the player presses the matching arrow key
as each arrow arrives.

## Module map

| File | Role |
|---|---|
| `pyxielib/tap_revolution.py` | Pure game logic — `Note`, `Level`, `TapRevolutionAnimation`. No file I/O or menu deps. |
| `pyxielib/tap_revolution_config.py` | `TapRevolutionConfig` — loads/merges YAML settings, adapts them for the animation. |
| `pyxielib/menu_library.py` | `TapRevolutionMenu` and its three children (Play, Settings, Reset Settings). |
| `pyxielib/key_watcher.py` | `last_pop_time` on both watchers — key capture timestamp for accurate scoring. |
| `levels/` | `.trl` level files (beat-mode or time-mode). |
| `config/tap_revolution.defaults.yaml` | Version-controlled default settings. |
| `config/tap_revolution.yaml` | Runtime persistent settings (gitignored — created from defaults on first run). |
| `scripts/test_tap_revolution.py` | Terminal preview (`--autoplay`, `--jitter`) and `--record` authoring mode. |

---

## Data model (`tap_revolution.py`)

### `Note`
```python
@dataclass
class Note:
    time: float   # absolute seconds from chart start — the source of truth
    lane: str     # one of 'L', 'R', 'U', 'D'
```
Beats are just one way to *express* a time (`offset + beat * 60 / bpm`); the engine
and scoring always work in absolute seconds.

### `Level`
```python
@dataclass
class Level:
    name: str
    notes: List[Note]         # sorted by time
    bpm: Optional[float]      # only needed for beat-mode authoring
    offset: float             # seconds before the first beat
    scroll_time: float        # seconds for an arrow to cross the full track (default 2.0)
    audio: Optional[str]      # reserved for future audio sync
```

**Constructors:**
- `Level.from_file(path)` — parse a `.trl` file.
- `Level.from_string(text)` — parse `.trl` content from a string.
- `Level.from_beats(name, bpm, beats, **kw)` — `beats` is `[(beat, lane), ...]`.
- `Level.from_times(name, times, **kw)` — `times` is `[(seconds, lane), ...]`.
- `Level.read_title(path)` — read only the `name:` header (cheap, for menu listing).

**Programmatic levels** available without any file:
```python
from pyxielib.tap_revolution import BUILTIN_LEVELS
level = BUILTIN_LEVELS["Demo <builtin>"]
```

---

## Level file format (`.trl`)

Two modes — the parser auto-detects based on the `mode:` header (or presence of `bpm:`).

### Beat mode
```
name: My Level
mode: beat          # default when bpm is present
bpm: 120
offset: 0.0
scroll_time: 2.0
audio:              # reserved

# beat   arrow(s)
1.0    left
2.0    right, down  # simultaneous notes (comma-separated)
3.0    up
```

### Time mode (author from a recording — no BPM needed)
```
name: My Level <by ear>
mode: time          # note values are absolute seconds
scroll_time: 2.0

# sec    arrow(s)
0.50   left
1.00   right, down
1.50   up
```

**Arrow names:** `left`/`right`/`up`/`down` (also `l`/`r`/`u`/`d`).  
**Header rules:** `key: value` lines before the first note; `#` comments and blank
lines ignored. `mode` defaults to `beat` if `bpm` is present, else `time`.  
**Nixie printability:** use `<` and `>` (not `(` and `)`) in level names — parens
are `NOCODE` on the display.

---

## `TapRevolutionAnimation`

Subclass of `Animation`. The Assembler drives it at 1 ms polling; scoring runs in
the scheduler/menu thread — a `threading.Lock` guards shared state.

### Constructor (all keyword-only after `level`)
```python
TapRevolutionAnimation(
    level,
    size=16, score_width=4,
    hit_windows=DEFAULT_HIT_WINDOWS,   # ((name, threshold_s, points), ...)
    grace=DEFAULT_GRACE,               # s past ok_window before auto-miss
    cooldown=DEFAULT_COOLDOWN,         # s bad-tap lane lockout (0 disables)
    bad_penalty=DEFAULT_BAD_PENALTY,   # points docked per BAD tap
    bad_enabled=True,                  # False -> ghost taps are a no-op
    judge_flash=True,                  # flash judgement word in score section
    flash_secs=0.6,
    hit_flash_frames=HIT_FLASH_FRAMES,           # ('x', '+', 'x')
    hit_flash_frame_secs=HIT_FLASH_FRAME_SECS,   # 0.05
    lead_in=None,                      # defaults to scroll_time
)
```

### Key methods
- `tap(lane, when=None)` — score a tap. `when` is the key's capture epoch time
  (`watcher.last_pop_time`); defaults to `time.time()`. Thread-safe.
- `done()` — True once the last arrow has scrolled off + 0.5 s tail.
- `reset()` — re-anchor and clear all state; the animation is replayable.
- `results() -> Dict[str, int]` — tally of judgement words + `'SCORE'`.
- `results_text() -> str` — human-readable summary for the results marquee;
  ends with `SCORE n` so the marquee ends on the headline number.
- `__eq__` is identity — `Program.update()` never resets a live game.

### Scoring and judgement

| Bracket | Default window | Points |
|---|---|---|
| BEST | ≤ 45 ms | 100 |
| GOOD | ≤ 90 ms | 70 |
| OK | ≤ 140 ms | 40 |
| MISS | auto (past ok + grace) | 0 |
| BAD | ghost tap (nothing in window) | −5, combo reset |

- **Real hits are never gated** by the bad-tap cooldown — only ghost/BAD taps lock
  a lane. `cooldown=0` disables the lockout (every ghost tap is BAD).
- **`bad_enabled=False`** makes ghost taps a complete no-op; `BAD` is also omitted
  from `results()` and `results_text()`.
- Scoring uses `when` (the **capture timestamp**), not poll time, so accuracy is
  independent of the ~20 ms scheduler poll cadence.

### Glyphs
```python
LANE_GLYPH = {'L': '<', 'R': '>', 'U': '^', 'D': '{0x0140}'}
```
`{0x0140}` is a `\ /` chevron (down arrow) mirroring `^` (`/ \`). Simultaneous
notes on the same tube are OR'd into one combined bitmap so chords are visible.

---

## Configuration (`tap_revolution_config.py`)

### `TapRevolutionConfig(defaults_path=None, persistent_path=None)`

Merge order: **code constants ← defaults file ← persistent file**.

On first run, if the persistent file is missing it is seeded from the defaults file.
With no paths, falls back to code constants with no persistence.

```python
cfg = TapRevolutionConfig(
    'config/tap_revolution.defaults.yaml',
    'config/tap_revolution.yaml',        # runtime; gitignored
)
```

**Key methods:**
- `animation_kwargs() -> dict` — pass directly to `TapRevolutionAnimation(level, size, **cfg.animation_kwargs())`.
- `key_lane_map() -> Dict[str, str]` — maps incoming key tokens to lanes; pass to
  the levels item so play routing goes through the config. Tokens are the Navigator
  strings (`'LEFT'`, `'RIGHT'`, `'UP'`, `'DOWN'`) or a literal character (`'a'`).
- `summary_lines() -> List[str]` — printable, nixie-safe lines for the Settings view.
- `results_secs() -> int` — freeze duration for the results marquee.
- `save()` — persist current settings (for future in-game edits).
- `reset()` — restore defaults into the persistent file and active settings.

### Settings schema (`tap_revolution.defaults.yaml`)
```yaml
keys:          {left: left, right: right, up: up, down: down}
score_buckets:
  - {name: BEST, threshold: 0.045, points: 100}
  - {name: GOOD, threshold: 0.090, points: 70}
  - {name: OK,   threshold: 0.140, points: 40}
bad_tap:       {enabled: true, cooldown: 0.15, penalty: 5}
grace: 0.12
flash_secs: 0.6
score_width: 4
judge_flash: true
hit_flash:     {frames: [x, +, x], frame_secs: 0.05}
results_secs: 6
```

**`keys`:** values are arrow names (`left`/`right`/`up`/`down`) or a single
character (`a`, `d`, etc.) for a custom key layout.  
**`bad_tap.enabled: false`** — ghost taps become a no-op; `BAD` is hidden from results.  
**`bad_tap.cooldown: 0`** — disables the lane lockout (every ghost tap is BAD).

---

## Menu structure (`menu_library.py`)

```
TapRevolutionMenu (Menu)
├── TapRevolutionLevelsItem "Play"   (ListItem)
├── TapRevolutionSettingsItem        (ListItem, read-only)
└── ResetSettingsItem                (MenuItem, y/n confirm)
```

**`TapRevolutionMenu(config, levels_path, *, watcher, size)`** — the top-level
entry in the Nixie Menu. `watcher` is the `KeyWatcher`/`TerminalKeyWatcher`; its
`last_pop_time` is passed into `tap()` for capture-accurate scoring.

**`TapRevolutionLevelsItem`:**
- `activate()` — scans `levels_path` for `.trl` files, titles them by `name:` header
  (falling back to filename stem), disambiguates duplicates with `<N>`, and prepends
  `BUILTIN_LEVELS`.
- `key_enter()` — loads the selected level, builds `TapRevolutionAnimation` from
  `config.animation_kwargs()`, caches `config.key_lane_map()`.
- During play, `key_up/down/left/right` and `key_char(c)` all route through
  `_play_key(token)`, which looks up the lane in the cached key map. List navigation
  resumes when not playing.
- ESC/Backspace while playing → results marquee; while viewing results → back to list.

**`TapRevolutionSettingsItem`:** `activate()` refreshes lines from `config.summary_lines()`.

**`ResetSettingsItem`:** shows `Reset Y/N`; `y` → `config.reset()` → `Settings reset`; `n` or Enter on done → exits. Reset takes effect on the next `key_enter` (next level launch).

---

## Key capture timestamps

Both `KeyWatcher` (evdev) and `TerminalKeyWatcher` expose `last_pop_time` (epoch
seconds) after each `pop()`. The levels item reads this before calling `tap()`:

```python
when = self.watcher.last_pop_time if self.watcher is not None else None
self.animation.tap(lane, when)
```

`evdev` events carry a hardware timestamp; `TerminalKeyWatcher` uses `time.time()`
at enqueue. Both are epoch seconds matching `TapRevolutionAnimation.start_time`.

---

## Master config and polling

**`pyxielib/config.py`** handles the master YAML (`--config`).
Precedence: **CLI > config file > hardcoded default** (`config.resolve`).

Tap Revolution paths live under `tap_revolution:` in the master config:
```yaml
tap_revolution:
  defaults_file: config/tap_revolution.defaults.yaml
  persistent_file: config/tap_revolution.yaml
```

Polling periods (in `polling:`) are clamped to safe bounds:
| Key | Default | Clamp |
|---|---|---|
| `assembler_poll_interval` | 0.001 s | [0.0005, 0.05] |
| `scheduler_period` | 0.1 s | [0.01, 5.0] |
| `scheduler_active_period` | 0.02 s | [0.005, period] |

`scheduler_active_period` is the poll cadence while the user menu is open (keeps
key→feedback latency ~20 ms during gameplay). It is capped at `scheduler_period`.

---

## Testing

```bash
# Full autoplay (all notes hit perfectly)
python scripts/test_tap_revolution.py -a levels/demo.trl --autoplay --no-clear

# Test timing brackets: +0.07s -> GOOD, +0.12s -> OK, +0.30s -> all MISS
python scripts/test_tap_revolution.py -a levels/demo.trl --autoplay --jitter 0.07 --no-clear

# Play a builtin level
python scripts/test_tap_revolution.py -a "Demo <builtin>" --autoplay --no-clear

# Record a new chart by ear (outputs a time-mode .trl to stdout)
python scripts/test_tap_revolution.py --record --name "My Song"
```

Expected autoplay result (perfect game): `BEST 12  GOOD 0  OK 0  MISS 0  BAD 0  SCORE 1200`

---

## What's still future

- **In-game settings editing** — Settings submenu is read-only display + Reset for
  now. `TapRevolutionConfig.save()` exists for when editing is added.
- **CronScheduler schedule configurable** — currently hardcoded in `run_display`.
  The config loader returns a plain dict so a future `schedule:` key is localized.
- **Audio sync** — `Level.audio` and the `.trl` `audio:` key are reserved. Notes
  are already on an absolute-seconds timeline; `start_time` is the single anchor
  that audio playback would also use.
