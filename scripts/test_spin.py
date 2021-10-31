#! /usr/bin/python3

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import animation, assembler, controller, decoder, tube_manager


c = controller.TerminalController(clear_screen=True)
a = assembler.Assembler(controller=c)
a.start()
a.setAnimation(animation.makeSpinAnimation(num_tubes=3, rate=10, loop=True))

try:
    while not a.animationDone():
        time.sleep(0.1)
except KeyboardInterrupt:
    print("User required exit")

a.stop()
