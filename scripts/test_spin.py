#! /usr/bin/python3

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import animation, assembler, controller, decoder, tube_manager


c = controller.TerminalController(clear_screen=True)
a1 = animation.makeSpinAnimation(num_tubes=3, rate=10, loop=True)
#a2 = animation.makeTextAnimation("Done")
asmlr = assembler.Assembler(controller=c)
asmlr.start()
asmlr.setAnimation(a1)

try:
    while not asmlr.animationDone():
        time.sleep(0.1)
except KeyboardInterrupt:
    print("User required exit")

asmlr.stop()
