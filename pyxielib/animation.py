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


class HexFrame(Frame):
    def __init__(self, hex_code=0x0):
        Frame.__init__(self, '{' + hex(0xFFFF & hex_code) + '}')


class TextFrame(Frame):
    def __init__(self, text, colon=False, underline=False):
        if len(text) > 1:
            raise PixieAnimationError("TextFrame cannot only take string length 1")

        code = text[0]
        if colon:
            code += ':'
        if underline:
            code += '!'

        Frame.__init__(self,code)


TimeFrame = Tuple[float, Frame]


class TubeAnimation:
    def __init__(self, frames: Sequence[TimeFrame]=None):
        self.start_time: float = time.time()
        self.frames: List[TimeFrame] = []
        self.frame_index = 0

        ## Calculate time passed
        frames = frames or []
        time_passed = 0
        for time_offset, frame in frames:
            self.frames.append((time_passed, frame))
            time_passed += time_offset

    def resetTime(self):
        """Reset the start time of the first frame"""
        self.start_time = time.time()
        self.frame_index = 0

    def frameCount(self):
        """Total frame count"""
        return len(self.frames)

    def remainingFrames(self):
        """Number of frames from now to end"""
        return max(0, len(self.frames) - self.frame_index)

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

    def __str__(self):
        return f"Start time {self.start_time}: " + str(self.frames)

    def __repr__(self):
        return str(self)


class AnimationSet:
    def __init__(self, animations: Sequence[TubeAnimation]):
        self.animations: Sequence[TubeAnimation] = animations
        self.current_frame_set: List[TubeAnimation] = [Frame()]*len(animations)

    def resetTime(self):
        """Reset the start time of the first frame"""
        for animation in self.animations:
            animation.resetTime()

    def tubeCount(self):
        """Get the number of tubes supported by this animation set"""
        return len(self.current_frame_set)

    def currentFrameSet(self) -> Sequence[Frame]:
        """Get the currently assembled frame"""
        return list(self.current_frame_set)

    def getCode(self):
        frames = self.currentFrameSet()
        return ''. join([frame.getCode() for frame in frames])

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


class LoopAnimationSet(AnimationSet):
    def __init__(self, animations: Sequence[TubeAnimation], delay: float=1):
        AnimationSet.__init__(self, animations)
        self.delay = delay
        self.last_update = time.time()

    def framesRemaining(self):
        total = 0
        for tube in self.animations:
            total += tube.remainingFrames()

        return total

    def updateFrameSet(self):
        update = AnimationSet.updateFrameSet(self)
        if update:
            self.last_update = time.time()
            return update

        if self.last_update + self.delay < time.time():
            self.resetTime()
            return AnimationSet.updateFrameSet(self)

        return None


class TextAnimation(AnimationSet):
    def __init__(self, text):
        AnimationSet.__init__(self, [TubeAnimation([(0, TextFrame(x))]) for x in text])


class SpinAnimation(AnimationSet):
    def __init__(self, *, speed=0.5, num_tubes=1):
        frames = [(speed, HexFrame(0x1 << x)) for x in range(7, 14)] + [(speed, HexFrame(0x1 << 6))]
        animations = [TubeAnimation(frames) for x in range(num_tubes)]
        AnimationSet.__init__(self, animations)
