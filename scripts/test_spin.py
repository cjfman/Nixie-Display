#! /usr/bin/python3

import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import animation, assembler, controller, decoder, tube_manager
from pyxielib import animation_library as animationlib


c = controller.TerminalController(clear_screen=True)
#a1 = animationlib.makeSpinAnimation(num_tubes=3, rate=10, loop=True)
#a2 = animationlib.makeTextAnimation("Done")
#aa = animation.LoopedTubeAnimation([animationlib.makeDoubleSpinSequence(10, offset=x) for x in range(12)])
#aa = animation.LoopedTubeAnimation([animationlib.makeSpinTubeSequence(10, reverse=(x%2), offset=(x%2)) for x in range(12)])
#aa = animation.LoopedTubeAnimation([animationlib.makeLoopSequence(5, length=2)])
aa = animation.TubeAnimation([animationlib.makeLoopSequence(5, length=2)*1.5])
asmlr = assembler.Assembler(controller=c)
asmlr.start()
asmlr.setAnimation(aa)

try:
    while not asmlr.animationDone():
        time.sleep(0.1)
except KeyboardInterrupt:
    print("User required exit")

asmlr.stop()
