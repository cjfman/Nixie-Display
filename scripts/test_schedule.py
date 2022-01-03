#! /usr/bin/python3
##pylint: disable=wrong-import-position

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import assembler, controller, program, scheduler


DEBUG = True
ctrl = None
if DEBUG:
    ctrl = controller.TerminalController(clear_screen=False, print_code=True)
else:
    print("Opening connection to Nixie Control Board")
    ctrl = controller.SerialController('/dev/ttyACM0', debug=True)
    print("Connection established")


clock_prgm = program.ClockProgram(flash=False)
rss_prgm = program.RssProgram("https://rss.nytimes.com/services/xml/rss/nyt/US.xml", size=16)
schl = (
    ("*/2 * * * *", rss_prgm),
    ("*/1 * * * *", clock_prgm),
)

asmlr = assembler.Assembler(controller=ctrl)
schdlr = scheduler.Scheduler(asmlr, schl)

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
