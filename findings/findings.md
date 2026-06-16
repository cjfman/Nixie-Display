# NixieShell Implementation Comparison: Opus vs Fable

**Branches:** `nixie-shell` (Opus 4) vs `nixie-shell-fable` (Fable 5)  
**Divergence point:** commit `a325b90` ("Logs menu: strip line preamble, contract log level")  
**Prompt:** Add a safe read-only command-line menu item

---

## At a glance

| Dimension | Opus (`nixie-shell`) | Fable (`nixie-shell-fable`) |
|---|---|---|
| Lines in `nixie_shell.py` | 1,080 | 860 |
| Test file | None | 198 lines |
| Skill doc | Yes | No |
| Config object | `NixieShellConfig` class | raw dict |
| History class | Separate `CommandHistory` | Integrated into `PromptLine` |
| Executor | `CommandRunner` | `ShellExecutor` |
| Parser output | `(argv, expanded)` tuple | `List[Token]` then `expand_tokens()` |
| Policy type | Module-level `check_command()` | `CommandPolicy` class |
| Wait-screen animation | Custom segment-rotation spinner | `ProgressSpinner` (reused) |
| Process group kill | No (`send_signal` to pid only) | Yes (`os.killpg`, `start_new_session`) |
| Pager env overrides | No | Yes (`PAGER=cat`, etc.) |
| Secret-path check in `less` | Yes | No (bug) |
| Ctrl+A/E wiring | Named special keys through Navigator | Raw control codes in `key_char` |
| `intro` state on activate | No (flash commented out) | Yes (1-second "Nixie Shell" splash) |

---

## Parsing

Both tokenizers honor single/double quotes and `$NAME`/`${NAME}` expansion, reject shell metacharacters outside quotes, and never re-split expanded values.

**Opus** uses a character-feeding state machine (`_Parser`) that builds one string token as it goes. It is a single class with three `_feed_*` methods dispatched by `getattr`. Variables are expanded inside `_flush_seg()`, which fires at every quote boundary. The result is a plain `(argv: List[str], expanded: bool)` tuple.

**Fable** uses an index-based scan (`_Tokenizer`) that builds a list of `Token` objects, where each `Token` holds `(text, context)` pairs — `'raw'`, `'single'`, or `'double'`. Expansion is deferred to a separate `expand_tokens()` call. This design is more principled: `Token.has_expansion()` can be queried before expansion (important for the policy check on `argv[0]`), and `Token.raw()` gives the unexpanded form for the policy's blocklist lookup (`sudo` in single quotes should still be blocked — and is).

Fable catches a subtlety Opus does not: it checks `tokens[0].has_expansion()` in `CommandPolicy.check()` and rejects an expanded command name (`$NIXIE_CMD /tmp → "bad command name"`). Opus only checks `'/' in argv[0]`, so an expanded command name like `$CMD` would be checked against the blocklist by its expanded value — which could be fine but is semantically murkier.

Both reject backtick and `$(` subcommands. Fable additionally rejects `(` and `)` as raw metacharacters; Opus does not, though they are uncommon in shell commands.

---

## Safety model

**Opus** keeps safety as module-level data and functions: `ALWAYS_BLOCKED` frozenset (more comprehensive, includes `fgrep`, `rgrep`, `strace`, additional script interpreters), `CRITICAL_CMDS`, `FIND_UNSAFE`, and `check_command(argv, config) -> Decision`. The `Decision` object carries `allowed`, `reason`, and `level` (logging level). Config lives in a `NixieShellConfig` class with a `from_dict()` factory; the config holds a `PathDenyList` instance (see below).

**Fable** packages safety as a `CommandPolicy` class with `check(tokens, argv) -> Optional[str]`. The class carries `ALWAYS_BLOCKED`, `CRITICAL_NAMES`, `ARG_DENY`, and `DEFAULT_ALLOW` as class attributes. `check()` returns `None` on success and a short reason string on rejection. The class approach is cleaner for testing (the test instantiates `CommandPolicy()` directly and calls `.check()`).

**`PathDenyList` (Opus only):** Opus implements a `PathDenyList` that converts `.gitignore`-style patterns (`**/.ssh/`, `*.pem`, `/etc/shadow`, `/proc/**`, etc.) into regexes and tests every command argument — including its `realpath` and all ancestor directories — against them. Any argument that resolves through a protected path is blocked. **Fable has no equivalent.** A user could do `cat /proc/1/environ` or `cat ~/.gnupg/secring.gpg` in Fable and it would run.

