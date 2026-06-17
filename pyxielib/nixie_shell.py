"""A small, mostly read-only command line for the nixie display.

NixieShell is *not* a bash facsimile. It runs a limited, configurable set of
commands under a strict default-deny allow list, never through a shell, always
with an argument list. Commands are parsed here (with quote handling and
``$NAME``/``${NAME}`` expansion only — no globbing, no ``~``, no subcommands, no
pipes/redirects/compound statements), checked against block/allow/secret-path
rules, then executed with ``subprocess.Popen`` off the menu thread.

The public menu entry point is :class:`NixieShellItem`.
"""

import fnmatch
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from typing import List, Optional, Tuple

from pyxielib.animation import Animation, FullFrameAnimation, LoopedFullFrameAnimation
from pyxielib.frames import Frame, FullFrame, HexFrame, TextFrame
from pyxielib.decoder import isPrintable
from pyxielib.menu_library import TextBodyItem
from pyxielib.navigator import MenuItem
from pyxielib.pyxieutil import PyxieError

logger = logging.getLogger(__name__)

DISPLAY_SIZE     = 16
PROMPT_GLYPH     = 0x1040    ## '>' marks tube 0 as a command prompt
UNDERLINE_CODE   = 0x4000    ## cursor underline bit
UNPRINTABLE_CODE = 0x157f    ## 'x' inside an 'o' — non-nixie-printable replacement

## Metacharacters that imply a pipe, redirect, compound statement or background
## job. Rejected outside quotes — this is not a shell.
_METACHARS = frozenset(('|', '<', '>', ';', '&', '`'))

## Commands that may never run, regardless of configuration. Beyond the user's
## list this adds command wrappers (env, xargs, timeout, ...) that can exec an
## otherwise-blocked command, plus more script interpreters.
##
## Interactive text editors. These get a custom "use less instead" hint rather
## than a bare rejection (see NixieShellItem._reject_editor); they are also kept
## in ALWAYS_BLOCKED so the real (interactive) binary can never run.
EDITORS = frozenset({
    'vi', 'vim', 'nvim', 'neovim', 'emacs', 'nano', 'pico', 'ed', 'joe', 'micro',
})

ALWAYS_BLOCKED = frozenset({
    'passwd', 'time', 'sleep', 'watch', 'cd', 'sudo', 'su', 'halt', 'reboot',
    'poweroff', 'shutdown', 'exit', 'cp', 'rm', 'mv', 'ln', 'mkdir', 'rmdir',
    'chmod', 'chown', 'chgrp', 'chroot', 'mount', 'umount', 'dd', 'mkfs',
    ## 'less' is intentionally absent — it is a built-in special command
    ## (NixieShellItem._run_less) that cats a file instead of paging it.
    'more', 'most', 'sed', 'awk', 'gawk', 'tee',
    'bash', 'sh', 'zsh', 'dash', 'csh', 'tcsh', 'ksh', 'fish',
    'source', 'export', 'eval', 'exec',
    'python', 'python2', 'python3', 'perl', 'ruby', 'node', 'nodejs', 'lua',
    'php', 'Rscript', 'irb', 'ghci', 'tclsh',
    'apt', 'apt-get', 'aptitude', 'dpkg', 'dpkg-reconfigure', 'snap',
    'pip', 'pip3', 'pipx', 'gem', 'npm', 'cargo',
    'grep', 'egrep', 'fgrep', 'rgrep', 'sort', 'git',
    'top', 'htop', 'gdb', 'strace', 'gcc', 'g++', 'cc', 'clang', 'make',
    ## command wrappers that can exec another (otherwise-blocked) command
    'env', 'xargs', 'nohup', 'timeout', 'nice', 'ionice', 'chrt', 'setsid',
    'stdbuf', 'script', 'flock', 'setpriv', 'unshare', 'doas',
}) | EDITORS

## Subset of dangerous commands logged at CRITICAL (all are also blocked).
CRITICAL_CMDS = frozenset({
    'sudo', 'su', 'cd', 'rm', 'halt', 'reboot', 'apt', 'apt-get', 'pip',
    'chmod', 'chown', 'chroot',
})

## ``find`` primaries that execute commands or write/delete files: reject these
## even though ``find .`` is otherwise allowed.
FIND_UNSAFE = frozenset({
    '-exec', '-execdir', '-ok', '-okdir', '-delete',
    '-fprint', '-fprintf', '-fprint0', '-fls',
})

