## pylint: disable=unused-import,wildcard-import,unused-wildcard-import,wildcard-import
from typing import List, Sequence, Tuple

from pyxielib.frames import *
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


def makeSpinAnimation(*, rate=3, num_tubes=1, offset=0, reverse=False, loop=False) -> TubeAnimation:
    """Create a spin animation"""
    seq = makeSpinTubeSequence(rate, offset, reverse)
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


class ProgressSpinner(FullFrameAnimation):
    """One fill-cycle spinner: ``label`` text followed by a single tube whose
    ring segments accumulate (OR together) one per frame, filling up over the
    cycle — mirroring the pip_nixie download spinner.

    It is deliberately a *one-shot* animation (extends FullFrameAnimation, not
    the Looped variant) so that ``done()`` reports True after a single fill.
    A polling owner (e.g. BTAddItem) relies on that: the scheduler only re-polls
    the user menu when the active animation reports done, so a never-done loop
    would freeze the menu's state machine. Recreate the spinner each cycle to
    loop it — which also drives one poll per cycle.

    Equality is by identity so each freshly built spinner counts as a *new*
    animation (Program.update compares by ``==``); otherwise two same-label
    spinners would be equal and the replacement would be deduplicated, leaving
    the finished spinner frozen on its last frame instead of restarting.
    """
    ## Outer-ring segments in draw order, cumulatively OR'd one per frame.
    ## Mirrors pip_nixie's SPINNER_SEGS (the leading 0x0080 repeat gives the
    ## first segment a double beat before the ring starts filling).
    _SEGS = [0x0080, 0x0100, 0x0200, 0x0400, 0x0800, 0x1000, 0x2000, 0x0040]

    def __init__(self, label="", rate=0.1, num_tubes=16):
        super().__init__([(rate, f) for f in self._make_frames(label, num_tubes)])

    @classmethod
    def _make_frames(cls, label, num_tubes) -> List[FullFrame]:
        label_frames = textToFrames(label)
        tail = [HexFrame(0)] * max(0, num_tubes - 1 - len(label_frames))
        frames = []
        bitmap = 0
        for seg in cls._SEGS:
            bitmap |= seg
            frames.append(FullFrame(label_frames + [HexFrame(bitmap)] + tail))
        return frames

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)


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
