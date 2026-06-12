---
name: nixie-shell
description: Reference for NixieShell — the safe, read-only command-line menu item (pyxielib/nixie_shell.py). Covers the command parser, the default-deny safety model (allow/block/secret-path lists), CommandRunner, the PromptLine editor, the state machine, built-in special commands (find/less/history/replay), config, and the wait-screen re-poll gotcha. Use when reading, writing, or debugging pyxielib/nixie_shell.py, tests/test_nixie_shell.py, or the nixie_shell config section.
---

# NixieShell

A keyboard-driven command line on the 16-tube display (a `MenuItem` in the user
menu). Runs a **limited, mostly read-only** set of commands under a strict
**default-deny** allow list. It is *not* a bash facsimile: no globbing, no `~`,
no `$(...)`, no pipes/redirects/compound statements; never `shell=True`, always
an argv list.

## Module map

| File | Role |
|---|---|
| `pyxielib/nixie_shell.py` | Everything: parser, safety checks, runner, history, prompt editor, the `NixieShellItem` state machine. |
| `tests/test_nixie_shell.py` | unittest suite (parse, safety, secret-path, state machine, special commands, history, editor). Run: `python -m unittest tests.test_nixie_shell`. |
| `config/nixie.example.yaml` | Documents the `nixie_shell:` config section. |
| `pyxielib/menu_library.py` | `TextBodyItem` (reused for output/replay; gained an `unprintable_code` param). |
| `pyxielib/navigator.py` | `MenuItem.key_ctrl_a/e` no-ops; `Navigator.key_entry` routes `CTRL_A`/`CTRL_E`. |
| `pyxielib/key_watcher.py` | Both watchers emit `CTRL_A`/`CTRL_E` (terminal: `\x01`/`\x05`; evdev: `ctrled()` + a/e). |
| `pyxielib/usermenuprogram.py` | Registers `NixieShellItem(nixie_shell_config, size=...)` in the menu tree. |
| `run_display` | `make_nixie_shell_config(cfg)` → passed to `UserMenuProgram`. |

Wired like the other features: `run_display` builds a `NixieShellConfig` from the
master config and passes it through `UserMenuProgram.__init__`.

## Pipeline (per submitted line)

`PromptLine.text()` → record in `CommandHistory` → special-command intercepts
(`replay`/`history`/`find`/`less`/editors) → `parse_command` → `check_command`
→ `CommandRunner` (background thread) → output in a `TextBodyItem`.