## .gitignore-style paths that may hold secrets. Always denied, never
## overridable by configuration (config patterns are *added* to these).
ALWAYS_DENY_PATHS = (
    '**/.ssh/', '**/.ssh/**',
    '**/.gnupg/**', '**/.aws/**', '**/.config/gcloud/**', '**/.azure/**',
    '**/.netrc', '**/.git-credentials', '**/.npmrc', '**/.pypirc',
    '**/.docker/config.json', '**/.kube/config', '**/.env',
    '**/id_rsa*', '**/id_dsa*', '**/id_ecdsa*', '**/id_ed25519*',
    '**/*.pem', '**/*.key', '**/*.p12', '**/*.pfx',
    '/etc/shadow', '/etc/gshadow', '/etc/sudoers', '/etc/sudoers.d/**',
    '/etc/ssl/private/**',
    ## /proc and /sys expose process environments/memory and kernel state
    ## (e.g. /proc/<pid>/environ leaks another process's secrets).
    '/proc/**', '/sys/**', '**/environ',
)

## The user's requested allow list; overridable via the master config. ``find``
## is NOT here — it is a built-in special command (see NixieShellItem._run_find).
DEFAULT_ALLOW_LIST = [
    'ls', 'cat', 'echo', 'wc', 'host', 'dig', 'du',
    'pactl info', 'pactl list *',
    'systemctl status *', 'systemctl --user status *',
]


class ShellError(PyxieError):
    pass


class ShellParseError(ShellError):
    """The command line could not be parsed (bad quote / disallowed syntax)."""


# --------------------------------------------------------------------------- #
# Command parsing
# --------------------------------------------------------------------------- #

_ENV_RE = re.compile(r'\$(?:\{(\w+)\}|(\w+))')


def _expand_env(text, environ):
    """Expand $NAME and ${NAME} from ``environ``; undefined names become ''."""
    def repl(match):
        name = match.group(1) or match.group(2)
        return environ.get(name, '')
    return _ENV_RE.sub(repl, text)


class _Parser:
    """Incremental tokenizer for :func:`parse_command` (one per line).

    Builds each argument in ``token`` (finalized text) plus ``seg`` (pending
    expandable text). ``seg`` is flushed — expanded and appended — at every quote
    boundary so a variable name cannot run past a quote, and once at token end.
    Single-quoted runs append literally; double-quoted and unquoted runs expand.
    """
    def __init__(self, environ):
        self.environ  = environ
        self.mode     = 'normal'    ## 'normal' | 'single' | 'double'
        self.argv     = []
        self.token    = ''
        self.seg      = ''
        self.started  = False       ## a token is in progress (tracks "")
        self.expanded = False

    def feed(self, c):
        getattr(self, '_feed_' + self.mode)(c)

    def _feed_normal(self, c):
        if c in ' \t':
            self._end_token()
        elif c == "'":
            self._flush_seg(); self.mode = 'single'; self.started = True
        elif c == '"':
            self._flush_seg(); self.mode = 'double'; self.started = True
        elif c in _METACHARS:
            raise ShellParseError("not allowed")
        else:
            self.seg += c; self.started = True

    def _feed_single(self, c):
        if c == "'":
            self.mode = 'normal'
        else:
            self.token += c

    def _feed_double(self, c):
        if c == '"':
            self._flush_seg(); self.mode = 'normal'
        elif c == '`':
            raise ShellParseError("not allowed")
        else:
            self.seg += c

    def _flush_seg(self):
        if not self.seg:
            return
        if '$(' in self.seg:                 ## subcommands are not allowed
            raise ShellParseError("not allowed")
        expanded = _expand_env(self.seg, self.environ)
        if expanded != self.seg:
            if not self.argv:        ## $VAR expansion is never allowed in the command name
                raise ShellParseError("not allowed")
            self.expanded = True
        self.token += expanded
        self.seg = ''

    def _end_token(self):
        if not self.started:
            return
        self._flush_seg()
        self.argv.append(self.token)
        self.token = ''
        self.started = False

    def finish(self) -> Tuple[List[str], bool]:
        if self.mode in ('single', 'double'):
            raise ShellParseError("unmatched quote")
        self._end_token()
        return self.argv, self.expanded


def parse_command(line, environ=None) -> Tuple[List[str], bool]:
    """Parse a command line into ``(argv, expanded)``.

    Honors single/double quotes and ``$NAME``/``${NAME}`` expansion (in
    double-quoted and unquoted text only). Raises :class:`ShellParseError` on an
    unmatched quote, a pipe/redirect/compound/subcommand metacharacter, or a
    ``$VAR`` substitution in ``argv[0]`` (command names must be literal). Globs,
    ``~`` and ``$(...)`` are never expanded; an expanded value is inserted
    verbatim and never re-split or re-parsed. ``expanded`` is True when any
    environment variable was substituted (so the caller can log both forms).
    """
    if environ is None:
        environ = os.environ
    parser = _Parser(environ)
    for c in line:
        parser.feed(c)
    return parser.finish()


