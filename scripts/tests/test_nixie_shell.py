#!/usr/bin/python3
##pylint: disable=wrong-import-position
"""Tests for the nixie shell parser, safety checks, prompt editor, and runner.
Pure logic only — no display required. Run directly:

    python3 scripts/tests/test_nixie_shell.py
"""

import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pyxielib.nixie_shell import (
    CommandRunner, NixieShellConfig, PromptLine, ShellParseError,
    check_command, parse_command,
)

PASSED = 0


def ok(condition, label):
    global PASSED
    assert condition, label
    PASSED += 1
    print(f"ok - {label}")


def raises(exc, fn, label):
    try:
        fn()
    except exc:
        ok(True, label)
    else:
        ok(False, label)


## parse_command returns (argv, expanded)
def argv_of(line):
    return parse_command(line)[0]


## check_command returns a Decision; this helper returns None on allow, reason str on deny
def check(line, cfg=None):
    argv, _ = parse_command(line)
    d = check_command(argv, cfg or NixieShellConfig())
    return None if d.allowed else d.reason


def test_parser():
    ok(argv_of('ls -la /tmp') == ['ls', '-la', '/tmp'], "plain words split")
    ok(argv_of('echo "a b" c') == ['echo', 'a b', 'c'], "double quotes group")
    ok(argv_of("echo 'a b'") == ['echo', 'a b'], "single quotes group")
    ok(argv_of('echo a"b c"d') == ['echo', 'ab cd'], "adjacent parts merge")
    ok(argv_of('echo "a;b|c"') == ['echo', 'a;b|c'], "metachars literal in double quotes")
    ok(argv_of("echo 'a;b|c'") == ['echo', 'a;b|c'], "metachars literal in single quotes")
    ok(argv_of('echo ""') == ['echo', ''], "quoted empty arg survives")
    ok(argv_of('ls *') == ['ls', '*'], "glob stays literal")
    ok(argv_of('ls ~/x') == ['ls', '~/x'], "tilde stays literal")
    raises(ShellParseError, lambda: parse_command('echo "a'), "unmatched double quote")
    raises(ShellParseError, lambda: parse_command("echo 'a"), "unmatched single quote")
    for bad in ('a;b', 'a|b', 'a&b', 'a<b', 'a>b', 'a`b'):
        raises(ShellParseError, lambda b=bad: parse_command(b), f"reject metachar in {bad!r}")
    raises(ShellParseError, lambda: parse_command('echo $(ls)'), "reject $() subcommand")
    raises(ShellParseError, lambda: parse_command('echo "`ls`"'), "reject backtick in dquotes")


def test_expansion():
    os.environ['NIXIE_TEST'] = 'hello world'
    os.environ['NIXIE_T2'] = 'two'
    ok(argv_of('echo $NIXIE_TEST') == ['echo', 'hello world'], "no word-split on expansion")
    ok(argv_of('echo ${NIXIE_TEST}x') == ['echo', 'hello worldx'], "curly brace form")
    ok(argv_of('echo "v=$NIXIE_T2"') == ['echo', 'v=two'], "expansion inside double quotes")
    ok(argv_of("echo '$NIXIE_TEST'") == ['echo', '$NIXIE_TEST'], "no expansion in single quotes")
    ## Opus keeps empty tokens (no word-splitting); the empty string is still argv[1]
    ok(argv_of('echo $NIXIE_UNDEFINED') == ['echo', ''], "undefined var expands to empty string")
    ok(argv_of('echo "$NIXIE_UNDEFINED"') == ['echo', ''], "undefined var empty in double quotes")
    ok(parse_command('echo $NIXIE_T2')[1] is True, "expansion flagged")
    ok(parse_command('echo abc')[1] is False, "no expansion not flagged")


