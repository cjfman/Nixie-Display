import logging
import math
import re
import time

from copy import copy
from typing import Dict, List, Sequence, Tuple

from pyxielib import tube_manager as tm
from pyxielib.pyxieutil import PyxieError, PyxieUnimplementedError, strToInt

logger = logging.getLogger(__name__)


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


def escapeText(txt, overrides:Dict[str, str]=None, regex_rep:Dict[str, str]=None):
    txt = txt.upper()
    replace = {
        '°': '*',
        '(': '<',
        ')': '>',
        '?': ' !',
    }
    if overrides is not None:
        replace.update(overrides)
    for old, new in replace.items():
        txt = txt.replace(old, new)

    if regex_rep is not None:
        for old, new in regex_rep.items():
            txt = re.sub(old, new, txt, flags=re.IGNORECASE)

    return txt


class PixieAnimationError(PyxieError):
    pass


class Frame:
    """A representation of a tube at a single point in time"""
    def __init__(self, code=' '):
        self.code = code

    def getCode(self):
        return self.code

    def decode(self):
        """Get the bitmap for an animation"""
        try:
            return tm.cmdDecodePrint(self.getCode())[0]
        except:
            raise PixieAnimationError(f"Failed to decode '{self.getCode()}'")

    def overlay(self, other):
        """Overlay a frame on top of another"""
        if self.code == ' ':
            return copy(other)
        if other.code == ' ':
            return copy(self)

        raise ValueError("Only overlaying of HexFrames is supported at this time")

    def copy(self):
        return Frame(self.code)

    def __or__(self, other):
        """Concatenate tubes: a frame joined with another frame becomes a FullFrame"""
        if isinstance(other, FullFrame):
            return FullFrame([self] + other.getFrames())
        if isinstance(other, Frame):
            return FullFrame([self, other])
        return NotImplemented

    def __str__(self):
        return self.code

    def __repr__(self):
        return self.code

    def __eq__(self, other):
        return (self.code == other.code)


class HexFrame(Frame):
    """A frame from a hex code"""
    def __init__(self, hex_code=0x0):
        Frame.__init__(self, '{' + hex(0xFFFF & hex_code) + '}')
        self.hex_code = hex_code

    def overlay(self, other):
        """Overlay a frame on top of another"""
        if self.code == ' ':
            return copy(other)
        if other.code == ' ':
            return copy(self)

        if not isinstance(other, HexFrame):
            raise ValueError("Only overlaying of HexFrames is supported at this time")

        return HexFrame(self.hex_code | other.hex_code)


class TextFrame(Frame):
    """
    A frame from a printable character
    Must either be a single character, optionally followed by a ':' or '!'
    ':' for colon
    '!' for underline
    """
    def __init__(self, text, colon=False, underline=False):
        if len(text) == 2 and text[1] == ':':
            colon = True
        elif len(text) > 1:
            raise PixieAnimationError("TextFrame cannot only take string length 1")

        code = text[0]
        if colon:
            code += ':'
        if underline:
            code += '!'

        Frame.__init__(self,code)

    def setColon(self):
        if self.code and self.code[-1] != ':':
            self.code += ':'

    def setUnderline(self):
        if self.code and self.code[-1] != '!':
            self.code += '!'


class FullFrame():
    """A representation of a tube array at a single point in time"""
    def __init__(self, frames: Sequence[Frame]=None):
        self.frames = list(frames or [])

    def tubeCount(self):
        """Number of tubes in this frame"""
        return len(self.frames)

    def getFrames(self):
        """Get the frames"""
        return self.frames[:] ## Make copy

    def overlay(self, other):
        base = None
        overlay = None
        if len(self) > len(other):
            base = self.frames.copy()
            overlay = other.frames
        else:
            base = other.frames.copy()
            overlay = self.frames

        ## pylint: disable=consider-using-enumerate
        for i in range(len(overlay)):
            base[i] = base[i].overlay(overlay[i])

        return FullFrame(base)

    def clone(self):
        return FullFrame(self.frames[:])

    def __or__(self, other):
        """Concatenate tubes with another FullFrame or Frame"""
        if isinstance(other, FullFrame):
            return FullFrame(self.frames + other.getFrames())
        if isinstance(other, Frame):
            return FullFrame(self.frames + [other])
        return NotImplemented

    def __eq__(self, other):
        return (self.frames == other.frames)

    def __len__(self):
        return len(self.frames)

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()

    def __str__(self):
        return ','.join([str(x) for x in self.frames])

    def __repr__(self):
        return "[FullFrame " + str(self) + "]"


