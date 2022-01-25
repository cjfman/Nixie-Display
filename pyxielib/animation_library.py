## pylint: disable=unused-import,wildcard-import,unused-wildcard-import,wildcard-import
from typing import List, Sequence, Tuple

from pyxielib.animation import *


def makeTextAnimation(text, length=1):
    """Create an animation set from a text string"""
    return FullFrameAnimation([(length, FullFrame(textToFrames(text)))])


def makeTextSequence(msgs:Sequence[str], delay:float, *, looped=False):
    """Create an animation set from multiple text strings"""
    frames = [FullFrame(textToFrames(msg)) for msg in msgs]
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


def makeSpinAnimation(*, rate=3, num_tubes=1, offset=0, loop=True) -> TubeAnimation:
    """Create a spin animation"""
    seq = makeSpinTubeSequence(rate, offset)
    animations = [seq.clone() for x in range(num_tubes)]
    if loop:
        return LoopedTubeAnimation(animations)

    return TubeAnimation(animations)


#def _offsetFrames(frames:FrameSequence, offset) -> FrameSequence:
def _offsetFrames(frames, offset):
    if not offset:
        return list(frames) ## Make copy

    offset = offset % len(frames)
    return frames[offset:] + frames[:offset]


def makeDoubleSpinSequence(rate, *, offset=0, reverse=False) -> TubeSequence:
    """Create a spin sequence"""
    frames_1 = [0x1 << x for x in range(7, 11)]
    frames_2 = [0x1 << x for x in range(11, 14)] + [0x1 << 6]
    frames = [HexFrame(x | y) for x, y in zip(frames_1, frames_2)]
    if offset:
        frames = _offsetFrames(frames, offset)
    if reverse:
        frames.reverse()

    return TubeSequence.makeTimed(frames, rate)


def makeLoopSequence(rate, *, length=1, offset=0, reverse=False) -> TubeSequence:
    """Create a loop sequence"""
    length = max(1, min(5, length))
    code = (0x1 << length) - 1
    #frames = [HexFrame(code << x) for x in range(7-length)]
    frames = []
    for x in range(6):
        frame_code = code << x
        code_1 = frame_code & 0x3F
        code_2 = (frame_code & 0xFFC0) >> 6
        frames.append(HexFrame(code_1 | code_2))

    if offset:
        frames = _offsetFrames(frames, offset)
    if reverse:
        frames.reverse()

    return TubeSequence.makeTimed(frames, rate)