# --------------------------------------------------------------------------- #
# Secret-path protection
# --------------------------------------------------------------------------- #

def _gitignore_to_regex(pattern) -> str:
    """Translate a .gitignore-ish glob into a full-match regex string.

    ``*`` matches within a path segment, ``**`` across segments, ``?`` a single
    non-slash char. A leading ``/`` anchors to the filesystem root; otherwise the
    pattern may match at any depth.
    """
    anchored = pattern.startswith('/')
    body = pattern.strip('/')
    out = ['^/'] if anchored else ['^']
    if not anchored and not body.startswith('**'):
        out.append('(.*/)?')
    i = 0
    while i < len(body):
        if body[i] == '*' and body[i + 1:i + 2] == '*':
            out.append('.*')
            i += 2
        elif body[i] == '*':
            out.append('[^/]*')
            i += 1
        elif body[i] == '?':
            out.append('[^/]')
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    out.append('$')
    return ''.join(out)


def _ancestors(path) -> List[str]:
    """A path plus each of its ancestor directories, up to the root."""
    seen = []
    while True:
        seen.append(path)
        parent = os.path.dirname(path)
        if parent == path:
            return seen
        path = parent


class PathDenyList:
    """Reject command arguments that resolve to secret-bearing paths.

    Each argument is resolved with ``os.path.realpath`` (collapsing ``..`` and
    symlinks) and tested — together with each ancestor directory — against the
    always-on :data:`ALWAYS_DENY_PATHS` plus any configured extras, so a relative
    path or a symlink into a protected directory cannot slip through.
    """
    def __init__(self, extra=None):
        patterns = list(ALWAYS_DENY_PATHS) + list(extra or [])
        self._regexes = [re.compile(_gitignore_to_regex(p)) for p in patterns]

    def is_denied(self, arg) -> bool:
        ## Check both the symlink-resolved path (catches a symlink into a
        ## protected dir) and the plain absolute path (so /etc/shadow still
        ## matches on systems where /etc itself is a symlink, e.g. macOS dev).
        candidates = {os.path.abspath(arg), os.path.realpath(arg)}
        return any(rx.match(path)
                   for base in candidates
                   for path in _ancestors(base)
                   for rx in self._regexes)

    def first_denied(self, argv) -> Optional[str]:
        """Return the first argument (after argv[0]) that hits the deny list."""
        for arg in argv[1:]:
            if self.is_denied(arg):
                return arg
        return None


# --------------------------------------------------------------------------- #
# Safety checks
# --------------------------------------------------------------------------- #

class Decision:
    """The outcome of :func:`check_command`."""
    def __init__(self, allowed, reason, level):
        self.allowed = allowed
        self.reason  = reason
        self.level   = level     ## logging level to record this command at

    def __repr__(self):
        return "<Decision allowed=%s reason=%r>" % (self.allowed, self.reason)


def _matches_block(cmd, block_list) -> bool:
    return any(fnmatch.fnmatch(cmd, pat) for pat in block_list)


def _find_is_unsafe(argv) -> bool:
    """True when a ``find`` call uses a command-executing/file-writing primary."""
    return any(arg in FIND_UNSAFE for arg in argv[1:])


def _match_entry(tokens, cmd, argv) -> bool:
    """An allow entry matches when its tokens are a prefix of argv.

    The first token is compared to the command basename; a trailing ``*`` token
    matches all remaining arguments. A bare single-token entry allows any args.
    """
    if not tokens or cmd != tokens[0]:
        return False
    for i, tok in enumerate(tokens[1:], start=1):
        if tok == '*':
            return True
        if i >= len(argv) or argv[i] != tok:
            return False
    return True


def _matches_allow(argv, allow_list) -> bool:
    cmd = os.path.basename(argv[0])
    return any(_match_entry(entry.split(), cmd, argv) for entry in allow_list)


def check_command(argv, config) -> Decision:
    """Decide whether ``argv`` may run. Default-deny; block/deny beat allow."""
    cmd = os.path.basename(argv[0])
    base = logging.CRITICAL if cmd in CRITICAL_CMDS else None

    ## A path in argv[0] (e.g. './cat', '/tmp/ls') would execute that exact file
    ## while matching an allowed *basename*. Require a bare name resolved on PATH.
    ## (Expanded command names are already rejected by parse_command.)
    if '/' in argv[0]:
        return Decision(False, "not allowed", base or logging.WARNING)
    if cmd in ALWAYS_BLOCKED or _matches_block(cmd, config.block_list):
        return Decision(False, "blocked", base or logging.WARNING)
    if cmd == 'find' and _find_is_unsafe(argv):
        return Decision(False, "blocked", base or logging.WARNING)
    if config.path_deny.first_denied(argv) is not None:
        return Decision(False, "protected path", base or logging.WARNING)
    if _matches_allow(argv, config.allow_list):
        return Decision(True, "allowed", base or logging.INFO)
    return Decision(False, "not allowed", base or logging.WARNING)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

