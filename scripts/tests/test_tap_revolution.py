#! /usr/bin/python3
## pylint: disable=wrong-import-position
"""Preview and author Tap Revolution levels without the full menu.

Modes:
  (default)   Play a level on the terminal controller. With --autoplay, taps are
              fired at each note's exact time so you can eyeball the scrolling
              playfield, the score section, and the results summary.
  --record    Tap a chart out by ear: arrow keypresses are timestamped and dumped
              as a time-mode .trl (the authoring path when all you have is a
              recording). This is also the seed of future audio sync.
"""

import argparse
import os
import sys
import termios
import time
import threading
import tty

sys.path.append("./")

from pyxielib import assembler, controller
from pyxielib import tap_revolution as taplib

LANE_NAME = {'L': 'left', 'R': 'right', 'U': 'up', 'D': 'down'}
ARROW_SEQ = {b'A': 'U', b'B': 'D', b'C': 'R', b'D': 'L'}


def make_parser():
    parser = argparse.ArgumentParser(description='Tap Revolution level preview / author')
    parser.add_argument('-a', '--level', default='levels/demo.trl',
        help="A .trl file path, or a built-in level name")
    parser.add_argument('-c', '--controller', choices=['terminal', 'serial'], default='terminal')
    parser.add_argument('--no-clear', action='store_true', help="Don't clear the screen each frame")
    parser.add_argument('--autoplay', action='store_true', help="Auto-tap every note perfectly")
    parser.add_argument('--jitter', type=float, default=0.0,
        help="Seconds to offset autoplay taps (to exercise GOOD/OK/MISS)")
    parser.add_argument('--record', action='store_true',
        help="Capture arrow keypresses and print a time-mode .trl")
    parser.add_argument('--name', default='Recorded', help="Level name for --record output")
    return parser


def load_level(spec) -> taplib.Level:
    if spec in taplib.BUILTIN_LEVELS:
        return taplib.BUILTIN_LEVELS[spec]

    return taplib.Level.from_file(spec)


def make_controller(name, clear_screen):
    if name == 'serial':
        return controller.SerialController('/dev/ttyACM0', debug=True, baud=115200)

    return controller.TerminalController(clear_screen=clear_screen)


def autoplay(animation, jitter):
    """Fire a tap at each note's exact time (plus jitter), in a background thread."""
    t0 = animation.start_time
    for ns in sorted(animation.note_states, key=lambda n: n.target):
        when = t0 + ns.target
        delay = when - time.time()
        if delay > 0:
            time.sleep(delay)
        animation.tap(ns.lane, when=when + jitter)


def run_preview(args):
    """Play a level on the controller until it finishes, then print the results."""
    level = load_level(args.level)
    ani = taplib.TapRevolutionAnimation(level)
    ctrl = make_controller(args.controller, not args.no_clear)
    asmlr = assembler.Assembler(controller=ctrl)
    asmlr.start()
    asmlr.setAnimation(ani)
    if args.autoplay:
        threading.Thread(target=autoplay, args=(ani, args.jitter), daemon=True).start()

    while not ani.done():
        time.sleep(0.05)
    asmlr.stop()
    print(f"\n{level.name}: {ani.results_text()}")


def read_arrow(fd) -> str:
    """Read one byte; return a lane for an arrow-key escape sequence, else ''."""
    b = os.read(fd, 1)
    if b in (b'\n', b'\r', b'q'):
        return 'quit'
    if b != b'\x1b' or os.read(fd, 1) != b'[':
        return ''

    return ARROW_SEQ.get(os.read(fd, 1), '')


def record_chart(args):
    """Read arrow keypresses, timestamp them, and print a time-mode .trl."""
    print("Recording. Press arrows in time; Enter or 'q' to finish.", file=sys.stderr)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    taps = []
    try:
        tty.setcbreak(fd)
        start = time.time()
        while True:
            lane = read_arrow(fd)
            if lane == 'quit':
                break
            if lane:
                taps.append((time.time() - start, lane))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    print_trl(args.name, taps)


def print_trl(name, taps):
    """Emit a time-mode .trl chart for the recorded taps."""
    print(f"name: {name}")
    print("mode: time")
    print("scroll_time: 2.0")
    print("audio:")
    print("# sec   arrow")
    for when, lane in taps:
        print(f"{when:.2f}  {LANE_NAME[lane]}")


def main():
    args = make_parser().parse_args()
    if args.record:
        record_chart(args)
    else:
        run_preview(args)


if __name__ == '__main__':
    main()
