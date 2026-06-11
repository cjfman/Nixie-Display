#!/usr/bin/python3
##pylint: disable=wrong-import-position

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import assembler, controller, key_watcher, scheduler, usermenuprogram


ctrl = controller.TerminalController(clear_screen=True)
asmlr = assembler.Assembler(controller=ctrl)
prgm = usermenuprogram.UserMenuProgram("/dev/input/event22")
schdlr = scheduler.SingleProgramScheduler(prgm, asmlr, period=0.01)

print("Starting program")
schdlr.run()
asmlr.start()
time.sleep(1)

try:
    while True:
        if not asmlr.isRunning():
            print("Assembler stopped unexpectedly")
            break
        if not schdlr.isRunning():
            print("Schedluer stopped unexpectedly")
            break
        time.sleep(0.1)
except KeyboardInterrupt:
    print("User requested exit")

asmlr.stop()
schdlr.stop()
