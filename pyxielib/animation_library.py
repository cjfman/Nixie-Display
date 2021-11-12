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


def makeSpinTubeSequence(rate, offset=0, reverse=False):
    """Create a spin sequence"""
    frames = [HexFrame(0x1 << x) for x in range(7, 14)] + [HexFrame(0x1 << 6)]
    if offset:
        offset = offset % len(frames)
        frames = frames[offset:] + frames[:offset]
    if reverse:
        frames.reverse()

    return TubeSequence.makeTimed(frames, rate)


def makeSpinAnimation(*, rate=3, num_tubes=1, offset=0, loop=True):
    """Create a spin animation"""
    seq = makeSpinTubeSequence(rate, offset=0)
    animations = [seq.clone() for x in range(num_tubes)]
    if loop:
        return LoopedTubeAnimation(animations)

    return TubeAnimation(animations)


def makeDoubleSpinTubeSequence(rate, offset=0, reverse=False):
    """Create a spin sequence"""
    frames_1 = [0x1 << x for x in range(7, 11)]
    frames_2 = [0x1 << x for x in range(11, 14)] + [0x1 << 6]
    frames = [HexFrame(x | y) for x, y in zip(frames_1, frames_2)]
    if offset:
        offset = offset % len(frames)
        frames = frames[offset:] + frames[:offset]
    if reverse:
        frames.reverse()

    return TubeSequence.makeTimed(frames, rate)
