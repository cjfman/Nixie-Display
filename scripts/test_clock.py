#! /usr/bin/python3
##pylint: disable=wrong-import-position

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import assembler, controller, program


#c = controller.TerminalController(clear_screen=True)
#p = program.ClockProgram()
#a = animation.makeTextAnimation(p.dateTimeAsNumbers())
#asmlr = assembler.Assembler(controller=c)
#asmlr.start()
#asmlr.setAnimation(a)

#ctrl = controller.TerminalController(clear_screen=True)
print("Opening connection to Nixie Control Board")
ctrl = controller.SerialController('/dev/ttyACM0', debug=True)
print("Connection established")
asmlr = assembler.Assembler(controller=ctrl)
prgm = program.ClockProgram(asmlr, flash=True)

print("Starting program")
prgm.run()
asmlr.start()
time.sleep(1)

try:
    #while not asmlr.animationDone():
    while True:
        if not asmlr.isRunning():
            print("Assembler stopped unexpectedly")
            break
        if not prgm.isRunning():
            print("Program stopped unexpectedly")
            break
        time.sleep(0.1)
except KeyboardInterrupt:
    print("User requested exit")

asmlr.stop()
prgm.stop()
