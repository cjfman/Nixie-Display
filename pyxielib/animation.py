import time

from typing import List, Sequence, Tuple

from pyxielib.pyxieutil import PyxieError, PyxieUnimplementedError


class PixieAnimationError(PyxieError):
    pass


class Frame:
    """A representation of a tube at a single point in time"""
    def __init__(self, code=' '):
        self.code = code

    def getCode(self):
        return self.code

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
        self.code += ':'

    def setUnderline(self):
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
        return list(self.frames) ## Make copy

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


class TubeSequence:
    """
    A sequence of timed frames for a single tube
    Each frame should be paired with an endtime in a tuple
    The end time should be relative from the start time, which
    should be treated as zero until the animation is run
    Ex: (<endtime as float>, Frame())
    """
    def __init__(self, frames: Sequence[TimeFrame]=None):
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

        ## Calculate time passed
        time_frames: List[TimeFrame] = []
        time_passed = 0
        for frame in frames:
            time_frames.append((time_passed, frame))
            time_passed += delay

        ## Add one blank frame
        time_frames.append((time_passed, Frame()))

        return cls(time_frames, **kwargs)

    def resetTime(self):
        """Reset the start time of the first frame"""
        self.start_time = time.time()
        self.frame_index = 0

    def reset(self):
        """Reset the animation"""
        self.resetTime()

    def frameCount(self):
        """Total frame count"""
        return len(self.frames)

    def remainingFrames(self):
        """Number of frames from now to end"""
        return max(0, len(self.frames) - self.frame_index)

    def done(self):
        if not self.frames:
            return True
        elif self.remainingFrames():
            return False

        now = time.time()
        return (self.start_time + self.frames[-1][0] < now)

    def length(self):
        """Time length of animation"""
        if not self.frames:
            return 0

        return self.frames[-1][0]

    def popFrame(self):
        """Get the next frame and adjust index"""
        if not self.remainingFrames():
            return None

        now = time.time()
        next_frame_index = None
        next_frame = None
        ## Get first frame that hasn't passed
        ## Frames should be in order
        for i, time_frame in list(enumerate(self.frames))[self.frame_index:]:
            next_frame_index = i + 1
            if time_frame[0] + self.start_time <= now:
                next_frame = time_frame[1]
                break

        if next_frame is not None and next_frame_index is not None:
            self.frame_index = next_frame_index

        return next_frame

    def __add__(self, other):
        ## Make copies
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
        return TubeSequence(frames1 + frames2)

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

    def resetTime(self):
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
    def __init__(self, animations: Sequence[TubeSequence]):
        Animation.__init__(self)
        self.animations: Sequence[TubeSequence] = animations
        self.current_frame_set: List[Frame] = [Frame()]*len(animations)

    def resetTime(self):
        """Reset the start time of the first frame"""
        for animation in self.animations:
            animation.resetTime()

    def length(self):
        """Time length of the animation set. Equal to the longest animation"""
        return max(map(lambda x: x.length(), self.animations))

    def tubeCount(self):
        """Get the number of tubes supported by this animation set"""
        return len(self.current_frame_set)

    def currentFrameSet(self) -> Sequence[Frame]:
        """Get the currently assembled frame"""
        return list(self.current_frame_set)

    def getCode(self):
        """Get the code to send to the decoder"""
        frames = self.currentFrameSet()
        return ''. join([frame.getCode() for frame in frames])

    @staticmethod
    def equalize(animations):
        """Make all animations the same length"""
        longest = max(map(lambda x: x.length(), animations))
        for animation in animations:
            diff = longest - animation.length()
            if diff:
                animation += TubeSequence.makeBlank(diff)

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        updated = False
        ## Iterate over each tube animation and check for an update
        for i, animation in enumerate(self.animations):
            frame = animation.popFrame()
            if frame is not None:
                self.current_frame_set[i] = frame
                updated = True

        if not updated:
            return None

        return list(self.current_frame_set) ## Make copy

    def done(self):
        """The last frame as loaded"""
        return all([tube.done() for tube in self.animations])

    def clone(self):
        return TubeAnimation(self.animations[:])

    def __add__(self, other):
        ## Make copies
        a1 = list(self.animations)
        a2 = list(other.animations)
        self.equalize(a1)
        ## Fix difference in number of tubes
        if len(a2) > len(a1):
            diff = len(a2) - len(a1)
            longest = max(map(lambda x: x.length(), self.animations))
            a1 += [TubeSequence.makeBlank(longest) for x in range(diff)]
        elif len(a1) > len(a2):
            diff = len(a1) - len(a2)
            longest = max(map(lambda x: x.length(), a2))
            a2 += [TubeSequence()]*diff

        new_a = [x + y for x, y in zip(a1, a2)]
        return TubeAnimation(new_a)

    def __iadd__(self, other):
        ## Fix difference in number of tubes
        self.equalize(self.animations)
        animations = list(other.animations) ## make copy
        if len(animations) > len(self.animations):
            diff = len(animations) - len(self.animations)
            longest = max(map(lambda x: x.length(), self.animations))
            self.animations += [TubeSequence.makeBlank(longest)]*diff
        elif len(self.animations) > len(animations):
            diff = len(self.animations) - len(animations)
            longest = max(map(lambda x: x.length(), animations))
            animations += [TubeSequence(longest)]*diff

        for i, x in enumerate(animations):
            self.animations[i] += x

    def __eq__(self, other):
        if other is None:
            return False

        return (self.animations == other.animations)

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()


