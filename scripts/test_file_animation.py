#! /usr/bin/python3
##pylint: disable=wrong-import-position

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import assembler, controller, animation


DEBUG = True
ctrl = None
if DEBUG:
    ctrl = controller.TerminalController(clear_screen=True)
else:
    print("Opening connection to Nixie Control Board")
    ctrl = controller.SerialController('/dev/ttyACM0', debug=True, baud=115200)
    print("Connection established")

ani = animation.FileAnimation('animations/packman.ani')
asmlr = assembler.Assembler(controller=ctrl)
asmlr.start()
asmlr.setAnimation(ani)

try:
    while True:
        if asmlr.animationDone():
            asmlr.rerun()

        time.sleep(0.1)
except KeyboardInterrupt:
    print("User required exit")

asmlr.stop()
