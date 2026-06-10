---
name: tap-revolution
description: Reference for the Tap Revolution rhythm game — architecture, data model, config, in-game settings editing, and the .trl level format. Use when reading, writing, or debugging files in pyxielib/tap_revolution.py, pyxielib/tap_revolution_config.py, pyxielib/tap_revolution_menu.py, levels/, or config/tap_revolution*.yaml.
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
| `pyxielib/tap_revolution_menu.py` | All four menu classes (see Menu structure below). |
| `pyxielib/key_watcher.py` | `last_pop_time` on both watchers — key capture timestamp for accurate scoring. |
| `levels/` | `.trl` level files (beat-mode or time-mode). |
| `config/tap_revolution.defaults.yaml` | Version-controlled default settings. |
| `config/tap_revolution.yaml` | Runtime persistent settings (gitignored — seeded from defaults on first run). |
| `scripts/test_tap_revolution.py` | Terminal preview (`--autoplay`, `--jitter`) and `--record` authoring mode. |

`usermenuprogram.py` imports `tap_revolution_menu` as `trmenulib` (not via `menu_library`).

---

## Data model (`tap_revolution.py`)

### `Note`
```python
@dataclass
class Note:
    time: float   # absolute seconds from chart start — the source of truth
    lane: str     # one of 'L', 'R', 'U', 'D'
```
Beats are one way to express a time (`offset + beat * 60 / bpm`); the engine and
scoring always use absolute seconds.

### `Level`
```python
@dataclass
class Level:
    name: str
    notes: List[Note]         # sorted by time
    bpm: Optional[float]      # only needed for beat-mode authoring
    offset: float
    scroll_time: float        # seconds for an arrow to cross the track (default 2.0)
    audio: Optional[str]      # reserved for future audio sync
```

Constructors: `Level.from_file(path)`, `Level.from_string(text)`,
`Level.from_beats(name, bpm, beats, **kw)`, `Level.from_times(name, times, **kw)`,
`Level.read_title(path)` (cheap header-only read for menu listing).

Programmatic: `from pyxielib.tap_revolution import BUILTIN_LEVELS` →
`BUILTIN_LEVELS["Demo <builtin>"]`.

---

## Level file format (`.trl`)

```
# Beat mode (BPM known)          # Time mode (from a recording)
name: My Level                   name: My Level <by ear>
mode: beat                       mode: time
bpm: 120                         scroll_time: 2.0
offset: 0.0
scroll_time: 2.0

1.0   left                       0.50  left
2.0   right, down                1.00  right, down
3.0   up                         1.50  up
```

Arrow names: `left`/`right`/`up`/`down` (or `l`/`r`/`u`/`d`). `mode` defaults
to `beat` if `bpm` present, else `time`. Use `<>` not `()` in names — parens are
NOCODE on the display.

---

## `TapRevolutionAnimation`

Assembler drives `updateFrameSet()` at 1 ms; scoring runs in the scheduler/menu
thread. A `threading.Lock` guards shared state. `__eq__` is identity.

```python
TapRevolutionAnimation(
    level,
    size=16, score_width=4,
    hit_windows=DEFAULT_HIT_WINDOWS,   # ((name, threshold_s, points), ...)
    grace=DEFAULT_GRACE,               # s past ok_window before auto-miss
    cooldown=DEFAULT_COOLDOWN,         # s bad-tap lane lockout after a BAD tap (0=off)
    bad_penalty=DEFAULT_BAD_PENALTY,   # points docked per BAD tap
    bad_enabled=True,                  # False -> ghost taps are a no-op
    judge_flash=True,
    flash_secs=0.6,
    hit_flash_frames=HIT_FLASH_FRAMES,         # ('x', '+', 'x')
    hit_flash_frame_secs=HIT_FLASH_FRAME_SECS, # 0.05
    lead_in=None,                      # defaults to scroll_time
)
```

Key methods: `tap(lane, when=None)`, `done()`, `reset()`, `results()`,
`results_text()`.

### Scoring

| Bracket | Default window | Points |
|---|---|---|
| BEST | ≤ 45 ms | 100 |
| GOOD | ≤ 90 ms | 70 |
| OK | ≤ 140 ms | 40 |
| MISS | auto (past ok + grace) | 0 |
| BAD | ghost tap, nothing in window | −5, combo reset |

**Cooldown is bad-taps-only**: real hits are never gated; only ghost/BAD taps
lock a lane. `cooldown=0` disables; `bad_enabled=False` makes ghost taps a no-op
and hides BAD from results.