def textToFrames(text):
    ## I'd love to do a list comprehension
    ## but we need to fix colons
    ## return [TextFrame(x) for x in text]
    frames = []
    for x in text:
        if x in [':', '!'] and not frames:
            raise PixieAnimationError("Cannot start a text animation with a command character")

        if x == ':':
            frames[-1].setColon()
        elif x == '!':
            frames[-1].setUnderline()
        else:
            frames.append(TextFrame(x))

    return frames


TimeFrame = Tuple[float, Frame]
TimeFullFrame = Tuple[float, FullFrame]
FrameSequence = Sequence[Frame]


def _frameSpans(timed_items):
    """Convert [(delay, item), ...] into cumulative [(start, end, item), ...] spans"""
    spans = []
    clock = 0.0
    for delay, item in timed_items:
        spans.append((clock, clock + delay, item))
        clock += delay

    return spans


def _spanValueAt(spans, when):
    """Return the item whose [start, end) span contains 'when', else None"""
    for start, end, item in spans:
        if start <= when < end:
            return item

    return None


def _padTubes(full_frame, width):
    """The tube frames of a FullFrame (or None), blank-padded out to 'width'"""
    frames = full_frame.getFrames() if full_frame is not None else []
    if len(frames) < width:
        frames = frames + [Frame()] * (width - len(frames))

    return frames


def concatFullFrameRows(left_rows, right_rows):
    """Join two lists of FullFrame rows tube-wise, row by row (blank-padded)"""
    left_width = max((row.tubeCount() for row in left_rows), default=0)
    right_width = max((row.tubeCount() for row in right_rows), default=0)
    rows = []
    for index in range(max(len(left_rows), len(right_rows))):
        left = left_rows[index] if index < len(left_rows) else None
        right = right_rows[index] if index < len(right_rows) else None
        rows.append(FullFrame(_padTubes(left, left_width) + _padTubes(right, right_width)))

    return rows


def concatFullFrameTimelines(left_frames, right_frames):
    """Merge two [(delay, FullFrame)] timelines, joining tubes over a shared timeline"""
    left_spans = _frameSpans(left_frames)
    right_spans = _frameSpans(right_frames)
    left_width = max((ff.tubeCount() for _, _, ff in left_spans), default=0)
    right_width = max((ff.tubeCount() for _, _, ff in right_spans), default=0)
    boundaries = sorted({0.0} | {e for _, e, _ in left_spans} | {e for _, e, _ in right_spans})
    frames = []
    for start, end in zip(boundaries, boundaries[1:]):
        left = _spanValueAt(left_spans, start)
        right = _spanValueAt(right_spans, start)
        tubes = _padTubes(left, left_width) + _padTubes(right, right_width)
        frames.append((end - start, FullFrame(tubes)))

    return frames


