import time

from typing import List, Sequence, Tuple

from .pyxieutil import PyxieError


class PixieAnimationError(PyxieError):
    pass


class Frame:
    def __init__(self):
        pass

    def getCode(self):
        return ' '


class TextFrame(Frame):
    def __init__(self, text, colon=False, underline=False):
        self.text       = text
        self.colon      = colon
        self.underline = underline
        if not self.text:
            self.text = ' '
        elif len(self.text) > 1:
            raise PixieAnimationError("TextFrame cannot only take string length 1")

    def getCode(self):
        code = self.text[0]
        if self.colon:
            text += ':'
        if self.underline:
            text += '!'

        return code


TimeFrame = Tuple[float, Frame]


class TubeAnimation:
    def __init__(self, frames: Sequence[TimeFrame]=None):
        self.start_time: float = time.time()
        self.frames: List[TimeFrame] = list(frames or []) ## Make a copy
        self.frame_index = 0

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
            if f_time > now:
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
        time_frame = nextTimeFrame()
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
            if time_frame[0] <= now:
                next_frame = time_frame[1]
            else:
                break

        if next_frame_index is not None:
            self.frame_index = next_frame_index

        return next_frame


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
        return len(self.current_set)

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


class TextAnimation(AnimationSet):
    def __init__(self, text):
        AnimationSet.__init__(self, [TubeAnimation([(0, TextFrame(x))]) for x in text])
