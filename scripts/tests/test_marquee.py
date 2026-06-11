#! /usr/bin/python3
##pylint: disable=wrong-import-position

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import assembler, controller, animation


ctrl = controller.TerminalController(clear_screen=True)
ani = animation.MarqueeAnimation.fromText("Floating along", size=8)
asmlr = assembler.Assembler(controller=ctrl)
asmlr.start()
asmlr.setAnimation(ani)

try:
    while not asmlr.animationDone():
        time.sleep(0.1)
except KeyboardInterrupt:
    print("User required exit")

asmlr.stop()