**`find` handling:**
- Opus intercepts `find` as a special command that accepts exactly one directory or file argument (no predicates, no options) and also checks the argument against `PathDenyList`.
- Fable handles `find` through the policy: `DEFAULT_ALLOW` includes `'find .'` as an exact two-token pattern, so only `find .` (no other paths, no predicates) is allowed. This is more restrictive than Opus (can't do `find /var/log`) but simpler. Fable additionally has `ARG_DENY['find']` that blocks `-exec`/`-delete` etc. even if a config were to allow `find . *`.

---

## Security issue: `less` missing secret-path check (Fable)

Fable's `_less` method:

```python
def _less(self, line, argv, expanded):
    if len(argv) != 2:
        self._reject(line, "less needs a file")
        return
    path = argv[1]
    if not os.path.exists(path):
        self._flash("no such file")
        return
    self.prompt.remember(line)
    self._execute(line, ['cat', path], expanded)
```

There is no path-deny check. Running `less ~/.ssh/id_rsa` or `less /etc/shadow` would successfully `cat` the file. Opus checks `self.config.path_deny.is_denied(target)` before catting. This is a meaningful security omission in Fable.

---

## Executor / subprocess handling

**Opus `CommandRunner`:**
- `Popen` without `start_new_session`; cancellation sends `SIGINT` to the process pid only — child processes may survive.
- No pager env overrides: if `git log` or `systemctl status` invokes `$PAGER`, that pager process would hang or misbehave.
- Output read in 65KB chunks via `os.read()` (avoids the newline-free stream / `cat /dev/zero` hang correctly noted in the comments).
- `threading.Event` signals completion; `_done.set()` in the reader thread.
- Kill escalation: `threading.Timer(2.0, _hard_kill)` sends SIGKILL to the pid if SIGINT didn't stop it within 2s.

**Fable `ShellExecutor`:**
- `Popen` with `start_new_session=True`; cancellation uses `os.killpg(proc.pid, sig)` — the entire process group is signaled. This correctly kills child processes (pagers, subcommands spawned by `systemctl`, etc.).
- Sets `PAGER=cat`, `SYSTEMD_PAGER=''`, `GIT_PAGER=cat` in the subprocess environment, preventing any pager from starting.
- Per-run `_Capture` objects: each `start()` call creates a fresh `_Capture`; the reader thread holds a reference to its own capture. If a previous run's reader thread is still draining a pipe after cancellation, it can never corrupt the current run's output buffer. This is an elegant solution to a real race condition.
- `poll()` method (instead of an event): returns `None` while running, the output lines once the process has exited.
- `finished_within(secs)` allows the caller to skip the wait screen entirely for fast commands.
- Kill escalation via a daemon thread calling `proc.wait(timeout=GRACE_SECS)` then `os.killpg(pid, SIGKILL)`.

**Assessment:** Fable's executor is more robust. The `start_new_session` + `killpg` design, pager overrides, and per-run `_Capture` isolation are all correct solutions to real failure modes.

---

## Prompt line and history

**Opus** uses a list-of-characters buffer (`self.buffer: List[str]`) and renders via `Frame`/`TextFrame`/`HexFrame` objects. Blink-on and blink-off frames are computed by `_build()` and cached until any edit. Includes a `static_frame()` method for use during the running-gate window. History lives in a separate `CommandHistory` class that appends each submitted line to the file rather than rewriting it.

**Fable** uses a plain string (`self.text`) and renders by calling `decodeChar()`/`underlineCode()` inline. History is integrated directly into `PromptLine` via `up()`/`down()`/`remember()`; `up()` stashes the in-progress draft when first pressed (bash-style), and `down()` restores it when scrolling back past the newest entry. On each submit the full history is rewritten to file (not appended). Fable's draft-stash behavior is more faithful to how bash history works; Opus tracks `hist_idx` in `NixieShellItem` and resets it after every Enter.

Both implement correct window scrolling to keep the cursor visible; both show blinking `<`/`>` edge markers when text runs off the sides.

Fable's test covers backspace-while-scrolled: deleting a character when the window is panned right should scroll the window back, not just decrement the cursor. That behavior is tested explicitly and the implementation matches.

---

## State machine

**Opus states:** `None` (inactive) → `prompt` → `running` → `cancel` → `output` → `exit`

**Fable states:** `intro` → `prompt` → `flash_error` → `running` → `cancel_confirm` → `output` → `exit_confirm`

Differences:
- Fable has an `intro` state (1-second "Nixie Shell" title splash on `activate()`). Opus had a similar `_flash()` call in `activate()` but it is commented out.
- Fable names confirmation states `cancel_confirm` / `exit_confirm` (clearer). Opus uses `cancel` / `exit`.
- Fable separates `flash_error` as an explicit state; Opus tracks `flash_msg`/`flash_until` as overlays on the current state, with a sequential flash queue (`flash_queue`). The queue allows chaining multiple flashes (used for the "No vim → only zuul → only less" editor hint sequence).
- **Backspace from output:** Fable's `key_backspace()` in the `output` state calls `_to_prompt(clear=True)`, returning to an empty prompt. Opus's `key_backspace()` in `output` does nothing; ESC is required to return to the prompt.

**Wait-screen re-poll (both correct):**
Both implementations use the one-shot animation trick to avoid freezing the state machine while a command runs:
- Opus wraps the current frame in a `FullFrameAnimation.makeTimed([frame], delay=_RUNNING_TICK)` — a one-shot (non-looped) animation that reports `done()` after 50ms, triggering a scheduler re-poll.
- Fable uses `ProgressSpinner` (which is itself a one-shot that resets and is recreated each second when the elapsed label changes).

---

## Ctrl+A / Ctrl+E wiring

**Opus** adds `CTRL_A` and `CTRL_E` as named special keys, parallel to `ENTER`/`ESC`/`BACKSPACE`. The Navigator routes them to `key_ctrl_a()` / `key_ctrl_e()` no-ops on `MenuItem`, overridden by `NixieShellItem`. Both the evdev `KeyWatcher` (via `ctrled()` + specific key check) and `TerminalKeyWatcher` (via `\x01`/`\x05` byte check) emit these named strings. Only those two combos are affected; no other Ctrl+letter is special.

**Fable** makes the evdev `KeyWatcher` map *all* `Ctrl+letter` combinations to their ASCII control codes (`\x01`–`\x1a`). `TerminalKeyWatcher` forwards `\x01`/`\x05` as literal bytes via `latin-1` decode. `NixieShellItem.key_char()` checks `c == '\x01'` and `c == '\x05'` inline. Other Ctrl+letter codes fall through to `key_char()` as non-printable characters in other menu items — harmless (they'd be ignored or displayed as replacement glyphs) but the evdev change is broader than needed.

Opus's approach is more targeted and doesn't alter the behavior of Ctrl+letter in any other menu item.

---

## Integration

Both branches wire the feature the same way conceptually: `run_display` builds a config from the YAML and passes it through `UserMenuProgram.__init__()` to `NixieShellItem`.

**Opus** goes through an intermediate `NixieShellConfig` object (`make_nixie_shell_config(cfg)` in `run_display`); the config is validated and defaulted at construction time. `usermenuprogram.py` receives a `NixieShellConfig` instance.

**Fable** passes the raw dict section directly (`cfg.get('nixie_shell')`); `NixieShellItem.__init__` does `config = config or {}` and reads keys with `.get()`. Less ceremony, but also less validation — a misspelled key silently falls back to the default.

**YAML config key names differ:**
- Opus: `allow_list`, `block_list`, `path_deny_list`, `max_output_bytes`, `max_output_lines`, `timeout`, `history_file`
- Fable: `allow`, `block`, `max_output_kb`, `history_file`, `history_size`

These are incompatible; choosing one means the example YAML must match.

**`TextBodyItem` parameter name differs:**
- Opus adds `unprintable_code=` parameter.
- Fable adds `replacement_code=` parameter.
These serve the same purpose (replacement glyph for characters the tubes can't render) but with different names.

---

## Tests

Fable includes `scripts/tests/test_nixie_shell.py` (198 lines), covering:
- Tokenizer (quoting, metachar rejection, glob/tilde/backslash literals)
- Env expansion (no word-split, curly form, single-quote immunity, empty-var handling, expansion flag)
- `CommandPolicy` (allow/block/deny patterns, `find` arg-deny, config overrides, `is_critical`)
- `PromptLine` (long-line scrolling, backspace-while-scrolled, home/end, history draft stash)
- `ShellExecutor` (echo output, DEVNULL stdin, nonzero exit code, runaway output truncation)

Opus has no test file in the branch.

---

## Minor differences

**Editor rejection:** Opus flashes a three-part message ("No vim", "only zuul", "only less") using a flash queue. Fable has no special editor hint; editors hit the blocklist and show "blocked".

**`replay` built-in:** Both have it. Opus's `_replay()` calls `_show_output(self.last_output)` and stays in the output state. Fable's also loads `viewer.set_lines` but additionally clears the prompt. Fable does not add `replay` to history (correct); Opus also does not.

**Output format for nonzero exit:** Fable appends `exit <N>` to the output lines. Opus does not report the exit code.

**`find` as built-in vs policy:** Opus's built-in `find` accepts any directory path (subject to PathDenyList), giving the user more flexibility. Fable's policy-level allow of `find .` is exact-only.

---

## Summary assessment

**Fable's strengths:**
- Cleaner `ShellExecutor` with `start_new_session`, `killpg`, pager overrides, and per-run `_Capture` isolation
- Structured `Token` class that enables policy checks on unexpanded command names
- Integrated history with draft-stash on `up()`
- Test suite covering the logic
- Shorter, more readable

**Opus's strengths:**
- `PathDenyList` with gitignore-style patterns protecting secret paths throughout (including in `less`)
- More comprehensive blocklist
- Named special keys for Ctrl+A/E (narrower impact on the rest of the menu)
- Sequential flash queue (enables the editor hint)
- Skill documentation

**The clearest defect is in Fable:** `less` does not check the path deny list, so `less ~/.ssh/id_rsa` would succeed. Fable also has no secret-path protection for `cat` arguments in general (no `PathDenyList` equivalent), meaning an allowlisted `cat ~/.netrc` would run.

**The clearest defect is in Opus:** `CommandRunner.cancel()` sends SIGINT to the process only, not the group; and no pager env overrides mean that `systemctl status` could open a pager that hangs.

A merged implementation would take Fable's executor model and Token-based parser, Opus's `PathDenyList` and secret-path checks in `less`/`find`, Opus's targeted Ctrl+A/E wiring, and Fable's test suite.
