#! /usr/bin/python3
##pylint: disable=wrong-import-position

import os
import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import assembler, controller, program, scheduler, stockticker


DEBUG = True
RASPI = True
ctrl = None
if DEBUG:
    ctrl = controller.TerminalController(clear_screen=False, print_code=True)
elif RASPI:
    print("Using the RaspberryPi outputs directly")
    ctrl = controller.RaspberryPiController(debug=True, speed=10**6)
else:
    print("Opening connection to Nixie Control Board")
    ctrl = controller.SerialController('/dev/ttyACM0', debug=True)
    print("Connection established")


clock_prgm = program.ClockProgram(flash=False)
ticker_prgm = stockticker.StockTicker()
schl = (
    ("*/1 * * * *", 1, ticker_prgm),
)

asmlr = assembler.Assembler(controller=ctrl)
schdlr = scheduler.CronScheduler(schl, asmlr, default=clock_prgm)

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
            print("Scheduler stopped unexpectedly")
            break
        time.sleep(0.1)
except KeyboardInterrupt:
    print("User requested exit")

asmlr.stop()
schdlr.stop()
