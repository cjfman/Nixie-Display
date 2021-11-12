import time

from typing import List, Sequence, Tuple

from pyxielib import tube_manager as tm
from pyxielib.pyxieutil import PyxieError, PyxieUnimplementedError


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

    def clone(self):
        return FullFrame(self.frames[:])

    def __eq__(self, other):
        return (self.frames == other.frames)

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()


TimeFrame = Tuple[float, Frame]
TimeFullFrame = Tuple[float, FullFrame]
FrameSequence = Sequence[Frame]


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
        if isinstance(x, int):
            ## Multiply list
            frames = self.frames*x
            return TubeSequence(self.frames*x)
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
        """The last frame as loaded"""
        raise PyxieUnimplementedError(self)


class TubeAnimation(Animation):
    def __init__(self, tubes: Sequence[TubeSequence]):
        Animation.__init__(self)
        self.tubes: Sequence[TubeSequence] = tubes
        self.current_frame_set: List[Frame] = [Frame()]*len(tubes)

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
        frames = self.currentFrameSet()
        return ''. join([frame.getCode() for frame in frames])

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

        return self.current_frame_set[:] ## Make copy

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

    def __eq__(self, other):
        if other is None:
            return False

        return (self.tubes == other.tubes)

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()


class LoopedTubeAnimation(TubeAnimation):
    def __init__(self, animations: Sequence[TubeSequence], delay: float=0):
        TubeAnimation.__init__(self, animations)
        self.delay = delay

    def done(self):
        """The last frame has loaded. Always false for LoopedTubeAnimation"""
        return False

    def loopOver(self):
        """Reached the last frame of the loop"""
        return TubeAnimation.done(self)

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        update = TubeAnimation.updateFrameSet(self)
        if update:
            return update

        if self.loopOver():
            self.reset()
            return TubeAnimation.updateFrameSet(self)

        return None

    def clone(self):
        return LoopedTubeAnimation(self.tubes[:], self.delay)

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
        frames = self.currentFrame()
        return ''. join([frame.getCode() for frame in frames])

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
        now = time.time()
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
            return TubeSequence(self.frames*x)
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
