#! /usr/bin/python3
##pylint: disable=wrong-import-position

import argparse
import sys
import time

sys.path.append("./")

from pyxielib import assembler, controller
from pyxielib.animation_file import FileAnimation

parser = argparse.ArgumentParser(description='Nixie Tube Animation Running')
parser.add_argument('-c', '--controller', choices=['terminal', 'serial'], default='terminal')
parser.add_argument('-n', '--no-clear', action='store_true')
parser.add_argument('-a', '--animation', default="animations/packman.ani")
parser.add_argument('-l', '--loops', type=int, default=1)
args = parser.parse_args()


ctrl = None
clear_screen = not args.no_clear
if args.controller == 'terminal':
    ctrl = controller.TerminalController(clear_screen=clear_screen)
elif args.controller == 'serial':
    print("Opening connection to Nixie Control Board")
    ctrl = controller.SerialController('/dev/ttyACM0', debug=True, baud=115200)
    print("Connection established")
else:
    raise Exception(f"Invalid value for argument --controller: {args.controller}")

ani = FileAnimation(args.animation)
asmlr = assembler.Assembler(controller=ctrl)
asmlr.start()
asmlr.setAnimation(ani)

try:
    count = 0
    while True:
        if asmlr.animationDone():
            count += 1
            if args.loops and count >= args.loops:
                break
            asmlr.rerun()

        time.sleep(0.1)
except KeyboardInterrupt:
    print("User required exit")

asmlr.stop()