### `parse_command(line, environ) -> (argv, expanded)`
Char scanner (`_Parser`), *not* shlex. Single quotes are literal; double-quoted
and unquoted text expand `$NAME`/`${NAME}`. Raises `ShellParseError` on an
unmatched quote or a `_METACHARS` char (`| < > ; & \``) or `$(`. Globs/`~` stay
literal. **An expanded value is inserted verbatim — never re-split or re-parsed**
(so `$VAR` can't inject extra args/options). `expanded` is only used to decide
logging, never to log the expanded text.

### `check_command(argv, config) -> Decision` (default-deny, in order)
1. `argv[0]` containing `/` → reject (a path would run that exact file under an
   allowed basename).
2. basename in `ALWAYS_BLOCKED` or config `block_list` → reject.
3. `find` + a `FIND_UNSAFE` primary → reject (defense-in-depth; `find` is also a
   special command, see below).
4. any arg hits the `PathDenyList` → reject (`protected path`).
5. matches an allow entry → **accept**; else reject (`not allowed`).

`Decision.level`: accept=INFO, reject=WARNING, `CRITICAL_CMDS`=CRITICAL.
Allow entries are **prefix** matches: a bare name (`ls`) allows any args; a
trailing `*` allows the remainder (`pactl list *`); a multi-word entry without
`*` requires that exact prefix. So `systemctl status *` permits `status` but not
`stop`.

### Security model (all in `nixie_shell.py`)
- **`ALWAYS_BLOCKED`** = dangerous commands + wrappers that can exec another
  command (`env`, `xargs`, `timeout`, `nohup`, `nice`, `setsid`, ...) +
  interpreters. `EDITORS` is unioned in. **`less` is intentionally absent** (it
  is special). These are hard-coded; config `block_list` only *adds*.
- **`ALWAYS_DENY_PATHS`** = `.gitignore`-style secret locations (`~/.ssh`,
  `~/.aws`, `~/.gnupg`, `*.pem`/`*.key`, `/etc/shadow`, `/proc/**`, `/sys/**`,
  `**/environ`, ...). Always enforced, **non-overridable** (config `path_deny_list`
  only adds). `PathDenyList.is_denied` checks every arg's `realpath` **and**
  `abspath`, plus each ancestor dir, so `..`/symlinks and `/etc`→`/private/etc`
  (macOS) can't bypass. See memory [[secrets-always-deny]].
- `CommandRunner`: `shell=False`, argv list, `stdin=DEVNULL` (no stdin hangs),
  stderr merged. Output read in **fixed-size chunks** with a byte cap (a
  newline-free stream like `cat /dev/zero` can't OOM) and a line cap; default
  30 s `timeout` (config, `null` disables); ESC→Cancel SIGINTs then SIGKILLs.
- **Logging**: the line is logged **as typed, never expanded** (an expanded env
  var could write a secret to `~/logs/nixie.log`).

## Built-in special commands (intercepted in `_run_prompt` before parsing)
- **`find <dir>`** (`_run_find`): one operand only. Directory → runs `find <dir>`;
  file → echoes its name; missing → flashes "Does not exist"; options/extra
  operands → rejected. Not on the allow list.
- **`less <file>`** (`_run_less`): one file operand; `cat`s it (the real pager
  would hang). Missing → "no such file"; secret paths still denied.
- **editors** (`vi`/`vim`/...): `_reject_editor` flashes a multi-screen hint.
- **`history`**: shows `CommandHistory` (newest-first, transient — `save=False`,
  doesn't clobber replayable output). **`replay`**: re-shows last output.

## State machine (`NixieShellItem`)
States: `prompt`, `running`, `cancel`, `exit`, `output`, plus a transient flash
overlay (single or queued via `_flash`/`_flash_seq`). `for_display()` returns a
`str` (wrapped by `MarqueeAnimation`) or an `Animation` (anything with raw
`{0x..}` glyphs — prompt, wait screen, output — must be an Animation; see memory
[[menu-text-rendering]]). ESC: prompt→EXIT?; running→Cancel?; output→new prompt;
triple-ESC within 1 s exits.

### Wait screen & the re-poll gotcha — IMPORTANT
The scheduler only re-polls the menu when the active animation reports `done()`
(or `should_interrupt`/a key). A `Looped` animation never finishes → it **freezes
the item's state machine** (counter stuck, completion unnoticed until a keypress).
So the wait screen returns **brief one-shot** frames (`_tick`, ~50 ms) — that
re-polls us each tick to advance the spinner/seconds and switch to output the
moment the command finishes. A 100 ms gate (`_RUNNING_GATE`) shows the command
line first, so a fast command never flashes a wait screen. See memory
[[menu-animation-repoll]].

### `PromptLine`
Tube 0 is a fixed `>` (backspace can't erase it). Insert mode, horizontal scroll
keeping the cursor visible; after a deletion the window pulls right to reveal
hidden left chars (no trailing blanks). Flashing `<`/`>` overflow markers on the
end cells (`_on_cell`). `0x157f` replaces non-printable chars. Up/Down recall
history; `key_home`/`key_end` (Ctrl+A/Ctrl+E) jump to line start/end.

## Config (`nixie_shell:` section, all optional)
`allow_list` (default `DEFAULT_ALLOW_LIST`), `block_list` (added to
`ALWAYS_BLOCKED`, fnmatch on basename), `path_deny_list` (added to
`ALWAYS_DENY_PATHS`), `max_output_bytes` (64 KiB), `max_output_lines` (2000),
`timeout` (30 s; `null` disables), `history_file` (cross-run history; omit =
in-memory for the process). Built via `NixieShellConfig.from_dict`.

## Gotchas
- Don't return a `Looped`/long animation while a command runs — it freezes the
  state machine (the original "stuck spinner" bug).
- `find`/`less` bypass `check_command`; they apply `PathDenyList` themselves —
  keep that when editing.
- Never log the expanded command; never let `block_list`/`path_deny_list` config
  *remove* a hard-coded protection.
- Allow/deny on `argv[0]` basename, but execution uses the full path → reject any
  `argv[0]` with a `/`.