class NixieShellConfig:
    """Allow/block lists and limits, built from the master config's section."""
    def __init__(self, allow_list=None, block_list=None, path_deny_list=None,
                 max_output_bytes=65536, max_output_lines=2000, timeout=30,
                 history_file=None):
        self.allow_list = list(allow_list) if allow_list is not None else list(DEFAULT_ALLOW_LIST)
        self.block_list = list(block_list or [])
        self.max_output_bytes = max_output_bytes
        self.max_output_lines = max_output_lines
        self.timeout = timeout
        self.history_file = history_file
        self.path_deny = PathDenyList(path_deny_list)

    @classmethod
    def from_dict(cls, section) -> 'NixieShellConfig':
        """Build from a master-config ``nixie_shell`` mapping (or None)."""
        section = section or {}
        return cls(
            allow_list=section.get('allow_list'),
            block_list=section.get('block_list'),
            path_deny_list=section.get('path_deny_list'),
            max_output_bytes=section.get('max_output_bytes', 65536),
            max_output_lines=section.get('max_output_lines', 2000),
            timeout=section.get('timeout', 30),
            history_file=section.get('history_file'),
        )


# --------------------------------------------------------------------------- #
# Command history
# --------------------------------------------------------------------------- #

class CommandHistory:
    """Command history, optionally persisted to a file.

    When ``path`` is set, entries are appended to it (and reloaded on start) so
    history survives restarts; otherwise it lives only for the running process.
    Stores the line *as typed* — never an expanded form, which could leak a
    secret to disk. Consecutive duplicates are collapsed, bash-style.
    """
    def __init__(self, path=None, *, limit=500):
        self.path = os.path.expanduser(path) if path else None
        self.limit = limit
        self.entries = self._load()

    def _load(self) -> List[str]:
        if not self.path:
            return []
        try:
            with open(self.path) as f:
                return [ln.rstrip('\n') for ln in f if ln.strip()][-self.limit:]
        except OSError:
            return []

    def add(self, line):
        if self.entries and self.entries[-1] == line:
            return
        self.entries.append(line)
        if len(self.entries) > self.limit:
            self.entries = self.entries[-self.limit:]
        self._append_file(line)

    def _append_file(self, line):
        if not self.path:
            return
        try:
            with open(self.path, 'a') as f:
                f.write(line + '\n')
        except OSError as e:
            logger.warning("nixie-shell: cannot write history file %s: %s", self.path, e)

    def list(self) -> List[str]:
        return list(self.entries)


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

## Pager overrides injected into every subprocess environment so that commands
## like ``git log`` or ``systemctl status`` never spawn an interactive pager.
_PAGER_ENV = {'PAGER': 'cat', 'SYSTEMD_PAGER': '', 'GIT_PAGER': 'cat'}


