import logging
import re

from typing import Dict, List, Optional, Tuple

from pyxielib.animation import (
    Frame, FullFrame, HexFrame, FullFrameAnimation,
    PixieAnimationError, TimeFullFrame,
    textToFrames,
)
from pyxielib.pyxieutil import PyxieError, strToInt

logger = logging.getLogger(__name__)


class FileAnimationError(PixieAnimationError):
    """Use for errors found when parsing an animation file"""


class FileAnimation(FullFrameAnimation):
    def __init__(self, path, size=16):
        self.path    = path
        self.size    = size
        self.scale   = 1
        self.sequence = None
        self._repeat:    Optional[Tuple[int, List]] = None  ## (count, saved_active) during repeat|start/end
        self.sprites:    Dict[str, Frame] = {}
        self.sequences:  Dict[str, List[TimeFullFrame]] = {}
        self.segments:   Dict[str, List[Frame]] = {}
        self.active:     List[TimeFullFrame] = None
        self.fullframes: List[TimeFullFrame] = []
        self.active = self.fullframes
        FullFrameAnimation.__init__(self, self.loadFrames(path))

    def loadFrames(self, path):
        """Load animation from a file given the path"""
        try:
            with open(path, 'r') as ani_file:
                return self._loadFramesHelper(ani_file)
        except FileAnimationError as e:
            raise PixieAnimationError(f"Failed to load animation file {path}: " + e.what()) from e
        except Exception as e:
            raise PixieAnimationError(f"Failed to load animation file {path}: " + str(e)) from e

    def _loadFramesHelper(self, ani_file):
        """Load animation from a sequence of strings"""
        ## Parse file line by line
        line_no = 0
        errors = []
        for line in ani_file:
            ## Get command and arguments from line
            line_no += 1
            line = re.sub(r"\s*(?:#.*)", '', line) ## Remove comments from line
            line = line.strip()
            if not line:
                continue

            args = line.split('|')
            if not args:
                errors.append((line_no, "Line is blank"))
                continue

            ## Parse command
            cmd = args[0]
            args = args[1:]
            if cmd == 'end':
                break

            handlers = {
                'sprite':   (2, 0, self._parseSprite),
                'segment':  (2, 0, self._parseSegment),
                'frame':    (2, 0, self._parseFrame),
                'scale':    (1, 0, self._parseScale),
                'sequence': (1, 1, self._parseSequence),
                'repeat':   (1, 1, self._parseRepeat),
            }
            try:
                if cmd not in handlers:
                    errors.append((line_no, f"No such command '{cmd}'"))
                    continue

                num_req, num_opt, hdlr = handlers[cmd]
                max_num = num_req + num_opt
                num_args = len(args)
                if num_args < num_req or num_args > max_num:
                    errors.append((line_no, f"Command '{cmd}' takes {num_req} required arguments and {num_opt} optional ones"))
                    continue

                hdlr(*args)
            except FileAnimationError as e:
                errors.append((line_no, e.what()))
                continue

        if errors:
            num = len(errors)
            errors = [f"Line {l_no}: {msg}" for l_no, msg in errors]
            raise FileAnimationError(f"Found {num} errors:\n" + "\n".join(errors))

        return self.fullframes

    def _parseSprite(self, name, code):
        """Parse a sprite line"""
        try:
            code = strToInt(code)
        except Exception as e:
            raise FileAnimationError("Failed to convert sprite code: " + str(e))

        self.sprites[name] = HexFrame(code)
        logger.debug(f"Found sprite '{name}'")

    def _parseSegmentHlpr(self, line) -> List[Frame]:
        """Parse a frame line"""
        tokens = self._tokenize(line)
        frames = []
        for t_type, token in tokens:
            if t_type == 'literal':
                frames.extend(textToFrames(token))
            elif t_type == 'macro':
                if token in self.sprites:
                    frames.append(self.sprites[token])
                elif token in self.segments:
                    frames.extend(self.segments[token])
                else:
                    raise FileAnimationError(f"Symbol '{token}' not defined")
            elif t_type == 'hex':
                frames.append(HexFrame(strToInt(token)))
            elif t_type == 'multiplier':
                if token == 0:
                    raise FileAnimationError(f"Multiplier must be a positive integer")
                if not frames:
                    raise FileAnimationError(f"No previous frame to multiply")
                for _ in range(token-1):
                    frames.append(frames[-1])
            else:
                raise PyxieError(f"Unrecognized token type '{t_type}'")

        return frames

    def _parseSegment(self, name, line):
        if name in self.segments:
            raise PyxieError(f"Segment '{name}' already exists")

        self.segments[name] = self._parseSegmentHlpr(line)

    def _parseFrame(self, length, line):
        try:
            length = float(length)*self.scale
        except:
            raise FileAnimationError(f"Argument 'delay' must be a float, not '{length}'")

        frames = self._parseSegmentHlpr(line)

        ## Fix number of frames
        num_frames = len(frames)
        if num_frames > self.size:
            frames = frames[:self.size]
        elif num_frames < self.size:
            missing = self.size - num_frames
            frames += [Frame()]*missing

        if length:
            self.active.append((length, FullFrame(frames)))
        else:
            ## Overlay the frames
            logger.debug("%s", self.active[-1])
            logger.debug("%s", FullFrame(frames))
            self.active[-1] = (self.active[-1][0], self.active[-1][1].overlay(FullFrame(frames)))
            logger.debug("%s", self.active[-1])

    def _parseScale(self, scale):
        try:
            self.scale = float(scale)
            logger.debug(f"Setting scale {self.scale}")
        except Exception as e:
            raise FileAnimationError("Failed to convert scale to float: " + str(e))

    def _parseSequence(self, subcmd, name=None):
        if subcmd == 'start':
            if name is None:
                raise PixieAnimationError("Cannot start a sequence without a name")
            if self.sequence is not None:
                raise PixieAnimationError(f"Cannot start new sequence '{name}' before finishing the current one")
            if self._repeat is not None:
                raise PixieAnimationError("Cannot start a named sequence inside a repeat block")
            if name in self.sequences:
                raise PixieAnimationError(f"Sequence already exists with name '{name}'")

            sequence = []
            self.sequences[name] = sequence
            self.active = sequence
            self.sequence = name
            logger.debug(f"Starting sequence '{name}'")
        elif subcmd == 'end':
            if self.sequence is None:
                raise PixieAnimationError("There is no sequence to end")

            self.sequence = None
            self.active = self.fullframes
            logger.debug(f"Completed sequence '{name}'")
        elif subcmd == 'insert':
            if name not in self.sequences:
                raise PixieAnimationError(f"Sequence '{name}' doesn't exist")

            self.active.extend(self.sequences[name])
            logger.debug(f"Inserted sequence '{name}'")

    def _parseRepeat(self, subcmd, count=None):
        if subcmd == 'start':
            if count is None:
                raise FileAnimationError("repeat|start requires a count")
            if self._repeat is not None:
                raise FileAnimationError("Cannot nest repeat blocks")
            try:
                n = int(count)
            except ValueError:
                raise FileAnimationError(f"repeat count must be an integer, not '{count}'")
            if n < 1:
                raise FileAnimationError("repeat count must be a positive integer")
            self._repeat = (n, self.active)
            self.active = []
            logger.debug(f"Starting repeat block (count={n})")
        elif subcmd == 'end':
            if self._repeat is None:
                raise FileAnimationError("No repeat block to end")
            count, saved_active = self._repeat
            repeat_frames = self.active
            self.active = saved_active
            for _ in range(count):
                self.active.extend(repeat_frames)
            logger.debug(f"Ended repeat block ({count}x, {len(repeat_frames)} frames each)")
            self._repeat = None
        else:
            raise FileAnimationError(f"Unknown repeat subcommand '{subcmd}'")

    @staticmethod
    def _tokenize(line):
        """Break line into tokens"""
        tokens = []
        parsed = ""
        while line:
            ## Match macro
            m = re.search(r"^\{([A-z]\w*)}", line)
            if m:
                tokens.append(('macro', m.groups()[0]))

            ## Match inline hex literal e.g. {0x1A2B}
            if m is None:
                m = re.search(r"^\{(0[xX][0-9A-Fa-f]+)}", line)
                if m:
                    tokens.append(('hex', m.groups()[0]))

            ## Match multiplier
            if m is None:
                m = re.search(r"^\{(\d+)}", line)
                if m:
                    tokens.append(('multiplier', int(m.groups()[0])))

            ## Match literal
            if m is None:
                m = re.search(r"^[^\{\}]+", line)
                if m:
                    tokens.append(('literal', m.group()))

            ## At least one match was found
            if m:
                matched = m.group()
                line = line[len(matched):]
                parsed += matched
                continue

            ## Identify error
            if line[0] in ['{', '}']:
                raise FileAnimationError(f"Found unmatched '{line[0]}: '{parsed}<<HERE>>{line}'")

            raise FileAnimationError(f"Unknown syntax error: '{parsed}<<HERE>>{line}'")

        return tokens