class TubeSequence:
    """
    A sequence of timed frames for a single tube
    Each frame should be paired with an endtime in a tuple
    The end time should be relative from the start time, which
    should be treated as zero until the animation is run
    Ex: (<endtime as float>, Frame())
    """
    def __init__(self, frames: Sequence[TimeFrame]=None):
        self.started = False
        self.start_time: float = time.time()
        self.frames: List[TimeFrame] = list(frames or []) ## Make Copy
        self.frame_index = 0

    @classmethod
    def makeBlank(cls, length: float=0):
        """Make a blank tube animation of a certain length"""
        return cls([(length, Frame())])

    @classmethod
    def makeTimed(cls, frames:Sequence[Frame], rate: int=1, *, delay: float=0, **kwargs):
        """Make an animation with evenly spaced frames"""
        if not frames:
            raise PixieAnimationError("Cannot make a timed animation without at least one frame")

        ## Delay overrides rate
        if not delay:
            delay = 1 / rate

        time_frames: List[TimeFrame] = [(delay, frame) for frame in frames]
        return cls(time_frames, **kwargs)

    def reset(self):
        """Reset the animation"""
        self.frame_index = 0
        self.started = False

    def frameCount(self) -> int:
        """Total frame count"""
        return len(self.frames)

    def remainingFrames(self) -> int:
        """Number of frames from now to end"""
        return max(0, len(self.frames) - self.frame_index)

    def done(self) -> bool:
        if not self.frames:
            return True

        return not self.remainingFrames()

    def length(self) -> float:
        """Time length of animation"""
        if not self.frames:
            return 0

        return sum(map(lambda x: x[0], self.frames))

    def currentFrame(self):
        if not self.remainingFrames():
            return None

        return self.frames[self.frame_index][1]

    def framesThroughTime(self, length:float):
        """Return all the frames that would display in 'length' time"""
        lapsed = 0
        index = 0
        for t, _ in self.frames:
            if lapsed <= length:
                lapsed += t
                index += 1

        return self.frames[:index + 1]

    def popFrame(self) -> Frame:
        """Get the next frame and adjust index"""
        if not self.remainingFrames():
            return None

        now = time.time()
        ## If not started, set start time and return first frame
        if not self.started:
            self.started = True
            self.start_time = now
            return self.frames[0][1]

        ## Check to see if current frame has passed
        length, _ = self.frames[self.frame_index]
        if now < self.start_time + length:
            return None

        ## Get next frame
        self.frame_index += 1
        if len(self.frames) <= self.frame_index:
            return None

        self.start_time = now
        return self.frames[self.frame_index][1]

    def __add__(self, other):
        return TubeSequence(self.frames + other.frames)

    def __iadd__(self, other):
        self.frames += other.frames
        self.reset()
        return self

    def _mul_helper(self, x:int):
        frames = None
        if x == 1:
            frames = self.frames[:]
        elif x == 0:
            frames = []
        elif isinstance(x, int):
            ## Multiply list
            frames = self.frames*x
        elif isinstance(x, float):
            ## Mulitply list by integer part
            frames = self.frames*int(x)
            f = x - int(x)
            frames += self.framesThroughTime(f*self.length())
        else:
            name = self.__class__.__name__
            raise PixieAnimationError(f"{name} must be multiplied by int")

        return frames

    def __mul__(self, x:int):
        return TubeSequence(self._mul_helper(x))

    def __imul__(self, x:int):
        if not isinstance(x, int):
            name = self.__class__.__name__
            raise PixieAnimationError(f"{name} must be multiplied by int")

        self.frames = self._mul_helper(x)
        self.reset()
        return self

    def __or__(self, other):
        """Concatenate tubes: two single-tube sequences become a list of FullFrame rows"""
        if not isinstance(other, TubeSequence):
            return NotImplemented

        left_rows = [FullFrame([frame]) for _, frame in self.frames]
        right_rows = [FullFrame([frame]) for _, frame in other.frames]
        return concatFullFrameRows(left_rows, right_rows)

    def clone(self):
        return TubeSequence(self.frames[:])

    def __eq__(self, other):
        """Equals boolean. Only considers frames and time diffs"""
        return (self.frames == other.frames)

    def __str__(self):
        return f"Start time {self.start_time}: " + str(self.frames)

    def __repr__(self):
        return str(self)

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()


class Animation:
    def __init__(self):
        pass

    def reset(self):
        """Reset the start time of the first frame"""
        raise PyxieUnimplementedError(self)

    def length(self):
        """Time length of the animation set"""
        raise PyxieUnimplementedError(self)

    def tubeCount(self):
        """Get the number of tubes supported by this animation"""
        raise PyxieUnimplementedError(self)

    def getCode(self):
        """Get the code to send to the decoder"""
        raise PyxieUnimplementedError(self)

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        raise PyxieUnimplementedError(self)

    def done(self):
        """The last frame has loaded"""
        raise PyxieUnimplementedError(self)

class EmtpyAnimation:
    def __init__(self):
        Animation.__init__(self)

    def reset(self):
        """Reset the start time of the first frame"""
        pass

    def length(self):
        """Time length of the animation set"""
        return 0

    def tubeCount(self):
        """Get the number of tubes supported by this animation"""
        return 0

    def getCode(self):
        """Get the code to send to the decoder"""
        return None

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        return False

    def done(self):
        """The last frame has loaded"""
        return True

#    @staticmethod
#    def _makeCode(frames, start=0, end=None):
#        if end is None:
#            end = len(frames)
#        if start >= len(frames):
#            raise PixieAnimationError("Cannot start index past last animation tube")
#        if start > end:
#            raise PixieAnimationError("'end' index is smaller than 'start'")
#
#        return ''. join([frame.getCode() for frame in frames[start:end]])


