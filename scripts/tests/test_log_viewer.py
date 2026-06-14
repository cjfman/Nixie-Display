#!/usr/bin/python3
##pylint: disable=wrong-import-position
"""Tests for LogViewerItem: per-file position memory and disk persistence.
Pure logic only — no display hardware required. Run directly:

    python3 scripts/tests/test_log_viewer.py
"""

import json
import os
import sys
import tempfile

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pyxielib.menu_library import LogViewerItem

PASSED = 0


def ok(condition, label):
    global PASSED
    assert condition, label
    PASSED += 1
    print(f"ok - {label}")


def make_viewer(path, lines, positions_file, *, clear=False):
    """Return a LogViewerItem with a fixed line set and a temp positions file.

    Pass clear=True at the start of a test to wipe any positions left by a
    previous test in the same file.
    """
    LogViewerItem._positions_file = positions_file
    LogViewerItem._positions = None
    if clear:
        with open(positions_file, 'w') as f:
            json.dump({}, f)
    v = LogViewerItem(path, tail=200)
    v._read = lambda: list(lines)
    return v


## ── preamble stripping ───────────────────────────────────────────────────────

def test_preamble_strip():
    strip = LogViewerItem._strip_preamble
    ok(strip('2026-06-11 18:38:10,380 INFO     pyxielib.stockticker: Stock ticker thread starting')
           == 'INFO Stock ticker thread starting',
       "INFO preamble stripped")
    ok(strip('2026-06-11 18:38:10,380 WARNING  pyxielib.foo: something')
           == 'WARN something',
       "WARNING → WARN")
    ok(strip('2026-06-11 18:38:10,380 ERROR    pyxielib.foo: oops') == 'ERRR oops',
       "ERROR → ERRR")
    ok(strip('2026-06-11 18:38:10,380 DEBUG    pyxielib.foo: dbg') == 'DBUG dbg',
       "DEBUG → DBUG")
    ok(strip('2026-06-11 18:38:10,380 VERBOSE  pyxielib.foo: v') == 'VBOS v',
       "VERBOSE → VBOS")
    ok(strip('2026-06-11 18:38:10,380 TRACE    pyxielib.foo: t') == 'TRCE t',
       "TRACE → TRCE")
    ok(strip('  File "foo.py", line 42, in bar') == '  File "foo.py", line 42, in bar',
       "traceback continuation passes through unchanged")
    ok(strip('no preamble here') == 'no preamble here',
       "non-matching line passes through unchanged")


## ── first open ───────────────────────────────────────────────────────────────

def test_first_open(tmp_pos):
    lines = [f'line{i}' for i in range(10)]
    v = make_viewer('/fake/a.log', lines, tmp_pos, clear=True)
    v.activate()
    ok(v.line == 9, "first open starts at newest (index n-1)")


## ── navigation resets offset ─────────────────────────────────────────────────

def test_navigation(tmp_pos):
    lines = [f'line{i}' for i in range(10)]
    v = make_viewer('/fake/a.log', lines, tmp_pos, clear=True)
    v.activate()
    ok(v.line == 9, "starts at newest")
    v.key_up()
    ok(v.line == 8, "key_up moves to older entry")
    v.key_down()
    ok(v.line == 9, "key_down moves to newer entry")
    v.key_up(); v.key_up(); v.key_up()
    ok(v.line == 6, "three key_up moves to index 6")
    ok(v.offset == 0, "offset reset on line change")


## ── position saved and restored within a session ─────────────────────────────

def test_save_restore_same_session(tmp_pos):
    lines = [f'line{i}' for i in range(10)]
    v = make_viewer('/fake/a.log', lines, tmp_pos, clear=True)
    v.activate()
    for _ in range(3):
        v.key_up()
    ok(v.line == 6, "scrolled to index 6 (3 from newest)")
    v.reset()

    v2 = make_viewer('/fake/a.log', lines, tmp_pos)
    v2.activate()
    ok(v2.line == 6, "re-activate restores saved index 6")


## ── disk persistence across restarts ─────────────────────────────────────────

def test_disk_persistence(tmp_pos):
    lines = [f'line{i}' for i in range(10)]
    v = make_viewer('/fake/a.log', lines, tmp_pos, clear=True)
    v.activate()
    for _ in range(4):
        v.key_up()
    v.reset()
    ok(os.path.exists(tmp_pos), "positions file written to disk")

    with open(tmp_pos) as f:
        saved = json.load(f)
    ## expanduser('/fake/a.log') == '/fake/a.log'
    key = os.path.expanduser('/fake/a.log')
    ok(key in saved and saved[key] == 4, "saved distance-from-newest is 4")

    ## Simulate restart: clear class-level cache entirely
    LogViewerItem._positions = None
    v2 = LogViewerItem('/fake/a.log', tail=200)
    v2._read = lambda: list(lines)
    v2.activate()
    ok(v2.line == 5, "position restored from disk after simulated restart (index 9-4=5)")


## ── per-file independence ─────────────────────────────────────────────────────

def test_per_file(tmp_pos):
    lines = [f'line{i}' for i in range(10)]
    va = make_viewer('/fake/a.log', lines, tmp_pos, clear=True)
    vb = make_viewer('/fake/b.log', lines, tmp_pos)

    va.activate(); vb.activate()
    for _ in range(3):
        va.key_up()   ## va at index 6
    ## vb stays at 9
    va.reset(); vb.reset()

    va2 = make_viewer('/fake/a.log', lines, tmp_pos)
    vb2 = make_viewer('/fake/b.log', lines, tmp_pos)
    va2.activate(); vb2.activate()
    ok(va2.line == 6, "a.log restores to index 6")
    ok(vb2.line == 9, "b.log restores to index 9 independently")


## ── tail overflow: saved position beyond new tail → oldest ───────────────────

def test_tail_overflow(tmp_pos):
    lines_long  = [f'line{i}' for i in range(200)]
    lines_short = [f'new{i}'  for i in range(50)]

    v = make_viewer('/fake/a.log', lines_long, tmp_pos, clear=True)
    v.activate()
    ## Scroll deep: 180 from newest → index 19
    for _ in range(180):
        v.key_up()
    ok(v.line == 19, "scrolled deep (index 19, 180 from newest)")
    v.reset()

    v2 = make_viewer('/fake/a.log', lines_short, tmp_pos)
    v2.activate()
    ok(v2.line == 0, "saved depth 180 > new tail 50: falls back to oldest (index 0)")


## ── log growth: same age offset after new entries ────────────────────────────

def test_log_growth(tmp_pos):
    lines_10 = [f'line{i}' for i in range(10)]
    lines_15 = [f'line{i}' for i in range(15)]  ## 5 new entries appended

    v = make_viewer('/fake/a.log', lines_10, tmp_pos, clear=True)
    v.activate()
    for _ in range(3):
        v.key_up()   ## 3 from newest on a 10-line log
    v.reset()

    v2 = make_viewer('/fake/a.log', lines_15, tmp_pos)
    v2.activate()
    ok(v2.line == 11, "after log grew to 15, still 3 from newest (index 11)")


def main():
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tf:
        tmp = tf.name
    try:
        test_preamble_strip()
        test_first_open(tmp)
        test_navigation(tmp)
        test_save_restore_same_session(tmp)
        test_disk_persistence(tmp)
        test_per_file(tmp)
        test_tail_overflow(tmp)
        test_log_growth(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    print(f"\nAll {PASSED} checks passed")


if __name__ == '__main__':
    main()