def test_policy():
    ok(check('ls -la /tmp') is None, "ls with any args allowed")
    ok(check('echo hi') is None, "echo allowed")
    ok(check('wc -l /tmp/x') is None, "wc allowed")
    ok(check('pactl info') is None, "pactl info allowed")
    ok(check('pactl list sinks') is None, "pactl list * matches with args")
    ok(check('pactl list') is None, "pactl list * matches with no further args")
    ok(check('pactl load-module foo') == "not allowed", "pactl load-module denied")
    ok(check('systemctl status nginx') is None, "systemctl status allowed")
    ok(check('systemctl --user status pulseaudio') is None, "systemctl --user status allowed")
    ok(check('systemctl stop nginx') == "not allowed", "systemctl stop denied")
    ok(check('sudo ls') == "blocked", "sudo blocked")
    ok(check('grep x y') == "blocked", "grep blocked")
    ok(check('git log') == "blocked", "git blocked")
    ok(check('python3 foo.py') == "blocked", "python3 blocked")
    ok(check('/bin/ls') == "not allowed", "absolute path in argv[0] rejected")
    ok(check('./ls') == "not allowed", "relative path in argv[0] rejected")
    os.environ['NIXIE_CMD'] = '/bin/ls'
    raises(ShellParseError, lambda: parse_command('$NIXIE_CMD /tmp'), "expanded path in argv[0] rejected at parse time")
    os.environ['NIXIE_CMD'] = 'ls'
    raises(ShellParseError, lambda: parse_command('$NIXIE_CMD /tmp'), "expanded allowlisted name in argv[0] rejected at parse time")
    ok(check('hexdump x') == "not allowed", "unknown command default-denied")

    ## Secret-path protection
    ok(check('cat /etc/shadow') == "protected path", "/etc/shadow protected")
    ok(check('ls /proc/1') == "protected path", "/proc/* protected")
    ok(check('ls /etc') is None, "non-secret /etc path allowed")

    ## Config allow_list and block_list
    cfg = NixieShellConfig(allow_list=['hexdump', 'ls'], block_list=['ls'])
    ok(check('hexdump x', cfg) is None, "config allow honored")
    ok(check('ls', cfg) == "blocked", "config block honored")
    ok(check('rm x', cfg) == "blocked", "ALWAYS_BLOCKED overrides config allow")


def test_prompt_line():
    p = PromptLine()
    for c in 'abcdefghijklmnopqrstuvwxyz':   ## 26 chars — wider than the 15-tube window
        p.key_char(c)
    p.render()
    ok(p.window > 0, "long line hides characters off the left edge")
    prev_window = p.window
    p.key_backspace()
    p.render()
    ok(p.window == prev_window - 1, "backspace scrolls window right when text is hidden left")
    ok(p.cursor == 25, "backspace removes the character")
    p.key_home()
    ok(p.cursor == 0, "key_home moves cursor to start")
    p.render()
    ok(p.window == 0, "key_home scrolls window back to the start")
    p.key_end()
    ok(p.cursor == len(p.buffer), "key_end moves cursor to end")

    q = PromptLine()
    for c in 'hi':
        q.key_char(c)
    q.render()
    q.key_backspace()
    ok(q.window == 0 and q.text() == 'h', "short-line backspace deletes normally")


def wait_for(runner, timeout=5.0) -> list:
    deadline = time.time() + timeout
    while runner.is_running() and time.time() < deadline:
        time.sleep(0.01)
    return runner.output_lines()


def test_runner():
    r = CommandRunner(['echo', 'hello']).run()
    lines = wait_for(r)
    ok(lines == ['hello'], "echo output captured")

    ## stdin is DEVNULL: bare cat exits immediately instead of hanging
    r = CommandRunner(['cat']).run()
    ok(not r.is_running() or wait_for(r, timeout=2) is not None, "bare cat exits on DEVNULL stdin")
    ok(r.output_lines() == ['(no output)'], "empty output placeholder")

    ## output cap kills and truncates a runaway command
    r = CommandRunner(['cat', '/dev/zero'], max_bytes=4096).run()
    lines = wait_for(r, timeout=10)
    ok(any('truncated' in ln for ln in lines), "runaway output killed and truncated")


def main():
    test_parser()
    test_expansion()
    test_policy()
    test_prompt_line()
    test_runner()
    print(f"\nAll {PASSED} checks passed")


if __name__ == '__main__':
    main()