class CommandRunner:
    """Run an argv in a subprocess off the menu thread, capturing capped output.

    ``stdin`` is ``DEVNULL`` so commands that would read it (e.g. ``cat`` with no
    file) get EOF instead of hanging; stderr is merged into stdout. The process
    runs in its own session so SIGINT/SIGKILL land on the whole process group,
    not just the top-level pid. Pager env vars are overridden so that ``git`` or
    ``systemctl`` cannot spawn an interactive pager. Output is capped at
    ``max_bytes``/``max_lines`` and an optional ``timeout`` SIGINTs the group;
    both stop a runaway producer.
    """
    def __init__(self, argv, *, max_bytes=65536, max_lines=2000, timeout=None):
        self.argv = argv
        self.max_bytes = max_bytes
        self.max_lines = max_lines
        self.timeout = timeout
        self.proc = None
        self.start = time.time()
        self._output = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def run(self) -> 'CommandRunner':
        self._thread.start()
        return self

    def is_running(self) -> bool:
        return not self._done.is_set()

    def elapsed(self) -> float:
        return time.time() - self.start

    def output_lines(self) -> List[str]:
        with self._lock:
            return list(self._output)

    def cancel(self):
        """Stop the command: SIGINT the process group now, SIGKILL after a short grace."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            self._done.set()
            return
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except OSError:
            return
        threading.Timer(2.0, self._hard_kill).start()

    def _hard_kill(self):
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass

    def _run(self):
        try:
            self.proc = subprocess.Popen(
                self.argv, shell=False, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True,
                env=dict(os.environ, **_PAGER_ENV),
            )
        except OSError as e:
            self._set_output(["error: %s" % e])
            self._done.set()
            return
        timer = None
        if self.timeout:
            timer = threading.Timer(self.timeout, self.cancel)
            timer.start()
        try:
            self._read_output()
        finally:
            if timer is not None:
                timer.cancel()
        self.proc.wait()
        self.proc.stdout.close()
        self._done.set()

    def _read_output(self):
        ## Read fixed-size chunks (not lines): a newline-free stream such as
        ## ``cat /dev/zero`` would otherwise buffer one unbounded "line" and the
        ## byte cap — checked per chunk here — would never fire.
        fd = self.proc.stdout.fileno()
        chunks = []
        total = 0
        truncated = False
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > self.max_bytes:
                truncated = True
                self.cancel()
                break
        self._set_output(self._format(b''.join(chunks), truncated))

    def _format(self, data, truncated) -> List[str]:
        """Decode captured bytes into display lines, applying the line cap."""
        lines = data.decode('utf-8', errors='replace').split('\n')
        if lines and lines[-1] == '':
            lines.pop()                 ## drop the empty field from a trailing '\n'
        if len(lines) > self.max_lines:
            lines = lines[:self.max_lines]
            truncated = True
        if truncated:
            lines.append("...(truncated)")
        return lines or ["(no output)"]

    def _set_output(self, lines):
        with self._lock:
            self._output = lines


# --------------------------------------------------------------------------- #
# Prompt line editor
# --------------------------------------------------------------------------- #

def _char_frame(c) -> Frame:
    """A frame for one character; non-nixie-printable chars use the replacement."""
    if isPrintable(c):
        return TextFrame(c)
    return HexFrame(UNPRINTABLE_CODE)


def _cursor_frame(c) -> Frame:
    """Like :func:`_char_frame` but with the cursor underline OR'd in."""
    if isPrintable(c):
        return TextFrame(c, underline=True)
    return HexFrame(UNPRINTABLE_CODE | UNDERLINE_CODE)


class PromptLine:
    """An editable single-line command buffer with a blinking cursor.

    Tube 0 always shows the '>' prompt and is not part of the buffer; the
    remaining tubes show a horizontally-scrolled window that keeps the cursor
    visible (insert mode). The blink is a cached two-frame loop, rebuilt only
    when the buffer, cursor or window changes (same contract as TextBodyItem).
    """
    def __init__(self, size=DISPLAY_SIZE):
        self.size = size
        self.width = size - 1       ## tubes available after the '>' prompt
        self.buffer = []
        self.cursor = 0
        self.window = 0
        self._animation = None

    def text(self) -> str:
        return ''.join(self.buffer)

    def set_text(self, text):
        self.buffer = list(text)
        self.cursor = len(self.buffer)
        self.window = 0
        self._after_edit()

    def clear(self):
        self.set_text('')

    def key_char(self, c):
        self.buffer.insert(self.cursor, c)
        self.cursor += 1
        self._after_edit()

    def key_backspace(self):
        if self.cursor > 0:
            del self.buffer[self.cursor - 1]
            self.cursor -= 1
            self._after_edit()

    def key_left(self):
        if self.cursor > 0:
            self.cursor -= 1
            self._after_edit()

    def key_right(self):
        if self.cursor < len(self.buffer):
            self.cursor += 1
            self._after_edit()

    def key_home(self):
        if self.cursor != 0:
            self.cursor = 0
            self._after_edit()

    def key_end(self):
        if self.cursor != len(self.buffer):
            self.cursor = len(self.buffer)
            self._after_edit()

    def render(self) -> Animation:
        if self._animation is None:
            self._animation = self._build()
        return self._animation

    def _after_edit(self):
        ## keep the cursor visible
        if self.cursor < self.window:
            self.window = self.cursor
        elif self.cursor >= self.window + self.width:
            self.window = self.cursor - self.width + 1
        ## after a deletion the line is shorter: pull the window right so hidden
        ## left-hand characters scroll back into view instead of leaving the
        ## right side blank (the window is at most one tube past the last char)
        max_fill = max(0, len(self.buffer) - self.width + 1)
        self.window = max(0, min(self.window, max_fill))
        self._animation = None

    def _window_chars(self) -> List[str]:
        chars = self.buffer[self.window:self.window + self.width]
        chars += [' '] * (self.width - len(chars))
        return chars

    def static_frame(self) -> FullFrame:
        """A single non-blinking frame of the current view (used by the run gate)."""
        return FullFrame([HexFrame(PROMPT_GLYPH)] + [_char_frame(c) for c in self._window_chars()])

    def _build(self) -> Animation:
        cells = self._window_chars()
        cpos = self.cursor - self.window
        has_left = self.window > 0                              ## chars hidden left
        has_right = self.window + self.width < len(self.buffer)  ## chars hidden right
        last = len(cells) - 1
        off_frames = [HexFrame(PROMPT_GLYPH)]
        on_frames = [HexFrame(PROMPT_GLYPH)]
        for i, c in enumerate(cells):
            off_frames.append(_char_frame(c))
            on_frames.append(self._on_cell(c, i, cpos, has_left, has_right, last))
        frames = [FullFrame(on_frames), FullFrame(off_frames)]
        return LoopedFullFrameAnimation.makeTimed(frames, delay=0.4)

    @staticmethod
    def _on_cell(c, i, cpos, has_left, has_right, last) -> Frame:
        """The blink-on frame for cell ``i``: flashing edge markers ('<' left,
        '>' right) take the end cells, otherwise the cursor underline at ``cpos``."""
        if has_left and i == 0:
            return TextFrame('<', underline=(i == cpos))
        if has_right and i == last:
            return TextFrame('>', underline=(i == cpos))
        if i == cpos:
            return _cursor_frame(c)
        return _char_frame(c)