class LoopedTubeAnimation(TubeAnimation):
    def __init__(self, animations: Sequence[TubeSequence], delay: float=0):
        TubeAnimation.__init__(self, animations)
        self.delay = delay
        self.last_update = time.time()

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
            self.last_update = time.time()
            return update

        now = time.time()
        if self.loopOver() and self.last_update + self.delay < now:
            self.resetTime()
            return TubeAnimation.updateFrameSet(self)

        return None

    def clone(self):
        return LoopedTubeAnimation(self.animations[:], self.delay)

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
        time_frames: List[TimeFullFrame] = []
        time_passed = 0
        for frame in frames:
            time_frames.append((time_passed, frame))
            time_passed += delay

        ## Add one blank frame
        time_frames.append((time_passed, FullFrame()))

        return cls(time_frames, **kwargs)

    def resetTime(self):
        """Reset the start time of the first frame"""
        self.start_time = time.time()
        self.frame_index = 0

    def reset(self):
        """Reset the animation"""
        self.resetTime()

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

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        now = time.time()
        next_frame_index = None
        ## Get frame that just passed
        ## Frames should be in order
        for i, time_frame in list(enumerate(self.frames))[self.frame_index:]:
            if time_frame[0] + self.start_time < now:
                next_frame_index = i + 1
                break

        if next_frame_index is not None:
            self.current_frame = self.frames[self.frame_index][1]
            self.frame_index = next_frame_index

        return (next_frame_index is not None)

    def done(self):
        return (self.frame_index == len(self.frames))

    def clone(self):
        return FullFrameAnimation(self.frames[:])


    def __eq__(self, other):
        if other is None:
            return False

        return (self.frames == other.frames)

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
        time_frames: List[TimeFullFrame] = []
        time_passed = 0
        for frame in frames:
            time_frames.append((time_passed, frame))
            time_passed += delay

        ## Don't add a blank one since we're looping

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
            self.resetTime()
            return FullFrameAnimation.updateFrameSet(self)

        return None

    def clone(self):
        return LoopedFullFrameAnimation(self.frames[:], self.delay)


def makeTextAnimation(text):
    """Create an animation set from a text string"""
    return FullFrameAnimation([(0, [TextFrame(x) for x in text])])


def makeTextSequence(msgs:Sequence[str], delay:float, *, looped=False):
    """Create an animation set from multiple text strings"""
    frames = [FullFrame([TextFrame(x) for x in msg]) for msg in msgs]
    if looped:
        return LoopedFullFrameAnimation.makeTimed(frames, delay=delay)

    return FullFrameAnimation.makeTimed(frames, delay=delay)


def makeSpinAnimation(*, rate=3, num_tubes=1, loop=True):
    """Create a spin animation"""
    frames = [HexFrame(0x1 << x) for x in range(7, 14)] + [HexFrame(0x1 << 6)]
    animations = [TubeSequence.makeTimed(frames, rate) for x in range(num_tubes)]
    if loop:
        return LoopedTubeAnimation(animations)

    return TubeAnimation(animations)
