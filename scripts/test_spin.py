#! /usr/bin/python3

import os
import psutil
import sys
import time

sys.path.append("/home/charles/Projects/nixie")

from pyxielib import animation, assembler, controller, decoder, tube_manager
from pyxielib import animation_library as animationlib


c = controller.TerminalController(clear_screen=True)
a1 = animationlib.makeSpinAnimation(num_tubes=3, rate=10, loop=True)
#a2 = animationlib.makeTextAnimation("Done")
a3 = animation.LoopedTubeAnimation([animationlib.makeDoubleSpinSequence(10, offset=x) for x in range(12)])
#aa = animation.LoopedTubeAnimation([animationlib.makeSpinTubeSequence(10, reverse=(x%2), offset=(x%2)) for x in range(12)])
#aa = animation.LoopedTubeAnimation([animationlib.makeLoopSequence(5, length=2)])
#aa = animation.TubeAnimation([animationlib.makeLoopSequence(5, length=2)*1.5])

#aa = animation.LoopedTubeAnimation([
#aa = animation.TubeAnimation.makeAndEqualize([
#aa = animation.LoopedTubeAnimation.makeAndNormalize([
#    animationlib.makeSpinTubeSequence(5),
#    animationlib.makeSpinTubeSequence(10, reverse=True),
#    animationlib.makeDoubleSpinSequence(2),
#    animationlib.makeDoubleSpinSequence(7, reverse=True),
#    animationlib.makeLoopSequence(5),
#])
#], loops=5)

aa = animation.ComboAnimation([a1, a3])

asmlr = assembler.Assembler(controller=c)
asmlr.start()
asmlr.setAnimation(aa)
#process = psutil.Process(os.getpid())
#print(process.memory_info()[0] >> 20)

try:
    while not asmlr.animationDone():
        time.sleep(0.1)
except KeyboardInterrupt:
    print("User required exit")

asmlr.stop()