# --------------------------------------------------------------------------- #
# The menu item
# --------------------------------------------------------------------------- #

class NixieShellItem(MenuItem):
    """Interactive command line, driven as a state machine.

    States: ``prompt`` (editing), ``running`` (wait spinner), ``cancel`` and
    ``exit`` (Y/N confirmations), ``output`` (scrollable result viewer). A
    transient flash overlays a message for a fixed duration before the current
    state's display resumes.
    """
    _FLASH_START_SECS = 1.0
    _FLASH_MSG_SECS   = 1.0
    _ESC_TRIPLE_SECS  = 1.0
    _RUNNING_GATE     = 0.1     ## don't show the wait screen until a command runs this long
    _RUNNING_TICK     = 0.05    ## wait-frame lifetime; bounds completion-detection latency
    _SPIN_PERIOD      = 0.1     ## advance the wait spinner one segment this often
    ## A single ring segment walked around one tube to animate the wait spinner.
    _SPIN_SEGS = (0x0080, 0x0100, 0x0200, 0x0400, 0x0800, 0x1000, 0x2000, 0x0040)

    def __init__(self, config=None, *, size=DISPLAY_SIZE, **kwargs):
        super().__init__("Nixie Shell", **kwargs)
        self.config = config or NixieShellConfig()
        self.size = size
        self.prompt = PromptLine(size)
        self.output = TextBodyItem("Output", size=size, unprintable_code=UNPRINTABLE_CODE)
        self.history = CommandHistory(self.config.history_file)
        self.hist_idx = len(self.history.entries)
        self.state = None
        self.runner = None
        self.last_output = None
        self.flash_msg = None
        self.flash_until = 0.0
        self.flash_queue = []
        self.esc_times = deque()

    # -- lifecycle ---------------------------------------------------------- #

    def activate(self):
        self._clear_transient()
        self.last_output = None
        self.hist_idx = len(self.history.entries)
        #self._flash("Nixie Shell", self._FLASH_START_SECS)
        self.state = 'prompt'

    def reset(self):
        super().reset()
        self._clear_transient()
        self.last_output = None
        self.state = None

    def _clear_transient(self):
        if self.runner is not None:
            self.runner.cancel()
            self.runner = None
        self.prompt.clear()
        self.output.reset()
        self.flash_msg = None
        self.flash_until = 0.0
        self.flash_queue = []
        self.esc_times.clear()

    def _flash(self, msg, secs):
        self.flash_msg = msg
        self.flash_until = time.time() + secs

    def _flash_seq(self, *pairs):
        """Show a sequence of (msg, secs) flashes, one after another."""
        self.flash_queue = list(pairs[1:])
        self._flash(*pairs[0])

    # -- display ------------------------------------------------------------ #

    def for_display(self):
        if self.state is None:
            return "Nixie Shell"
        if self.flash_msg is not None:
            if time.time() < self.flash_until:
                return self.flash_msg
            self.flash_msg = None
            if self.flash_queue:                  ## advance to the next queued flash
                self._flash(*self.flash_queue.pop(0))
                return self.flash_msg
        return getattr(self, '_display_' + self.state)()

    def _display_prompt(self):
        return self.prompt.render()

    def _display_running(self):
        if self.runner is None or not self.runner.is_running():
            return self._finish_running()
        elapsed = self.runner.elapsed()
        if elapsed < self._RUNNING_GATE:
            ## Within the gate: keep showing the command line (so a sub-100ms
            ## command never flashes a wait screen) but as a brief one-shot so
            ## the scheduler keeps re-polling us to catch it finishing.
            return self._tick(self.prompt.static_frame())
        return self._tick(self._running_frame(elapsed))

    def _running_frame(self, elapsed) -> FullFrame:
        """The wait screen: 'Running for N s' plus a one-tube rotating spinner."""
        label = ("Running for %d s" % int(elapsed))[:self.size - 1]
        cells = [_char_frame(c) for c in label]
        cells += [HexFrame(0)] * max(0, self.size - 1 - len(cells))
        phase = int(elapsed / self._SPIN_PERIOD) % len(self._SPIN_SEGS)
        cells.append(HexFrame(self._SPIN_SEGS[phase]))
        return FullFrame(cells)

    def _tick(self, full_frame) -> Animation:
        """Wrap a frame in a brief *one-shot* animation.

        One-shot (not Looped) so it reports done() after _RUNNING_TICK; the
        scheduler only re-polls the menu when the active animation finishes, so
        this is what advances the spinner/seconds and lets us switch to the
        output the moment the command completes. A Looped animation would freeze
        the state machine until a keypress.
        """
        return FullFrameAnimation.makeTimed([full_frame], delay=self._RUNNING_TICK)

    def _display_output(self):
        return self.output.for_display()

    def _display_cancel(self):
        return "Cancel? Y/N"

    def _display_exit(self):
        return "EXIT? Y/N"

    def _finish_running(self):
        if self.runner is not None:
            self.last_output = self.runner.output_lines()
            self.runner = None
        self._show_output(self.last_output or ["(no output)"])
        return self.output.for_display()

    def _show_output(self, lines, *, save=True):
        """Load ``lines`` into the viewer and switch to the output state.

        ``save`` records the lines as the replayable last output; a transient
        view such as ``history`` passes save=False so it does not clobber the
        last command's output.
        """
        if save:
            self.last_output = lines
        self.output.set_lines(lines)
        self.state = 'output'

    # -- key handling ------------------------------------------------------- #

    def key_char(self, c):
        if self.state == 'prompt':
            self.prompt.key_char(c)
        elif self.state == 'cancel':
            self._answer_cancel(c)
        elif self.state == 'exit':
            self._answer_exit(c)

    def key_backspace(self):
        if self.state == 'prompt':
            self.prompt.key_backspace()

    def key_left(self):
        if self.state == 'prompt':
            self.prompt.key_left()
        elif self.state == 'output':
            self.output.key_left()

    def key_right(self):
        if self.state == 'prompt':
            self.prompt.key_right()
        elif self.state == 'output':
            self.output.key_right()

    def key_ctrl_a(self):
        if self.state == 'prompt':
            self.prompt.key_home()      ## bash: move to start of line

    def key_ctrl_e(self):
        if self.state == 'prompt':
            self.prompt.key_end()       ## bash: move to end of line

    def key_up(self):
        if self.state == 'prompt':
            self._history_prev()
        elif self.state == 'output':
            self.output.key_up()

    def key_down(self):
        if self.state == 'prompt':
            self._history_next()
        elif self.state == 'output':
            self.output.key_down()

    def key_enter(self):
        if self.state == 'prompt':
            self._run_prompt()
        elif self.state == 'output':
            self.output.key_enter()

    def key_esc(self):
        if self.state in ('prompt', 'exit'):
            self._esc_prompt()
        elif self.state == 'running':
            self.state = 'cancel'
        elif self.state == 'cancel':
            self.state = 'running'
        elif self.state == 'output':
            self._new_prompt()

    # -- state transitions -------------------------------------------------- #

    def _esc_prompt(self):
        now = time.time()
        self.esc_times.append(now)
        while self.esc_times and now - self.esc_times[0] > self._ESC_TRIPLE_SECS:
            self.esc_times.popleft()
        if len(self.esc_times) >= 3:    ## three ESCs within a second == "Yes"
            self.set_done()
            return
        self.state = 'exit'

    def _answer_exit(self, c):
        if c in 'yY':
            self.set_done()
        elif c in 'nN':
            self.esc_times.clear()
            self.state = 'prompt'       ## typed text is preserved

    def _answer_cancel(self, c):
        if c in 'yY':
            if self.runner is not None:
                self.runner.cancel()
                self.runner = None
            self.prompt.clear()
            self.hist_idx = len(self.history.entries)
            self.state = 'prompt'
        elif c in 'nN':
            self.state = 'running'

    def _new_prompt(self):
        self.prompt.clear()
        self.output.reset()
        self.hist_idx = len(self.history.entries)
        self.state = 'prompt'

    def _run_prompt(self):
        line = self.prompt.text().strip()
        if not line:
            return
        self.history.add(line)                ## record as typed (never expanded)
        self.hist_idx = len(self.history.entries)
        if line == 'replay':
            self._replay()
            return
        if line == 'history':
            self._show_history()
            return
        try:
            argv, _ = parse_command(line)         ## expanded value is intentionally
        except ShellParseError as e:              ## never logged (it may be a secret)
            self._flash(e.what(), self._FLASH_MSG_SECS)   ## keep text for editing
            return
        if not argv:
            return
        if argv[0] == 'find':                 ## built-in special commands
            self._run_find(line, argv)
        elif argv[0] == 'less':
            self._run_less(line, argv)
        elif argv[0] in EDITORS:
            self._reject_editor(line, argv[0])
        else:
            self._dispatch(line, argv)

    def _replay(self):
        if not self.last_output:
            self._flash("no output", self._FLASH_MSG_SECS)
            return
        self._show_output(self.last_output)

    def _show_history(self):
        entries = self.history.list()
        if not entries:
            self._flash("no history", self._FLASH_MSG_SECS)
            return
        self._show_output(entries[::-1], save=False)   ## newest first, transient

    def _history_prev(self):
        """Up arrow: recall the previous command (bash-style)."""
        if self.hist_idx > 0:
            self.hist_idx -= 1
            self.prompt.set_text(self.history.entries[self.hist_idx])

    def _history_next(self):
        """Down arrow: move toward the newest command, then the empty line."""
        if self.hist_idx >= len(self.history.entries) - 1:
            self.hist_idx = len(self.history.entries)
            self.prompt.set_text('')
        else:
            self.hist_idx += 1
            self.prompt.set_text(self.history.entries[self.hist_idx])

    def _dispatch(self, line, argv):
        decision = check_command(argv, self.config)
        if not decision.allowed:
            self._reject(line, decision.reason, decision.level)
            return
        self._log(line, decision.reason, decision.level)
        self._start_runner(argv)

    def _run_find(self, line, argv):
        """``find`` takes a single directory operand and lists it.

        A file operand just echoes its name; a missing operand flashes; anything
        else (no/extra operands, options) is rejected. This keeps ``find`` from
        running arbitrary predicates (-exec/-delete) or extra start paths.
        """
        args = argv[1:]
        if len(args) != 1 or args[0].startswith('-'):
            self._reject(line, "not allowed", logging.WARNING)
            return
        target = args[0]
        if self.config.path_deny.is_denied(target):
            self._reject(line, "protected path", logging.WARNING)
            return
        if os.path.isdir(target):
            self._log(line, "allowed", logging.INFO)
            self._start_runner(['find', target])
        elif os.path.exists(target):
            self._log(line, "allowed", logging.INFO)
            self._show_output([target])
        else:
            self._log(line, "does not exist", logging.INFO)
            self._flash("Does not exist", self._FLASH_MSG_SECS)

    def _run_less(self, line, argv):
        """``less`` takes a single file path and ``cat``s it (no real pager).

        The real ``less`` is interactive and would hang, so this views the file
        through ``cat`` instead. A missing file flashes; options/extra operands
        are rejected; secret paths are still denied.
        """
        args = argv[1:]
        if len(args) != 1 or args[0].startswith('-'):
            self._reject(line, "not allowed", logging.WARNING)
            return
        target = args[0]
        if self.config.path_deny.is_denied(target):
            self._reject(line, "protected path", logging.WARNING)
            return
        if os.path.exists(target):
            self._log(line, "allowed", logging.INFO)
            self._start_runner(['cat', target])
        else:
            self._log(line, "no such file", logging.INFO)
            self._flash("no such file", self._FLASH_MSG_SECS)

    def _reject_editor(self, line, cmd):
        """Point text editors at the ``less`` viewer instead of running them.

        'No <cmd>, only less' is too wide for the display, so flash it in two
        parts (à la "There is no Dana, only Zuul")."""
        self._log(line, "blocked", logging.WARNING)
        self._flash_seq(
            ("No %s" % cmd, 1.5),
            ("only zuul", 0.25),
            ("only less", 1.5),
        )

    def _start_runner(self, argv):
        self.runner = CommandRunner(
            argv,
            max_bytes=self.config.max_output_bytes,
            max_lines=self.config.max_output_lines,
            timeout=self.config.timeout,
        ).run()
        self.state = 'running'

    def _reject(self, line, reason, level):
        self._log(line, reason, level)
        self._flash(reason, self._FLASH_MSG_SECS)   ## keep typed text for editing

    @staticmethod
    def _log(line, reason, level):
        ## Log the command exactly as typed — never the expanded form, which
        ## could write an environment-variable secret to the log file.
        logger.log(level, "nixie-shell %s: %r" % (reason, line))