### Glyphs
```python
LANE_GLYPH = {'L': '<', 'R': '>', 'U': '^', 'D': '{0x0140}'}
```
`{0x0140}` = `\ /` chevron (mirrors `^` = `/ \`). Chords on the same tube are
OR'd into one combined bitmap.

---

## Configuration (`tap_revolution_config.py`)

Merge order: **code constants ← defaults file ← persistent file**.

```python
cfg = TapRevolutionConfig(
    'config/tap_revolution.defaults.yaml',
    'config/tap_revolution.yaml',
)
```

Key methods: `animation_kwargs()`, `key_lane_map()`, `results_secs()`,
`summary_lines()`, `save()`, `reset()`, `validate_buckets(buckets)` (static).

`key_lane_map()` returns `{token: lane}` where tokens are `'LEFT'`/`'RIGHT'`/
`'UP'`/`'DOWN'` for arrow keys or a literal char (`'a'`).

Settings schema:
```yaml
keys:          {left: left, right: right, up: up, down: down}
score_buckets:
  - {name: BEST, threshold: 0.045, points: 100}
  - {name: GOOD, threshold: 0.090, points: 70}
  - {name: OK,   threshold: 0.140, points: 40}
bad_tap:       {enabled: true, cooldown: 0.15, penalty: 5}
grace: 0.12
flash_secs: 0.6
score_width: 4      ## read-only in settings UI
judge_flash: true
hit_flash:     {frames: [x, +, x], frame_secs: 0.05}   ## read-only in settings UI
results_secs: 6
```

`run_display.make_tap_config(cfg)` falls back to the project-relative
`config/tap_revolution.defaults.yaml` and `config/tap_revolution.yaml` when the
master config omits `tap_revolution:` paths, so settings persist without `--config`.

---

## Menu structure (`tap_revolution_menu.py`)

```
TapRevolutionMenu (Menu)
├── TapRevolutionLevelsItem  "Play"          (ListItem)
├── TapRevolutionSettingsItem "Settings"     (MenuItem, full state machine)
└── ResetSettingsItem         "Reset Settings" (MenuItem, y/n → flashes DONE)
```

### `TapRevolutionLevelsItem`
Lists `.trl` files (by `name:` header) + `BUILTIN_LEVELS`. `key_enter()` launches
a game built from `config.animation_kwargs()` and caches `config.key_lane_map()`.
Arrow keys and `key_char` route through the key map during play; ESC aborts to
results marquee.

### `TapRevolutionSettingsItem` — state machine

States: `browse` → `edit` / `key_capture` / `sub_browse` / `bucket` / `bucket_edit`
→ `save_confirm`.

All edits go to a `_draft` copy. ESC from `browse`:
- no changes → `set_done()` immediately (no prompt)
- dirty → `save_confirm` ("SAVE Y/N")
  - `y` → save + flash "SAVED" (1 s) → auto-dismiss
  - `n` → flash "CANCELED" (1 s) → auto-dismiss
  - ESC → cancel the confirm, back to browse

Sub-menus (entered via `sub_browse` state):
- **KEY MAPPINGS** — Left / Right / Up / Down key bindings. `key_enter` opens
  `key_capture` ("PRESS KEY"); the next char or arrow key auto-commits.
  Arrow values display as e.g. `"left key"`.
- **SCORE RANGES** — BEST / GOOD / OK buckets. Entering one opens `bucket`
  (thresh/points sub-list). ESC validates all buckets on exit — thresholds must
  strictly increase, points strictly decrease; on failure the edited bucket is
  reverted and "INVALID" flashes.
- **BAD/GHOST HIT** — bad_tap.enabled (bool), penalty (int), cooldown (ms,
  hidden when disabled).

Edit mode display format: `"{title} | {value_or_buffer}"`.
- Numeric: blinking `" !"` cursor; backspace deletes digits; bad penalty always
  shows `-` prefix (display-only, can't be backspaced).
- Bool: title shown, value word blinks with underline (e.g. `"JUDGE | O!N!"`).
- Buffer pre-filled with current value on entry.
- `score_width` and `hit_flash` are read-only (visible, Enter does nothing).

### `ResetSettingsItem`
"Reset Y/N" → `y` calls `config.reset()` then flashes "DONE" for 1 s → auto-dismiss.
Enter or any key during the flash dismisses early.

---

## Key capture timestamps

Both `KeyWatcher` and `TerminalKeyWatcher` expose `last_pop_time` (epoch seconds)
after each `pop()`. The levels item passes it to `tap()` so accuracy is independent
of the ~20 ms scheduler poll cadence.

`UserMenuProgram.makeAnimation()` checks `navigator.node.is_done()` after
`for_display()`, so timed auto-dismiss (flash expiry → `set_done()`) fires without
needing a keypress.

---

## Testing

```bash
python scripts/test_tap_revolution.py -a levels/demo.trl --autoplay --no-clear
python scripts/test_tap_revolution.py -a levels/demo.trl --autoplay --jitter 0.07 --no-clear
python scripts/test_tap_revolution.py -a "Demo <builtin>" --autoplay --no-clear
python scripts/test_tap_revolution.py --record --name "My Song"
```

Expected perfect-game result: `BEST 12  GOOD 0  OK 0  MISS 0  BAD 0  SCORE 1200`

---

## What's still future

- **CronScheduler schedule** — hardcoded in `run_display`; the config loader
  returns a plain dict so a `schedule:` key is a localized add when ready.
- **Audio sync** — `Level.audio` and `.trl` `audio:` are reserved. Notes are on
  the same absolute-seconds timeline audio would use; `start_time` is the single
  anchor.
- **In-game settings value editing for new settings** — future settings should be
  assumed read-only in the UI unless explicitly made editable.
- **Hold notes** — SSC types `2` (hold head) and `4` (roll head) are currently
  converted to plain taps by `scripts/ssc_to_trl.py`; tails (`3`) are dropped.
  True hold support would require the animation to sustain a lane highlight and
  score a "held" judgement over the duration.
- **Mines** — SSC type `M` (avoid hitting) is currently dropped by the converter.
  Mine support would penalise a tap that lands on a mine's tube within its window.
- **Native SSC playback** — skipping the `.trl` conversion step and loading SSC
  files directly; would require `.trl` format extensions or a new `Level`
  constructor.
