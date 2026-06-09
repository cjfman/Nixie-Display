---
name: app-config
description: Reference for the master YAML config system — pyxielib/config.py, run_display CLI args and config precedence, polling period clamps, and how the TapRevolutionConfig is wired in. Use when adding CLI args, changing config-overridable settings, adjusting polling periods, or debugging why a setting isn't persisting.
---

# App Config System

The app supports an optional YAML master config (`--config`) that can supply values
for a set of CLI args. Precedence is **CLI arg > config file > hardcoded default**.

## `pyxielib/config.py`

The loader module. No side effects; returns plain dicts.

```python
load_config(path) -> dict          # empty dict if path is None; raises ConfigError on bad file
resolve(cli_value, cfg, key, default)  # first non-None of CLI > cfg[key] > default
clamp(value, lo, hi)               # inclusive range clamp
polling_periods(cfg) -> dict       # reads 'polling:' section, clamps values
```

`resolve` treats `False` as a set value, so a boolean config option is honored
unless the CLI explicitly overrides it.

## `run_display` — CLI args and config precedence

Config is loaded in `get_args()`:
```python
cfg = config.load_config(args.config)   # {} if no --config
args.animations_dir = config.resolve(args.animations_dir, cfg, 'animations_dir', default_path)
# ... and so on for each overridable arg
```

**Config-overridable CLI args** (config key = snake_case of arg name):

| CLI arg | Config key | Default |
|---|---|---|
| `-s`/`--serial` | `serial` | None |
| `--keyboard-event-file` | `keyboard_event_file` | None |
| `--animations-dir` | `animations_dir` | `<project>/animations` |
| `--levels-dir` | `levels_dir` | `<project>/levels` |
| `--extended-hours` / `--no-extended-hours` | `extended_hours` | `false` |
| `--git-check` | `git_check` | None (disabled) |
| `--git-origin` | `git_origin` | `@{u}` |
| `--logfile` | `logfile` | None (stdout) |
| `--loglevel` | `loglevel` | `info` |

`--extended-hours` uses `argparse.BooleanOptionalAction` (requires Python ≥ 3.9,
which is what the Pi runs). Both `--extended-hours` and `--no-extended-hours` default
to `None` so the config can be overridden in either direction.

**Config-only keys** (no CLI equivalent):
```yaml
controller:
  raspi_speed: 1000000      # SPI clock speed for RaspberryPiController

tap_revolution:
  defaults_file: config/tap_revolution.defaults.yaml
  persistent_file: config/tap_revolution.yaml

polling:
  assembler_poll_interval: 0.001
  scheduler_period: 0.1
  scheduler_active_period: 0.02
```

## Polling periods

`config.polling_periods(cfg)` reads the `polling:` section and returns a dict with
safe-clamped values. The clamps are enforced in code regardless of what the config says:

| Key | Default | Clamp |
|---|---|---|
| `assembler_poll_interval` | 0.001 s | [0.0005, 0.05] |
| `scheduler_period` | 0.1 s | [0.01, 5.0] |
| `scheduler_active_period` | 0.02 s | [0.005, scheduler_period] |

`scheduler_active_period` is additionally capped at `scheduler_period` so the
active (menu-open) poll is never slower than idle.

These are wired in `run_display.main()`:
```python
periods = config.polling_periods(cfg)
asmlr = assembler.Assembler(controller=ctrl, poll_interval=periods['assembler_poll_interval'])
schdlr = scheduler.CronScheduler(..., period=periods['scheduler_period'],
                                     active_period=periods['scheduler_active_period'])
```

## TapRevolutionConfig wiring

`run_display.make_tap_config(cfg)` builds the game config:

```python
def make_tap_config(cfg):
    section = cfg.get('tap_revolution') or {}
    defaults   = section.get('defaults_file')   or os.path.join(file_dir, 'config', 'tap_revolution.defaults.yaml')
    persistent = section.get('persistent_file') or os.path.join(file_dir, 'config', 'tap_revolution.yaml')
    return TapRevolutionConfig(defaults, persistent)
```

The fallback to project-relative paths ensures settings persist to disk even when
no `--config` is given. A `--config` that specifies its own `tap_revolution.persistent_file`
overrides the default.

## Example master config

See `config/nixie.example.yaml` for a fully documented template.

## Adding a new config-overridable arg

1. Add the arg to `make_parser()` in `run_display` with `default=None`.
2. Add a `config.resolve(args.new_arg, cfg, 'new_key', hardcoded_default)` call in
   `get_args()`.
3. Document it in `config/nixie.example.yaml`.

## Environment

- **Pi target:** Python 3.9.2 on Raspberry Pi Zero 2, Raspbian 11 (Bullseye).
  Use `BooleanOptionalAction`; avoid `match/case`, `X | Y` union hints (3.10+).
- **CronScheduler schedule** is currently hardcoded in `run_display`; the config
  loader returns a plain dict so a `schedule:` key will be a localized add.
