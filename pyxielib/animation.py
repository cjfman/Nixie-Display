import time

from typing import List, Sequence, Tuple

from pyxielib.pyxieutil import PyxieError


class PixieAnimationError(PyxieError):
    pass


class Frame:
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
    def __init__(self, hex_code=0x0):
        Frame.__init__(self, '{' + hex(0xFFFF & hex_code) + '}')


class TextFrame(Frame):
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


TimeFrame = Tuple[float, Frame]


class Animation:
    def __init__(self, frames: Sequence[TimeFrame]=None):
        self.start_time: float = time.time()
        self.frames: List[TimeFrame] = list(frames or []) ## Make Copy
        self.frame_index = 0

    @classmethod
    def makeBlank(cls, length: float=0):
        """Make a blank tube animation of a certain length"""
        return cls([(length, Frame())])

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

    def length(self):
        """Time length of animation"""
        delay = 0
        frames_len = len(self.frames)
        if frames_len >= 2:
            delay = self.frames[-1][0] - self.frames[-2][0]
        elif not frames_len:
            return 0

        return delay + self.frames[-1][0]

    def nextTimeFrame(self) -> TimeFrame:
        """Next time and frame"""
        now = time.time()
        ## Get first frame that hasn't passed
        ## Frames should be in order
        for f_time, frame in self.frames[self.frame_index:]:
            if f_time + self.start_time > now:
                return (f_time, frame)

        return None

    def nextFrame(self):
        """Next frame"""
        time_frame = self.nextTimeFrame()
        if time_frame is None:
            return None

        return time_frame[1]

    def nextTime(self):
        """Time of next frame"""
        time_frame = self.nextTimeFrame()
        if time_frame is None:
            return None

        return time_frame[0]

    def popFrame(self):
        """Get the next frame and adjust index"""
        if not self.remainingFrames():
            return None

        now = time.time()
        next_frame_index = None
        next_frame = None
        ## Get frame that just passed
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
        return Animation(frames1 + frames2)

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

    def __eq__(self, other):
        """Equals boolean. Only considers frames and time diffs"""
        return (self.frames == other.frames)

    def __str__(self):
        return f"Start time {self.start_time}: " + str(self.frames)

    def __repr__(self):
        return str(self)


class TimedAnimation(Animation):
    def __init__(self, frames: Frame, rate: int=1, *, delay: float=0):
        ## Delay overrides rate
        if not delay:
            delay = 1 / rate

        ## Calculate time passed
        time_frames: List[TimeFrame] = []
        time_passed = 0
        for frame in frames:
            time_frames.append((time_passed, frame))
            time_passed += delay

        Animation.__init__(self, time_frames)


class AnimationSet:
    def __init__(self, animations: Sequence[Animation], repeat=False):
        self.animations: Sequence[Animation] = animations
        self.current_frame_set: List[Frame] = [Frame()]*len(animations)
        self.repeat = repeat

    @classmethod
    def makeFromFullFrames(cls, full_frames: Sequence[Sequence[Frame]], delay: float, *args, **kwargs):
        """Take full frames and convert them to animations"""
        num_frames = len(full_frames[0])
        transposed = [list() for x in range(num_frames)]
        for full_frame in full_frames:
            if len(full_frame) != num_frames:
                raise PixieAnimationError("Number of tubes in all full frame not equal. Cannot transpose")

            for i, frame in enumerate(full_frame):
                transposed[i].append(frame)

        animations = [TimedAnimation(x, delay) for x in transposed]
        return cls(animations, *args, **kwargs)

    def resetTime(self):
        """Reset the start time of the first frame"""
        for animation in self.animations:
            animation.resetTime()

    def length(self):
        """Time length of the animation set. Equal to the lonest animation"""
        return max(map(lambda x: x.length(), self.animations))

    def tubeCount(self):
        """Get the number of tubes supported by this animation set"""
        return len(self.current_frame_set)

    def shouldRepeat(self):
        return self.repeat

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
                animation += Animation.makeBlank(diff)

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
        for tube in self.animations:
            if tube.remainingFrames():
                return False

        return True

    def __add__(self, other):
        ## Make copies
        a1 = list(self.animations)
        a2 = list(other.animations)
        self.equalize(a1)
        ## Fix difference in number of tubes
        if len(a2) > len(a1):
            diff = len(a2) - len(a1)
            longest = max(map(lambda x: x.length(), self.animations))
            a1 += [Animation.makeBlank(longest) for x in range(diff)]
        elif len(a1) > len(a2):
            diff = len(a1) - len(a2)
            longest = max(map(lambda x: x.length(), a2))
            a2 += [Animation()]*diff

        new_a = [x + y for x, y in zip(a1, a2)]
        return AnimationSet(new_a)

    def __iadd__(self, other):
        ## Fix difference in number of tubes
        self.equalize(self.animations)
        animations = list(other.animations) ## make copy
        if len(animations) > len(self.animations):
            diff = len(animations) - len(self.animations)
            longest = max(map(lambda x: x.length(), self.animations))
            self.animations += [Animation.makeBlank(longest)]*diff
        elif len(self.animations) > len(animations):
            diff = len(self.animations) - len(animations)
            longest = max(map(lambda x: x.length(), animations))
            animations += [Animation(longest)]*diff

        for i, x in enumerate(animations):
            self.animations[i] += x

    def __eq__(self, other):
        if other is None:
            return False

        return (self.animations == other.animations)


class LoopAnimationSet(AnimationSet):
    def __init__(self, animations: Sequence[Animation], delay: float=1):
        AnimationSet.__init__(self, animations)
        self.delay = delay
        self.last_update = time.time()

    def done(self):
        """The last frame has loaded. Always false for LoopAnimationSet"""
        return False

    def loopOver(self):
        """Reached the last frame of the loop"""
        return AnimationSet.done(self)

    def updateFrameSet(self):
        """Update the frame set based upon the current time. Return True if updated"""
        update = AnimationSet.updateFrameSet(self)
        if update:
            self.last_update = time.time()
            return update

        if self.loopOver() and self.last_update + self.delay < time.time():
            self.resetTime()
            return AnimationSet.updateFrameSet(self)

        return None


def makeTextAnimation(text):
    """Create an animation set from a text string"""
    return AnimationSet([Animation([(0, TextFrame(x))]) for x in text])

def makeTextSequence(msgs: Sequence[str], delay: float):
    """Create an animation set from a text strings"""
    full_frames = [[Frame(x) for x in msg] for msg in msgs]
    return AnimationSet.makeFromFullFrames(full_frames, delay)


def makeSpinAnimation(*, rate=3, num_tubes=1, loop=True):
    """Create a spin animation"""
    frames = [HexFrame(0x1 << x) for x in range(7, 14)] + [HexFrame(0x1 << 6)]
    animations = [TimedAnimation(frames, rate) for x in range(num_tubes)]
    if loop:
        return LoopAnimationSet(animations, 1/rate)

    return AnimationSet(animations)
