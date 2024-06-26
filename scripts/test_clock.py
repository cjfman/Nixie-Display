#!/usr/bin/python3
##pylint: disable=wrong-import-position

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import assembler, controller, program, scheduler


DEBUG = True
ctrl = None
if DEBUG:
    ctrl = controller.TerminalController(clear_screen=True, print_code=False)
else:
    print("Opening connection to Nixie Control Board")
    ctrl = controller.SerialController('/dev/ttyACM0', debug=True)
    print("Connection established")

asmlr = assembler.Assembler(controller=ctrl)
prgm = program.ClockProgram(asmlr, flash=True)
schdlr = scheduler.SingleProgramScheduler(prgm, asmlr)

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
