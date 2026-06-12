"""
Tests for the Nixie Shell: command parsing, the safety check (allow/block/
secret-path rules) and the interactive state machine.

Run directly:      python tests/test_nixie_shell.py
Or via unittest:   python -m unittest discover tests
"""
##pylint: disable=wrong-import-position

import logging
import os
import sys
import tempfile
import time
import unittest

## Make the repo root importable when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyxielib.animation import Animation
from pyxielib.nixie_shell import (
    NixieShellConfig, NixieShellItem, ShellParseError, check_command, parse_command,
)


ENV = {'FOO': 'bar', 'EMPTY': '', 'HOME': '/home/tester'}

## The shell logs every command (rejections at WARNING/CRITICAL); keep that
## noise out of the test output.
logging.disable(logging.CRITICAL)


def _frame_codes(anim):
    """The decoded code string of every FullFrame in a small frame animation."""
    return [''.join(f.getCode() for f in ff.getFrames()) for _delay, ff in anim.frames]


class ParseTest(unittest.TestCase):
    def parse(self, line):
        return parse_command(line, ENV)

    def test_basic_split(self):
        self.assertEqual(self.parse('echo hello world'), (['echo', 'hello', 'world'], False))

    def test_extra_whitespace(self):
        self.assertEqual(self.parse('  ls   -la  ')[0], ['ls', '-la'])

    def test_single_quote_literal(self):
        ## single quotes suppress expansion
        self.assertEqual(self.parse("echo '$FOO'"), (['echo', '$FOO'], False))

    def test_double_quote_expands(self):
        self.assertEqual(self.parse('echo "$FOO baz"'), (['echo', 'bar baz'], True))

    def test_brace_expansion(self):
        self.assertEqual(self.parse('echo ${FOO}x'), (['echo', 'barx'], True))

    def test_bare_expansion(self):
        self.assertEqual(self.parse('cat $FOO'), (['cat', 'bar'], True))

    def test_undefined_is_empty(self):
        self.assertEqual(self.parse('echo $NOPE.')[0], ['echo', '.'])

    def test_quote_boundary_terminates_name(self):
        ## "$FOO"x must read FOO then literal x, not the variable FOOx
        self.assertEqual(self.parse('echo "$FOO"x')[0], ['echo', 'barx'])

    def test_adjacent_quotes_concatenate(self):
        self.assertEqual(self.parse("echo a'b'\"c\"")[0], ['echo', 'abc'])

    def test_empty_quotes_are_a_token(self):
        self.assertEqual(self.parse('echo ""')[0], ['echo', ''])

    def test_no_glob_no_home(self):
        ## '*' and '~' stay literal — never expanded
        self.assertEqual(self.parse('ls *.py ~/x')[0], ['ls', '*.py', '~/x'])

    def test_expansion_not_resplit(self):
        ## an expanded value is one argument, never re-split into options
        self.assertEqual(self.parse('du $EVIL', )[0], ['du', ''])
        self.assertEqual(parse_command('du $X', {'X': '-d 0 /etc'})[0], ['du', '-d 0 /etc'])

    def test_unmatched_quote(self):
        with self.assertRaises(ShellParseError):
            self.parse('echo "hi')
        with self.assertRaises(ShellParseError):
            self.parse("echo 'hi")

    def test_metacharacters_rejected(self):
        for bad in ['a | b', 'a > b', 'a < b', 'a; b', 'a && b', 'a `b`']:
            with self.assertRaises(ShellParseError, msg=bad):
                self.parse(bad)

    def test_subcommand_rejected(self):
        with self.assertRaises(ShellParseError):
            self.parse('echo $(ls)')

    def test_metachar_in_quotes_is_literal(self):
        self.assertEqual(self.parse('echo "a|b;c"')[0], ['echo', 'a|b;c'])


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.cfg = NixieShellConfig()

    def decide(self, line, env=ENV):
        argv, _ = parse_command(line, env)
        return check_command(argv, self.cfg)

    def test_allowed_bare(self):
        self.assertTrue(self.decide('ls -la').allowed)
        self.assertTrue(self.decide('cat notes.txt').allowed)

    def test_not_on_allow_list(self):
        d = self.decide('ifconfig')
        self.assertFalse(d.allowed)
        self.assertEqual(d.level, logging.WARNING)

    def test_blocked_command(self):
        self.assertFalse(self.decide('mv a b').allowed)

    def test_critical_logs_critical(self):
        for line in ['rm foo', 'sudo ls', 'chmod 777 x', 'reboot']:
            self.assertEqual(self.decide(line).level, logging.CRITICAL, line)
            self.assertFalse(self.decide(line).allowed, line)

    def test_wrappers_blocked(self):
        for line in ['env python', 'xargs rm', 'timeout 5 ls', 'nohup du']:
            self.assertFalse(self.decide(line).allowed, line)

    def test_interpreters_blocked(self):
        for line in ['python s.py', 'perl s.pl', 'node s.js', 'bash -c x']:
            self.assertFalse(self.decide(line).allowed, line)

    def test_prefix_matching(self):
        self.assertTrue(self.decide('pactl list short sinks').allowed)
        self.assertTrue(self.decide('pactl info').allowed)
        self.assertTrue(self.decide('systemctl status nixie').allowed)
        self.assertTrue(self.decide('systemctl --user status pipewire').allowed)

    def test_prefix_does_not_overreach(self):
        self.assertFalse(self.decide('pactl set-sink-volume 0 50%').allowed)
        self.assertFalse(self.decide('systemctl stop nixie').allowed)

    def test_find_not_allowlisted(self):
        ## 'find' is a built-in special command, not on the generic allow list,
        ## so check_command never green-lights it (see StateMachineTest).
        self.assertFalse(self.decide('find /tmp').allowed)

    def test_find_unsafe_guard_defense_in_depth(self):
        ## the FIND_UNSAFE guard still blocks exec/write primaries should 'find'
        ## ever reach check_command (e.g. an admin re-adds it to allow_list).
        self.assertEqual(check_command(['find', '.', '-delete'], self.cfg).reason, 'blocked')
        self.assertEqual(check_command(['find', '.', '-exec', 'rm', '{}', '+'], self.cfg).reason, 'blocked')
        self.assertEqual(check_command(['find', '.', '-fprintf', 'out', 'x'], self.cfg).reason, 'blocked')

    def test_path_in_argv0_rejected(self):
        ## a path in argv[0] would execute that exact file under an allowed
        ## basename — rejected so only PATH-resolved bare names run
        self.assertFalse(self.decide('/bin/rm foo').allowed)        ## blocked basename
        self.assertFalse(self.decide('/bin/ls foo').allowed)        ## allowed basename, but path
        self.assertFalse(self.decide('./cat x').allowed)
        self.assertFalse(self.decide('/tmp/echo hi').allowed)