class TubeAnimation(Animation):
    def __init__(self, tubes: Sequence[TubeSequence]):
        Animation.__init__(self)
        self.tubes: Sequence[TubeSequence] = tubes
        self.current_frame_set: List[Frame] = [Frame()]*len(tubes)

    @classmethod
    def makeAndEqualize(cls, tubes: Sequence[TubeSequence], *, extend=1):
        """Make a TubeAnimation and make all tube sequences the same length"""
        max_len = max(map(lambda x: x.length(), tubes)) * extend
        return cls([x*(max_len/x.length()) for x in tubes])

    def reset(self):
        """Reset the start time of the first frame"""
        for animation in self.tubes:
            animation.reset()

    def length(self):
        """Time length of the animation set. Equal to the longest animation"""
        return max(map(lambda x: x.length(), self.tubes))

    def tubeCount(self):
        """Get the number of tubes supported by this animation set"""
        return len(self.current_frame_set)

    def currentFrameSet(self) -> Sequence[Frame]:
        """Get the currently assembled frame"""
        return self.current_frame_set[:]

    def getCode(self):
        """Get the code to send to the decoder"""
        #return self._makeCode(self.currentFrameSet(), start, end)
        return ''.join(map(lambda x: x.getCode(), self.currentFrameSet()))

    @staticmethod
    def equalize(tubes):
        """Make all tubes the same length"""
        longest = max(map(lambda x: x.length(), tubes))
        for animation in tubes:
            diff = longest - animation.length()
            if diff:
                animation += TubeSequence.makeBlank(diff)

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        updated = False
        ## Iterate over each tube animation and check for an update
        for i, animation in enumerate(self.tubes):
            frame = animation.popFrame()
            if frame is not None:
                self.current_frame_set[i] = frame
                updated = True

        if not updated:
            return None

        return True #self.current_frame_set[:] ## Make copy

    def done(self):
        """The last frame as loaded"""
        return all([tube.done() for tube in self.tubes])

    def clone(self):
        return TubeAnimation(self.tubes[:])

    def __add__(self, other):
        ## Make copies
        a1 = self.tubes[:]
        a2 = other.tubes[:]
        self.equalize(a1)
        ## Fix difference in number of tubes
        if len(a2) > len(a1):
            diff = len(a2) - len(a1)
            longest = max(map(lambda x: x.length(), self.tubes))
            a1 += [TubeSequence.makeBlank(longest) for x in range(diff)]
        elif len(a1) > len(a2):
            diff = len(a1) - len(a2)
            longest = max(map(lambda x: x.length(), a2))
            a2 += [TubeSequence()]*diff

        new_a = [x + y for x, y in zip(a1, a2)]
        return TubeAnimation(new_a)

    def __iadd__(self, other):
        ## Fix difference in number of tubes
        self.equalize(self.tubes)
        tubes = other.tubes[:] ## make copy
        if len(tubes) > len(self.tubes):
            ## "other" has more tubes
            diff = len(tubes) - len(self.tubes)
            longest = max(map(lambda x: x.length(), self.tubes))
            self.tubes += [TubeSequence.makeBlank(longest)]*diff
        elif len(self.tubes) > len(tubes):
            ## "self" has more tubes
            diff = len(self.tubes) - len(tubes)
            longest = max(map(lambda x: x.length(), tubes))
            tubes += [TubeSequence(longest)]*diff

        for i, x in enumerate(tubes):
            self.tubes[i] += x

    def __mul__(self, x:int):
        return TubeAnimation([tube * x for tube in self.tubes])

    def toFullFrameAnimation(self):
        """Merge the per-tube sequences onto a shared timeline of full frames"""
        timelines = [_frameSpans(tube.frames) for tube in self.tubes]
        boundaries = sorted({0.0} | {end for timeline in timelines for _, end, _ in timeline})
        frames = []
        for start, end in zip(boundaries, boundaries[1:]):
            tubes = [_spanValueAt(timeline, start) or Frame() for timeline in timelines]
            frames.append((end - start, FullFrame(tubes)))

        return FullFrameAnimation(frames)

    def __or__(self, other):
        """Concatenate tubes with another tube or full-frame animation"""
        return self.toFullFrameAnimation() | other

    def __eq__(self, other):
        if other is None:
            return False

        return (self.tubes == other.tubes)

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()


