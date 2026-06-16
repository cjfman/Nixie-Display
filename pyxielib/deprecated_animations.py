"""
Deprecated animation helpers.

None of these are used by the live display path anymore. The animation DSL
(pyxielib/animation_file.py + pyxielib/animation_sandbox.py) superseded the
hand-rolled "stitch tube sequences together with LCM timing" machinery they
were written for. They are kept here for reference and for the manual smoke
script scripts/tests/deprecated_test_spin.py.

  - rgcd / mulAll / lcm : integer GCD/LCM helpers; only ever existed to
                          time-normalize looping tube animations (makeAndNormalize).
  - makeAndEqualize     : was TubeAnimation.makeAndEqualize (classmethod).
  - makeAndNormalize    : was LoopedTubeAnimation.makeAndNormalize (classmethod).
  - ComboAnimation      : concatenates the tubes of several animations.
"""

import math
from typing import Sequence

from pyxielib.animation import Animation, LoopedTubeAnimation, TubeAnimation, TubeSequence


def rgcd(nums):
    """Recursive math.gcd"""
    if not nums:
        raise ValueError("rgcd cannot take an empty list")
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return math.gcd(nums[0], nums[1])

    return math.gcd(nums[0], rgcd(nums[1:]))


def mulAll(nums):
    q = 1
    for x in nums:
        q *= x

    return q


def lcm(nums):
    return mulAll(nums) // rgcd(nums)


def makeAndEqualize(tubes: Sequence[TubeSequence], *, extend=1):
    """Make a TubeAnimation and make all tube sequences the same length"""
    max_len = max(map(lambda x: x.length(), tubes)) * extend
    return TubeAnimation([x*(max_len/x.length()) for x in tubes])


def makeAndNormalize(tubes: Sequence[TubeSequence]):
    """Make a LoopedTubeAnimation and make all tube sequences loop at the same time"""
    ## Normalize with a time precision of 100ms
    coef = lcm(list(map(lambda x: int(x.length()*10), tubes)))/10
    return LoopedTubeAnimation([x*coef for x in tubes])


class ComboAnimation(Animation):
    """Animation made by concatinating the tubes of other animations"""
    def __init__(self, animations: Sequence[Animation]):
        Animation.__init__(self)
        self.animations: Sequence[Animation] = list(animations)

    def reset(self):
        for ani in self.animations:
            ani.reset()

    def tubeCount(self):
        total = 0
        for ani in self.animations:
            total += ani.tubeCount()

    def getCode(self):
        return ''.join(map(lambda ani: ani.getCode(), self.animations))

    def updateFrameSet(self):
        """
        Update the frame set for every animation based upon the current time.
        Return True if any animations are updated
        """
        return any(map(lambda ani: ani.updateFrameSet(), self.animations))

    def done(self):
        return all(map(lambda ani: ani.done(), self.animations))