class PathDenyTest(unittest.TestCase):
    def setUp(self):
        self.cfg = NixieShellConfig()

    def decide(self, line):
        argv, _ = parse_command(line, ENV)
        return check_command(argv, self.cfg)

    def test_home_ssh_denied(self):
        d = self.decide('cat $HOME/.ssh/id_rsa')
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, 'protected path')

    def test_relative_traversal_denied(self):
        ## realpath collapses '..' so the deny list still catches it
        rel = os.path.relpath('/home/tester/.ssh/known_hosts')
        self.assertFalse(check_command(['cat', rel], self.cfg).allowed)

    def test_pem_anywhere_denied(self):
        self.assertFalse(self.decide('cat /tmp/server.pem').allowed)

    def test_etc_shadow_denied(self):
        self.assertFalse(self.decide('cat /etc/shadow').allowed)

    def test_ordinary_path_allowed(self):
        self.assertTrue(self.decide('cat /tmp/notes.txt').allowed)

    def test_config_cannot_unprotect(self):
        ## even an empty config keeps the always-on secret paths
        cfg = NixieShellConfig(allow_list=['cat'], path_deny_list=[])
        self.assertFalse(check_command(['cat', '/home/tester/.aws/credentials'], cfg).allowed)


class StateMachineTest(unittest.TestCase):
    def setUp(self):
        self.item = NixieShellItem(NixieShellConfig(), size=16)
        self.item.activate()
        ## skip the opening flash
        self.item.flash_msg = None

    def type(self, text):
        for c in text:
            self.item.key_char(c)

    def test_prompt_renders_animation(self):
        self.type('echo hi')
        self.assertIsInstance(self.item.for_display(), Animation)
        self.assertEqual(self.item.prompt.text(), 'echo hi')

    def test_backspace_keeps_prompt(self):
        self.type('ab')
        self.item.key_backspace()
        self.item.key_backspace()
        self.item.key_backspace()        ## extra backspace must not underflow
        self.assertEqual(self.item.prompt.text(), '')

    def test_backspace_reveals_hidden_left_chars(self):
        ## on a scrolled line, backspacing pulls the window right so hidden
        ## left-hand characters scroll back in (no trailing blanks)
        p = self.item.prompt
        for c in 'abcdefghijklmnopqrstuvwxyz0123':   ## 30 chars > display width
            p.key_char(c)
        self.assertGreater(p.window, 0)
        w_before = p.window
        p.key_backspace()
        self.assertLess(p.window, w_before)
        ## window sits exactly one tube past the last char (cursor cell), i.e.
        ## no wasted trailing blanks while characters are hidden off the left
        self.assertEqual(p.window, len(p.buffer) - p.width + 1)

    def test_left_overflow_indicator(self):
        ## a flashing '<' marks characters hidden off the left edge
        p = self.item.prompt
        for c in 'abcdefghijklmnopqrstuvwxyz0123':
            p.key_char(c)
        self.assertGreater(p.window, 0)
        codes = _frame_codes(p.render())
        self.assertTrue(any('<' in c for c in codes), codes)
        ## no '<' once we scroll fully back to the start
        for _ in range(len(p.buffer)):
            p.key_left()
        self.assertEqual(p.window, 0)
        self.assertFalse(any('<' in c for c in _frame_codes(p.render())))

    def test_right_overflow_indicator(self):
        ## a flashing '>' marks characters hidden off the right edge
        p = self.item.prompt
        for c in 'abcdefghijklmnopqrstuvwxyz0123':
            p.key_char(c)
        p.key_home()                      ## back to the start: chars now hidden right
        self.assertEqual(p.window, 0)
        codes = _frame_codes(p.render())
        self.assertTrue(any('>' in c for c in codes), codes)
        self.assertFalse(any('<' in c for c in codes))   ## nothing hidden left at start

    def test_ctrl_a_e_move_cursor(self):
        self.type('echo hello')
        end = len('echo hello')
        self.assertEqual(self.item.prompt.cursor, end)
        self.item.key_ctrl_a()
        self.assertEqual(self.item.prompt.cursor, 0)
        self.item.key_ctrl_e()
        self.assertEqual(self.item.prompt.cursor, end)

    def test_navigator_routes_ctrl_keys(self):
        ## CTRL_A / CTRL_E tokens reach the prompt through the Navigator
        from pyxielib.navigator import Menu, Navigator
        item = NixieShellItem(NixieShellConfig(), size=16)
        nav = Navigator(Menu("root", [item]))
        nav.key_entry("ENTER")            ## descend into the shell (activate)
        item.flash_msg = None
        for c in 'abc':
            nav.key_entry(c)
        nav.key_entry("CTRL_A")
        self.assertEqual(item.prompt.cursor, 0)
        nav.key_entry("CTRL_E")
        self.assertEqual(item.prompt.cursor, 3)

    def test_run_command_to_output(self):
        self.type('echo nixie')
        self.item.key_enter()
        ## drive for_display until the command finishes
        out = self._settle()
        self.assertEqual(self.item.state, 'output')
        self.assertIsInstance(out, Animation)
        self.assertIn('nixie', '\n'.join(self.item.last_output))

    def test_replay_restores_output(self):
        self.type('echo replayme')
        self.item.key_enter()
        self._settle()
        self.item.key_esc()              ## leave output -> fresh prompt
        self.assertEqual(self.item.state, 'prompt')
        self.type('replay')
        self.item.key_enter()
        self.assertEqual(self.item.state, 'output')
        self.assertIn('replayme', '\n'.join(self.item.last_output))

    def test_unmatched_quote_keeps_text(self):
        self.type('echo "hi')
        self.item.key_enter()
        self.assertEqual(self.item.flash_msg, 'unmatched quote')
        self.assertEqual(self.item.prompt.text(), 'echo "hi')

    def test_rejected_command_keeps_text(self):
        self.type('rm foo')
        self.item.key_enter()
        self.assertEqual(self.item.state, 'prompt')
        self.assertEqual(self.item.prompt.text(), 'rm foo')

    def test_esc_opens_exit_prompt(self):
        self.item.key_esc()
        self.assertEqual(self.item.state, 'exit')
        self.assertEqual(self.item.for_display(), 'EXIT? Y/N')

    def test_exit_no_restores_prompt(self):
        self.type('ls')
        self.item.key_esc()
        self.item.key_char('n')
        self.assertEqual(self.item.state, 'prompt')
        self.assertEqual(self.item.prompt.text(), 'ls')

    def test_exit_yes_sets_done(self):
        self.item.key_esc()
        self.item.key_char('y')
        self.assertTrue(self.item.is_done())

    def test_triple_esc_exits(self):
        self.item.key_esc()
        self.item.key_esc()
        self.item.key_esc()
        self.assertTrue(self.item.is_done())

    def test_find_directory_runs(self):
        with tempfile.TemporaryDirectory() as d:
            self.type('find ' + d)
            self.item.key_enter()
            self.assertEqual(self.item.state, 'running')
            self._settle()
            self.assertIn(d, '\n'.join(self.item.last_output))

    def test_find_file_echoes_name(self):
        with tempfile.NamedTemporaryFile() as f:
            self.type('find ' + f.name)
            self.item.key_enter()
            ## a file operand is echoed directly, no subprocess
            self.assertEqual(self.item.state, 'output')
            self.assertEqual(self.item.last_output, [f.name])

    def test_find_missing_flashes(self):
        self.type('find /no/such/path/here')
        self.item.key_enter()
        self.assertEqual(self.item.flash_msg, 'Does not exist')
        self.assertEqual(self.item.state, 'prompt')

    def test_find_rejects_options_and_extra_operands(self):
        for line in ['find', 'find . -name x', 'find /tmp /etc', 'find -L /tmp']:
            self.setUp()
            self.type(line)
            self.item.key_enter()
            self.assertEqual(self.item.flash_msg, 'not allowed', line)
            self.assertEqual(self.item.prompt.text(), line, line)

    def test_find_protected_path_denied(self):
        self.type('find $HOME/.ssh')
        self.item.key_enter()
        self.assertEqual(self.item.flash_msg, 'protected path')

    def test_less_existing_file_cats_it(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write('viewme contents\n')
            path = f.name
        try:
            self.type('less ' + path)
            self.item.key_enter()
            self.assertEqual(self.item.state, 'running')
            self._settle()
            self.assertIn('viewme contents', '\n'.join(self.item.last_output))
        finally:
            os.unlink(path)

    def test_less_missing_file_flashes(self):
        self.type('less /no/such/file.txt')
        self.item.key_enter()
        self.assertEqual(self.item.flash_msg, 'no such file')
        self.assertEqual(self.item.state, 'prompt')

    def test_less_rejects_options_and_extra_operands(self):
        for line in ['less', 'less a b', 'less -N file']:
            self.setUp()
            self.type(line)
            self.item.key_enter()
            self.assertEqual(self.item.flash_msg, 'not allowed', line)
            self.assertEqual(self.item.prompt.text(), line, line)

    def test_less_protected_path_denied(self):
        self.type('less $HOME/.ssh/id_rsa')
        self.item.key_enter()
        self.assertEqual(self.item.flash_msg, 'protected path')

    def test_editors_redirect_to_less(self):
        ## the hint is too wide for the display, so it flashes as a sequence
        ## ('No <cmd>' / 'only zuul' / 'only less') — one screen at a time
        for cmd in ['vi', 'vim', 'nvim', 'neovim', 'emacs', 'nano']:
            self.setUp()
            self.type(cmd + ' file.txt')
            self.item.key_enter()
            for expected in ('No %s' % cmd, 'only zuul', 'only less'):
                self.assertEqual(self.item.for_display(), expected, cmd)
                self.item.flash_until = 0.0   ## expire this flash, advance the queue
            self.assertEqual(self.item.state, 'prompt', cmd)
            self.assertEqual(self.item.prompt.text(), cmd + ' file.txt', cmd)

    def test_typed_command_logged_not_expanded(self):
        ## a secret in an expanded variable must not reach the log
        os.environ['NIXIE_TEST_SECRET'] = 's3cr3t-value'
        self.type('echo $NIXIE_TEST_SECRET')
        logging.disable(logging.NOTSET)          ## re-enable for assertLogs
        try:
            with self.assertLogs('pyxielib.nixie_shell', level='INFO') as cm:
                self.item.key_enter()
        finally:
            logging.disable(logging.CRITICAL)
        joined = '\n'.join(cm.output)
        self.assertIn('echo $NIXIE_TEST_SECRET', joined)
        self.assertNotIn('s3cr3t-value', joined)

    def test_wait_screen_appears_after_gate(self):
        ## the wait screen must not show within the first 100ms, then appears as
        ## a short one-shot animation that lets the scheduler re-poll us
        self.item._start_runner(['python3', '-c', 'import time; time.sleep(0.5)'])
        self.assertLess(self.item.runner.elapsed(), self.item._RUNNING_GATE)
        time.sleep(self.item._RUNNING_GATE + 0.02)
        spin = self.item.for_display()
        spin.updateFrameSet()
        code = spin.getCode()
        self.assertIn('Running for', code)
        self.assertIn(' s', code)                ## space between number and 's'
        self.item.runner.cancel()
        self._settle()
        self.assertEqual(self.item.state, 'output')

    def test_fast_command_skips_wait_screen(self):
        ## a sub-100ms command goes straight to output — no 'Running for' screen
        self.type('echo quick')
        self.item.key_enter()
        first = self.item.for_display()          ## first running poll, elapsed < gate
        if isinstance(first, Animation):
            first.updateFrameSet()
            self.assertNotIn('Running for', first.getCode())
        self._settle()
        self.assertEqual(self.item.state, 'output')
        self.assertIn('quick', '\n'.join(self.item.last_output))

    def test_history_records_and_navigates(self):
        for cmd in ['echo one', 'echo two']:
            self.type(cmd)
            self.item.key_enter()
            self._settle()
            self.item.key_esc()                   ## back to a fresh prompt
        ## up arrow recalls previous commands, newest first
        self.item.key_up()
        self.assertEqual(self.item.prompt.text(), 'echo two')
        self.item.key_up()
        self.assertEqual(self.item.prompt.text(), 'echo one')
        ## down arrow walks back toward the empty line
        self.item.key_down()
        self.assertEqual(self.item.prompt.text(), 'echo two')
        self.item.key_down()
        self.assertEqual(self.item.prompt.text(), '')

    def test_history_command_shows_history(self):
        self.type('echo a')
        self.item.key_enter()
        self._settle()
        self.item.key_esc()
        self.type('history')
        self.item.key_enter()
        self.assertEqual(self.item.state, 'output')
        self.assertIn('echo a', self.item.output.lines)
        ## history view is transient — it must not clobber the replayable output
        self.assertNotEqual(self.item.last_output, self.item.output.lines)

    def test_history_persists_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'hist')
            cfg = NixieShellConfig(history_file=path)
            item = NixieShellItem(cfg, size=16)
            item.activate(); item.flash_msg = None
            for c in 'echo hi':
                item.key_char(c)
            item.key_enter()
            ## a fresh item with the same file reloads the history
            item2 = NixieShellItem(NixieShellConfig(history_file=path), size=16)
            self.assertIn('echo hi', item2.history.entries)

    def _settle(self):
        ## poll for_display (as the menu would) until the subprocess thread
        ## finishes and the item switches to the output viewer
        for _ in range(400):
            out = self.item.for_display()
            if self.item.state == 'output':
                return out
            time.sleep(0.01)
        self.fail("command did not finish")
        return None


if __name__ == '__main__':
    unittest.main()