class LoopedTubeAnimation(TubeAnimation):
    def __init__(self, animations: Sequence[TubeSequence], loops=None):
        TubeAnimation.__init__(self, animations)
        self.loops = loops
        self.loops_done = 0

    @classmethod
    def makeAndNormalize(cls, tubes: Sequence[TubeSequence]):
        """Make a TubeAnimation and make all tube sequences loop at the same time"""
        ## Normalize with a time precision of 100ms
        coef = lcm(list(map(lambda x: int(x.length()*10), tubes)))/10
        return cls([x*coef for x in tubes])

    def reset(self):
        TubeAnimation.reset(self)
        self.loops_done = 0

    def done(self):
        """The last frame has loaded. Always false for LoopedTubeAnimation"""
        return (self.loops is not None and self.loops_done >= self.loops)

    def loopOver(self):
        """Reached the last frame of the loop"""
        return TubeAnimation.done(self)

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        update = TubeAnimation.updateFrameSet(self)
        if update:
            return update

        if self.loopOver():
            self.loops_done += 1
            if not self.done():
                TubeAnimation.reset(self)
                return TubeAnimation.updateFrameSet(self)

        return None

    def clone(self):
        return LoopedTubeAnimation(self.tubes[:])

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()


class FullFrameAnimation(Animation):
    def __init__(self, frames:Sequence[TimeFullFrame] = None):
        """A sequence of timed full frames"""
        Animation.__init__(self)
        self.frames: Sequence[TimeFullFrame] = list(frames or [(0, [])])
        self.start_time: float = time.time()
        self.frame_index = 0
        self.started = False
        self.current_frame = self.frames[0]
        self.num_tubes = max(map(lambda x: x[1].tubeCount(), self.frames))

    @classmethod
    def makeTimed(cls, frames: Sequence[FullFrame], rate: int=1, *, delay: float=0, **kwargs):
        """Make an animation with evenly spaced frames"""
        if not frames:
            raise PixieAnimationError("Cannot make a timed animation without at least one frame")

        ## Delay overrides rate
        if not delay:
            delay = 1 / rate

        ## Calculate time passed
        time_frames: List[TimeFullFrame] = [(delay, frame) for frame in frames]
        return cls(time_frames, **kwargs)

    def reset(self):
        """Reset the animation"""
        self.started = False
        self.frame_index = 0
        self.start_time = time.time()

    def frameCount(self):
        """Total frame count"""
        return len(self.frames)

    def length(self):
        """Time length of the animation set"""
        return self.frames[0]

    def tubeCount(self):
        """Get the number of tubes supported by this animation"""
        return self.num_tubes

    def currentFrame(self) -> Sequence[FullFrame]:
        """Get the current frame"""
        return list(self.current_frame.getFrames())

    def getCode(self):
        """Get the code to send to the decoder"""
        #return self._makeCode(self.currentFrame(), start, end)
        return ''.join(map(lambda x: x.getCode(), self.currentFrame()))

    def framesThroughTime(self, length:float):
        """Return all the frames that would display in 'length' time"""
        lapsed = 0
        index = 0
        for t, _ in self.frames:
            if lapsed <= length:
                lapsed += t
                index += 1

        return self.frames[:index + 1]

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        ## This should never happen, but let's be safe
        if self.frame_index >= len(self.frames):
            return False

        now = time.time()
        ## Force set the first frame and set the start time
        if not self.started:
            self.started = True
            self.start_time = now
            self.current_frame = self.frames[0][1]
            return True

        ## See if current frame has passed
        length, _ = self.frames[self.frame_index]
        if now < self.start_time + length:
            return False

        self.frame_index += 1
        self.start_time = now
        if self.frame_index >= len(self.frames):
            return False

        self.current_frame = self.frames[self.frame_index][1]
        return True

    def done(self):
        return (self.frame_index == len(self.frames))

    def clone(self):
        return FullFrameAnimation(self.frames[:])

    def __eq__(self, other):
        if other is None:
            return False

        return (self.frames == other.frames)

    def __add__(self, other):
        ## Make copy
        frames1 = list(self.frames)

        ## Get difference between last two frames. Default to 1
        delay = 0
        if len(self.frames) >= 2:
            delay = self.frames[-1][0] - self.frames[-2][0]

        ## Change time offsets
        offset = 0
        if self.frames:
            offset = self.frames[-1][0] + delay

        frames2 = [(x + offset, y) for x, y in other.frames]
        return FullFrameAnimation(frames1 + frames2)

    def __iadd__(self, other):
        ## Get difference between last two frames. Default to 1
        delay = 1
        if len(self.frames) >= 2:
            delay = self.frames[-1][0] - self.frames[-2][0]

        ## Change time offsets
        offset = 0
        if self.frames:
            offset = self.frames[-1][0] + delay

        new_frames = [(x + offset, y) for x, y in other.frames]

        ## Add frames
        self.frames += new_frames
        self.reset()
        return self

    def _mul_helper(self, x:int):
        frames = None
        if isinstance(x, int):
            ## Multiply list
            frames = self.frames*x
        elif isinstance(x, float):
            ## Mulitply list by integer part
            frames = self.frames*int(x)
            f = x - int(x)
            frames += self.framesThroughTime(f*self.length())
        else:
            name = self.__class__.__name__
            raise PixieAnimationError(f"{name} must be multiplied by int")

        return frames

    def __mul__(self, x:int):
        return FullFrameAnimation(self._mul_helper(x))

    def __imul__(self, x:int):
        if not isinstance(x, int):
            name = self.__class__.__name__
            raise PixieAnimationError(f"{name} must be multiplied by int")

        self.frames = self._mul_helper(x)
        self.reset()
        return self

    def __or__(self, other):
        """Concatenate tubes with another full-frame (or tube) animation"""
        if isinstance(other, TubeAnimation):
            other = other.toFullFrameAnimation()
        if not isinstance(other, FullFrameAnimation):
            return NotImplemented
        return FullFrameAnimation(concatFullFrameTimelines(self.frames, other.frames))

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()


