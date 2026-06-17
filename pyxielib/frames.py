"""
Frame data layer for the Nixie display.

A Frame is the state of a single tube at one instant; a FullFrame is a row of
tubes at one instant. This module owns the frame types and the pure helpers
that combine them (overlay, the `|` tube-concatenation operator, and the
row/timeline concatenation functions). It knows nothing about timing or
playback -- that lives in pyxielib/animation.py, which builds on these types.
"""

import re
from copy import copy
from typing import Dict, Sequence, Tuple

from pyxielib import tube_manager as tm
from pyxielib.pyxieutil import PyxieError


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


def frameSpans(timed_items):
    """Convert [(delay, item), ...] into cumulative [(start, end, item), ...] spans"""
    spans = []
    clock = 0.0
    for delay, item in timed_items:
        spans.append((clock, clock + delay, item))
        clock += delay

    return spans


def spanValueAt(spans, when):
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
    left_spans = frameSpans(left_frames)
    right_spans = frameSpans(right_frames)
    left_width = max((ff.tubeCount() for _, _, ff in left_spans), default=0)
    right_width = max((ff.tubeCount() for _, _, ff in right_spans), default=0)
    boundaries = sorted({0.0} | {e for _, e, _ in left_spans} | {e for _, e, _ in right_spans})
    frames = []
    for start, end in zip(boundaries, boundaries[1:]):
        left = spanValueAt(left_spans, start)
        right = spanValueAt(right_spans, start)
        tubes = _padTubes(left, left_width) + _padTubes(right, right_width)
        frames.append((end - start, FullFrame(tubes)))

    return frames
