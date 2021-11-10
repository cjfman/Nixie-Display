## pylint: disable=unused-import,wildcard-import,unused-wildcard-import,wildcard-import
from typing import List, Sequence, Tuple

from pyxielib.animation import *


def makeTextAnimation(text):
    """Create an animation set from a text string"""
    return FullFrameAnimation([(0, [TextFrame(x) for x in text])])


def makeTextSequence(msgs:Sequence[str], delay:float, *, looped=False):
    """Create an animation set from multiple text strings"""
    frames = [FullFrame([TextFrame(x) for x in msg]) for msg in msgs]
    if looped:
        return LoopedFullFrameAnimation.makeTimed(frames, delay=delay)

    return FullFrameAnimation.makeTimed(frames, delay=delay)


def makeSpinTubeSequence(rate):
    """Create a spin sequence"""
    frames = [HexFrame(0x1 << x) for x in range(7, 14)] + [HexFrame(0x1 << 6)]
    return TubeSequence.makeTimed(frames, rate)


def makeSpinAnimation(*, rate=3, num_tubes=1, loop=True):
    """Create a spin animation"""
    seq = makeSpinTubeSequence(rate)
    animations = [seq.clone() for x in range(num_tubes)]
    if loop:
        return LoopedTubeAnimation(animations)

    return TubeAnimation(animations)
