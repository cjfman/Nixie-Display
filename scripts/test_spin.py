#! /usr/bin/python3

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import animation, assembler, controller, decoder, tube_manager


c = controller.TerminalController(clear_screen=True)
a = assembler.Assembler(controller=c)
a.start()
a.setAnimation(animation.SpinAnimation(num_tubes=3, rate=10))

time.sleep(3)
a.stop()