class LoopedFullFrameAnimation(FullFrameAnimation):
    def __init__(self, frames:Sequence[TimeFullFrame], delay: float=0):
        FullFrameAnimation.__init__(self, frames)
        self.delay = delay
        self.last_update = time.time()

    @classmethod
    def makeTimed(cls, frames: Sequence[FullFrame], rate: int=1, *, delay: float=0, **kwargs):
        """Make an animation with evenly spaced frames"""
        if not frames:
            raise PixieAnimationError("Cannot make a timed animation without at least one frame")

        ## Delay overrides rate
        if not delay:
            delay = 1 / rate

        ## Calculate time passed
        time_frames: List[TimeFullFrame] = [(delay, frame) for frame in frames]
        return cls(time_frames, delay, **kwargs)

    def done(self):
        """The last frame has loaded. Always false for LoopedTubeAnimation"""
        return False

    def loopOver(self):
        """Reached the last frame of the loop"""
        return FullFrameAnimation.done(self)

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        update = FullFrameAnimation.updateFrameSet(self)
        if update:
            self.last_update = time.time()
            return update

        if self.loopOver() and self.last_update + self.delay < time.time():
            self.reset()
            return FullFrameAnimation.updateFrameSet(self)

        return None

    def clone(self):
        return LoopedFullFrameAnimation(self.frames[:], self.delay)


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


class MarqueeAnimation(Animation):
    def __init__(self, frames:Sequence[Frame], size:int, delay:float=0.5, freeze:float=0):
        super().__init__()
        self.frames     = frames
        self.size       = size
        self.delay      = delay
        self.freeze     = freeze if len(frames) <= size else 0
        self.index      = None
        self.start_time = time.time()

    @classmethod
    def fromText(cls, msg, *args, **kwargs):
        frames = [Frame(x) for x in msg]
        return cls(frames, *args, **kwargs)

    def reset(self):
        self.index = None
        self.start_time = time.time()

    def tubeCount(self):
        return self.size

    def getCode(self):
        """Get the code to send to the decoder"""
        frames = self.frames[self.index:self.index + self.size]
        missing = self.size - len(frames)
        if missing:
            frames += [Frame()]*missing

        return ''.join(map(lambda x: x.getCode(), frames))

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        if self.freeze:
            idx = self.index
            self.index = 0
            return (idx is None)

        ## Shift frames if it's time
        elapsed = time.time() - self.start_time
        next_index = int(elapsed / self.delay)
        if next_index > len(self.frames) or next_index == self.index:
            return False

        self.index = next_index
        return True

    def done(self):
        """The last frame has loaded"""
        if self.freeze:
            return (time.time() - self.start_time > self.freeze)

        ## Return true if the last frame has shifted off the screen
        elapsed = time.time() - self.start_time
        next_index = elapsed // self.delay
        return (next_index >= len(self.frames))
