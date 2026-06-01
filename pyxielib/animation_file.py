import logging
import os
import re

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pyxielib.animation import (
    Frame, FullFrame, HexFrame, FullFrameAnimation,
    PixieAnimationError, TimeFullFrame,
    textToFrames,
)
from pyxielib.pyxieutil import PyxieError, strToInt

logger = logging.getLogger(__name__)


class FileAnimationError(PixieAnimationError):
    """Use for errors found when parsing an animation file"""


class ArgSpec:
    """Declares the argument shape of a DSL command.

    A command takes ``required`` positional arguments, up to ``optional`` more
    positional arguments (``None`` for unlimited), and any of the named
    arguments in ``named`` (a mapping of name to default value). In a command
    call, positional arguments must come before named arguments, named
    arguments are always written ``name=value`` (never positionally), and named
    arguments may appear in any order.
    """

    def __init__(self, required, optional=0, named: Optional[Dict[str, Any]] = None, handler=None):
        self.required = required        ## minimum number of positional arguments
        self.optional = optional        ## extra positional slots allowed (None = unlimited)
        ## Map of named-argument name -> default value used when it is omitted.
        ## An empty map means the command takes no named arguments at all, so
        ## its fields are never split on '=' (see FileAnimation._bindArgs).
        self.named: Dict[str, Any] = named or {}
        self.handler = handler          ## function invoked as handler(*positional, **named)


class FileAnimation(FullFrameAnimation):
    def __init__(self, path, size=16):
        self.path          = path
        self.size          = size
        self.scale         = 1
        self.sequence      = None
        self._anon_args    = None  ## (shift, repeat, scale) insert args during sequence|anon
        self._repeat:      Optional[Tuple[int, List]] = None  ## (count, saved_active) during repeat|start/end
        ## (name, [segments], scale) during a flatten block. A named block has
        ## name set and scale None; an anonymous block (flatten|anon) has name
        ## None and scale set to the delay of the frame inserted at flatten|end.
        self._flatten:     Optional[Tuple[Optional[str], List, Optional[float]]] = None
        ## (name, [(frames, shift)], anon_args) during a merge block. A named
        ## block has name set and anon_args None; an anonymous block (merge|anon)
        ## has name None and anon_args set to the (shift, repeat, scale) insert
        ## arguments applied to the merged sequence at merge|end.
        self._merge:       Optional[Tuple[Optional[str], List, Optional[Tuple]]] = None
        self._sandbox      = None  ## SandboxParser during sandbox|start/end
        self._skip_block   = None  ## 'sequence'/'flatten'/'sandbox' while skipping a disabled block
        self._library_mode: bool = False
        ## Cache shared down the import tree: filename -> parsed library object,
        ## or None while a file is still being parsed (marks a circular import).
        self._imported:    Dict[str, Optional['FileAnimation']] = {}
        self.sprites:      Dict[str, Frame] = {}
        self.sequences:    Dict[str, List[TimeFullFrame]] = {}
        self.segments:     Dict[str, List[Frame]] = {}
        self.fullframes:   List[TimeFullFrame] = []
        self.active:       List[TimeFullFrame] = self.fullframes
        FullFrameAnimation.__init__(self, self.loadFrames(path))

    @classmethod
    def _load_as_library(cls, path, imported: Dict[str, Optional['FileAnimation']], size=16) -> 'FileAnimation':
        """Parse a .ani file as a library (sprites/segments/sequences only)."""
        obj = object.__new__(cls)
        obj.path          = path
        obj.size          = size
        obj.scale         = 1
        obj.sequence      = None
        obj._anon_args    = None
        obj._repeat       = None
        obj._flatten      = None
        obj._merge        = None
        obj._sandbox      = None
        obj._skip_block   = None
        obj._library_mode = True
        obj._imported     = imported
        obj.sprites       = {}
        obj.sequences     = {}
        obj.segments      = {}
        obj.fullframes    = []
        obj.active        = obj.fullframes
        try:
            with open(path, 'r') as f:
                obj._loadFramesHelper(f)
        except FileAnimationError:
            raise
        except Exception as e:
            raise FileAnimationError(f"Failed to load library '{path}': {e}")
        return obj

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
        sandbox_pending = ''  ## carries sandbox lines joined by a trailing '\'
        for line in ani_file:
            ## Get command and arguments from line
            line_no += 1
            line = re.sub(r"\s*(?:#.*)", '', line)  ## Remove comments from line
            line = line.strip()
            if not line:
                continue

            ## Route every line of an open sandbox block to its parser
            if self._sandbox is not None:
                sandbox_pending = self._routeSandboxLine(line, sandbox_pending, line_no, errors)
                continue

            ## A disabled block (sequence|disable / flatten|disable /
            ## sandbox|disable) swallows every line, unparsed, until its
            ## matching '<type>|end'.
            if self._skip_block is not None:
                parts = line.split('|')
                if parts[0] == self._skip_block and len(parts) > 1 and parts[1] == 'end':
                    logger.debug(f"End of disabled {self._skip_block} block")
                    self._skip_block = None
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

            ## Anonymous line (|...) inside a flatten or merge block: a flatten
            ## block expects one segment content field, a merge block expects a
            ## sequence name plus optional named arguments.
            if cmd == '':
                if self._flatten is not None:
                    if len(args) != 1:
                        errors.append((line_no, "Anonymous segment line must have exactly one content field after '|'"))
                        continue
                    try:
                        self._flatten[1].append(self._parseSegmentHlpr(args[0]))
                    except FileAnimationError as e:
                        errors.append((line_no, e.what()))
                elif self._merge is not None:
                    try:
                        self._parseMergeLine(args)
                    except FileAnimationError as e:
                        errors.append((line_no, e.what()))
                else:
                    errors.append((line_no, "Anonymous lines ('|...') are only valid inside a flatten or merge block"))
                continue

            ## '<block>|disable' turns a whole block into a no-op: its arguments
            ## are ignored entirely (never validated) and every line through the
            ## matching '<block>|end' is skipped (handled at the top of the loop).
            if cmd in ('sequence', 'flatten', 'sandbox') and args and args[0] == 'disable':
                try:
                    self._disableBlock(cmd)
                except FileAnimationError as e:
                    errors.append((line_no, e.what()))
                continue

            ## Each command's argument shape (positional counts + named args)
            ## is declared in an ArgSpec; _bindArgs validates the raw fields
            ## against it before the handler runs.
            handlers = {
                'sprite':   ArgSpec(2, 0, handler=self._parseSprite),
                'segment':  ArgSpec(2, 0, handler=self._parseSegment),
                'frame':    ArgSpec(2, 0, handler=self._parseFrame),
                'scale':    ArgSpec(1, 0, handler=self._parseScale),
                'sequence': ArgSpec(1, 1, named={'shift': '0', 'repeat': '1', 'scale': None}, handler=self._parseSequence),
                'repeat':   ArgSpec(1, 1, handler=self._parseRepeat),
                'flatten':  ArgSpec(1, 1, handler=self._parseFlatten),
                'merge':    ArgSpec(1, 1, named={'shift': '0', 'repeat': '1', 'scale': None}, handler=self._parseMerge),
                'import':   ArgSpec(1, 1, handler=self._parseImport),
                'sandbox':  ArgSpec(1, 0, handler=self._parseSandbox),
            }
            try:
                if cmd not in handlers:
                    errors.append((line_no, f"No such command '{cmd}'"))
                    continue

                ## Split the fields into positional values and a named dict
                ## (with defaults filled in), then dispatch to the handler.
                spec = handlers[cmd]
                positional, named = self._bindArgs(cmd, spec, args)
                spec.handler(*positional, **named)
            except PixieAnimationError as e:
                ## Catch the whole animation-error family (not just
                ## FileAnimationError) so every parse failure is recorded with
                ## its line number instead of aborting the parse uncaught.
                errors.append((line_no, e.what()))
                continue

        if self._sandbox is not None:
            errors.append((line_no, "Sandbox block was not closed with 'sandbox|end'"))

        if self._skip_block is not None:
            errors.append((line_no, f"Disabled {self._skip_block} block was not closed with '{self._skip_block}|end'"))

        if errors:
            num = len(errors)
            errors = [f"Line {l_no}: {msg}" for l_no, msg in errors]
            raise FileAnimationError(f"Found {num} errors:\n" + "\n".join(errors))

        return self.fullframes

    def _routeSandboxLine(self, line, pending, line_no, errors):
        """Feed one line to the open sandbox, joining lines that end with '\\'.

        Returns the continuation buffer to carry into the next line.
        """
        if line == 'sandbox|end':
            if pending:
                errors.append((line_no, "Sandbox line continuation '\\' has no following line"))
            try:
                self._endSandbox()
            except FileAnimationError as e:
                errors.append((line_no, e.what()))
            return ''

        line = pending + line
        if line.endswith('\\'):
            return line[:-1]

        try:
            self._sandbox.parseLine(line)
        except FileAnimationError as e:
            errors.append((line_no, e.what()))
        return ''

    @staticmethod
    def _bindArgs(cmd, spec, args: Sequence[str]) -> Tuple[List[str], Dict[str, Any]]:
        """Split a command's raw fields into positional and named arguments.

        Positional fields must precede named ones. A field of the form
        ``key=value`` is a named argument; the command must declare ``key`` in
        its ``ArgSpec``, and named arguments may appear in any order. Missing
        named arguments are filled with their declared defaults. Only commands
        that declare named arguments parse ``key=value`` fields, so commands
        like ``frame`` may carry an '=' in their content.
        """
        positional: List[str] = []
        named: Dict[str, Any] = {}
        for arg in args:
            ## A field is a named argument only when the command declares any
            ## (spec.named) and the field looks like 'key=value'. Commands with
            ## no named arguments skip this entirely, so '=' stays literal.
            match = re.match(r"([A-Za-z]\w*)=(.*)$", arg) if spec.named else None
            if match:
                key, value = match.group(1), match.group(2)
                if key not in spec.named:
                    raise FileAnimationError(f"Command '{cmd}' has no named argument '{key}'")
                if key in named:
                    raise FileAnimationError(f"Command '{cmd}' got multiple values for named argument '{key}'")
                named[key] = value
            elif named:
                ## Seeing a positional field after a named one breaks the
                ## "positional before named" ordering rule.
                raise FileAnimationError(f"Command '{cmd}' has positional argument '{arg}' after named arguments")
            else:
                positional.append(arg)

        ## Positional count must land within [required, required + optional]
        num = len(positional)
        max_num = None if spec.optional is None else spec.required + spec.optional
        if num < spec.required or (max_num is not None and num > max_num):
            opt = 'unlimited' if spec.optional is None else spec.optional
            raise FileAnimationError(
                f"Command '{cmd}' takes {spec.required} required arguments and {opt} optional ones"
            )

        ## Fill in defaults for any named argument the call left out so the
        ## handler always receives every named argument as a keyword.
        for key, default in spec.named.items():
            named.setdefault(key, default)

        return positional, named

    def _parseSprite(self, name, code):
        """Parse a sprite line"""
        ## Convert code to int
        try:
            code = strToInt(code)
        except Exception as e:
            raise FileAnimationError("Failed to convert sprite code: " + str(e))

        self.sprites[name] = HexFrame(code)
        logger.debug(f"Found sprite '{name}'")

    def _parseSegmentHlpr(self, line) -> List[Frame]:
        """Parse a frame line"""
        ## Tokenize the frames
        tokens = self._tokenize(line)
        frames = []
        for t_type, token in tokens:
            if t_type == 'literal':
                ## Treat token as plain text
                frames.extend(textToFrames(token))
            elif t_type == 'macro':
                ## Look up token as defined symbol
                if token in self.sprites:
                    frames.append(self.sprites[token])
                elif token in self.segments:
                    frames.extend(self.segments[token])
                else:
                    raise FileAnimationError(f"Symbol '{token}' not defined")
            elif t_type == 'hex':
                frames.append(HexFrame(strToInt(token)))
            elif t_type == 'multiplier':
                ## Multiply the previously defined token
                if token == 0:
                    raise FileAnimationError(f"Multiplier must be a positive integer")
                if not frames:
                    raise FileAnimationError(f"No previous frame to multiply")
                ## Add token-1 more of the last frame
                for _ in range(token-1):
                    frames.append(frames[-1])
            else:
                raise PyxieError(f"Unrecognized token type '{t_type}'")

        return frames

    def _parseSegment(self, name, line):
        if name in self.segments:
            raise FileAnimationError(f"Segment '{name}' already exists")

        self.segments[name] = self._parseSegmentHlpr(line)

    def _parseFrame(self, length, line):
        if self._library_mode and self.sequence is None:
            raise FileAnimationError("frame is only valid inside a sequence in library files")

        ## Parse first argument. Frames inside a sequence keep their raw delay;
        ## scale is applied later by sequence|insert. Frames added directly to
        ## the animation have no insert step, so scale is applied here.
        try:
            length = float(length)
        except:
            raise FileAnimationError(f"Argument 'delay' must be a float, not '{length}'")
        if self.sequence is None:
            length *= self.scale

        frames = self._parseSegmentHlpr(line)

        ## Fix number of frames
        num_frames = len(frames)
        if num_frames > self.size:
            frames = frames[:self.size]
        elif num_frames < self.size:
            missing = self.size - num_frames
            frames += [Frame()]*missing

        if length:
            ## Add the frames
            self.active.append((length, FullFrame(frames)))
        else:
            ## Overlay the frames
            logger.debug("%s", self.active[-1])
            logger.debug("%s", FullFrame(frames))
            self.active[-1] = (self.active[-1][0], self.active[-1][1].overlay(FullFrame(frames)))
            logger.debug("%s", self.active[-1])

    def _parseScale(self, scale):
        """Parse a sprite line"""
        ## Convert code to int
        try:
            self.scale = float(scale)
            logger.debug(f"Setting scale {self.scale}")
        except Exception as e:
            raise FileAnimationError("Failed to convert scale to float: " + str(e))

    def _shiftFullFrame(self, full_frame: FullFrame, shift: int) -> FullFrame:
        frames = full_frame.getFrames()
        if shift > 0:
            frames = [Frame()] * shift + frames
            frames = frames[:self.size]
        elif shift < 0:
            frames = frames[abs(shift):]
        missing = self.size - len(frames)
        if missing > 0:
            frames += [Frame()] * missing
        return FullFrame(frames)

    def _parseSequence(self, subcmd, name=None, shift='0', repeat='1', scale=None):
        if subcmd == 'start':
            if name is None:
                raise FileAnimationError("Cannot start a sequence without a name")
            if self.sequence is not None:
                raise FileAnimationError(f"Cannot start new sequence '{name}' before finishing the current one")
            if self._repeat is not None:
                raise FileAnimationError("Cannot start a named sequence inside a repeat block")
            if name in self.sequences:
                raise FileAnimationError(f"Sequence already exists with name '{name}'")

            sequence = []
            self.sequences[name] = sequence
            self.active = sequence
            self.sequence = name
            logger.debug(f"Starting sequence '{name}'")
        elif subcmd == 'anon':
            if name is not None:
                raise FileAnimationError("sequence|anon takes no name, only insert arguments")
            if self.sequence is not None:
                raise FileAnimationError("Cannot start an anonymous sequence before finishing the current one")
            if self._repeat is not None:
                raise FileAnimationError("Cannot start an anonymous sequence inside a repeat block")

            ## Build into a throwaway list; sequence|end inserts it with these args
            self.active = []
            self.sequence = '<anon>'
            self._anon_args = (shift, repeat, scale)
            logger.debug("Starting anonymous sequence")
        elif subcmd == 'end':
            if self.sequence is None:
                raise FileAnimationError("There is no sequence to end")

            closed = self.sequence
            frames = self.active
            anon_args = self._anon_args
            self.sequence = None
            self._anon_args = None
            self.active = self.fullframes
            if anon_args is not None:
                ## Anonymous block: insert the just-built frames exactly as if
                ## the sequence had been named and immediately inserted here.
                shift, repeat, scale = anon_args
                self._appendInsertedFrames(frames, shift, repeat, scale, "anonymous sequence")
            logger.debug(f"Completed sequence '{closed}'")
        elif subcmd == 'insert':
            self._insertSequence(name, shift, repeat, scale)
        else:
            raise FileAnimationError(f"Unknown sequence subcommand '{subcmd}'")

    def _disableBlock(self, kind):
        """Skip a disabled block. The parse loop swallows every line until the
        matching '<kind>|end'; all of the disable line's arguments are ignored.
        """
        if self.sequence is not None:
            raise FileAnimationError(f"Cannot disable a {kind} block inside an open sequence")
        if self._repeat is not None:
            raise FileAnimationError(f"Cannot disable a {kind} block inside a repeat block")
        if self._flatten is not None:
            raise FileAnimationError(f"Cannot disable a {kind} block inside a flatten block")
        self._skip_block = kind
        logger.debug(f"Disabling {kind} block")

    def _insertSequence(self, name, shift, repeat, scale):
        """Append a previously-defined sequence to the active frame list."""
        if name not in self.sequences:
            raise FileAnimationError(f"Sequence '{name}' doesn't exist")
        self._appendInsertedFrames(self.sequences[name], shift, repeat, scale, f"sequence '{name}'")

    def _appendInsertedFrames(self, frames, shift, repeat, scale, label):
        """Transform a sequence's frames by the insert arguments and append them.

        ``shift`` slides each frame along the tube axis, ``scale`` multiplies
        each frame's delay (defaulting to the file's current scale), and
        ``repeat`` controls how many copies of the (shifted, scaled) sequence
        are appended. The arguments arrive as raw strings (from _bindArgs for
        sequence|insert, or from the sequence|anon line).
        """
        ## Convert the raw string arguments to numbers
        shift_n  = self._intArg('sequence|insert shift', shift)
        repeat_n = self._intArg('sequence|insert repeat', repeat)
        if repeat_n < 1:
            raise FileAnimationError("sequence|insert repeat must be a positive integer")
        ## scale defaults to None from the ArgSpec, meaning "use the file scale"
        scale_f = self.scale if scale is None else self._floatArg('sequence|insert scale', scale)

        ## Build the transformed copy once, then append it repeat_n times.
        ## Each entry is a (delay, FullFrame) tuple, so shift rewrites the frame
        ## and scale rewrites the delay.
        if shift_n:
            frames = [(t, self._shiftFullFrame(f, shift_n)) for t, f in frames]
        if scale_f != 1:
            frames = [(t * scale_f, f) for t, f in frames]
        for _ in range(repeat_n):
            self.active.extend(frames)
        logger.debug(f"Inserted {label} (shift={shift_n}, repeat={repeat_n}, scale={scale_f})")

    @staticmethod
    def _intArg(label, value) -> int:
        """Parse a named-argument string as an int, or raise with ``label``"""
        try:
            return int(value)
        except (TypeError, ValueError):
            raise FileAnimationError(f"{label} must be an integer, not '{value}'")

    @staticmethod
    def _floatArg(label, value) -> float:
        """Parse a named-argument string as a float, or raise with ``label``"""
        try:
            return float(value)
        except (TypeError, ValueError):
            raise FileAnimationError(f"{label} must be a float, not '{value}'")

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

    def _flattenSegments(self, segments: List[List[Frame]]) -> List[Frame]:
        result = []
        for i in range(self.size):
            ## Collect the frame at tube position i from every segment
            tube_frames = [seg[i] for seg in segments if i < len(seg)]

            ## Filter to non-blank frames
            non_blank = [f for f in tube_frames if f.getCode().strip()]

            if not non_blank:
                result.append(Frame())
            elif len(non_blank) == 1:
                result.append(non_blank[0])
            else:
                ## Overlay all non-blank frames as hex bitmaps
                try:
                    combined = HexFrame(non_blank[0].decode())
                    for f in non_blank[1:]:
                        combined = combined.overlay(HexFrame(f.decode()))
                except Exception as e:
                    raise FileAnimationError(f"Failed to overlay frames at tube {i}: {e}")
                result.append(combined)

        return result

    def _parseFlatten(self, subcmd, arg=None):
        if subcmd == 'start':
            if arg is None:
                raise FileAnimationError("flatten|start requires a name")
            if self._flatten is not None:
                raise FileAnimationError("Cannot nest flatten blocks")
            if arg in self.segments:
                raise FileAnimationError(f"Segment '{arg}' already exists")
            self._flatten = (arg, [], None)
            logger.debug(f"Starting flatten block '{arg}'")
        elif subcmd == 'anon':
            if arg is None:
                raise FileAnimationError("flatten|anon requires a scale")
            if self._flatten is not None:
                raise FileAnimationError("Cannot nest flatten blocks")
            scale = self._floatArg('flatten|anon scale', arg)
            self._flatten = (None, [], scale)
            logger.debug(f"Starting anonymous flatten block (scale={scale})")
        elif subcmd == 'end':
            if self._flatten is None:
                raise FileAnimationError("No flatten block to end")
            name, segs, scale = self._flatten
            self._flatten = None
            frames = self._flattenSegments(segs)
            if name is None:
                ## Anonymous block: insert the flattened frame straight into the
                ## active animation, right after the block. Like a direct frame,
                ## its delay is multiplied by the file scale (unless it lands in
                ## a sequence, which applies the scale itself at insert time).
                delay = scale if self.sequence is not None else scale * self.scale
                self.active.append((delay, FullFrame(frames)))
                logger.debug(f"Inserted anonymous flattened frame from {len(segs)} segments (delay={delay})")
            else:
                self.segments[name] = frames
                logger.debug(f"Flattened {len(segs)} segments into '{name}'")
        else:
            raise FileAnimationError(f"Unknown flatten subcommand '{subcmd}'")

    def _parseMerge(self, subcmd, name=None, shift='0', repeat='1', scale=None):
        if subcmd == 'start':
            if name is None:
                raise FileAnimationError("Cannot start a merge block without a name")
            if self._merge is not None:
                raise FileAnimationError("Cannot nest merge blocks")
            if self.sequence is not None:
                raise FileAnimationError(f"Cannot start merge block '{name}' before finishing the current sequence")
            if self._repeat is not None:
                raise FileAnimationError("Cannot start a named merge block inside a repeat block")
            if self._flatten is not None:
                raise FileAnimationError("Cannot start a merge block inside a flatten block")
            if name in self.sequences:
                raise FileAnimationError(f"Sequence already exists with name '{name}'")
            self._merge = (name, [], None)
            logger.debug(f"Starting merge block '{name}'")
        elif subcmd == 'anon':
            if name is not None:
                raise FileAnimationError("merge|anon takes no name, only insert arguments")
            if self._merge is not None:
                raise FileAnimationError("Cannot nest merge blocks")
            if self.sequence is not None:
                raise FileAnimationError("Cannot start an anonymous merge block before finishing the current sequence")
            if self._repeat is not None:
                raise FileAnimationError("Cannot start an anonymous merge block inside a repeat block")
            if self._flatten is not None:
                raise FileAnimationError("Cannot start a merge block inside a flatten block")
            ## Collect into a throwaway list; merge|end inserts the merged
            ## sequence with these args, exactly like sequence|anon.
            self._merge = (None, [], (shift, repeat, scale))
            logger.debug("Starting anonymous merge block")
        elif subcmd == 'end':
            if self._merge is None:
                raise FileAnimationError("No merge block to end")
            name, parts, anon_args = self._merge
            self._merge = None
            frames = self._mergeSequences(parts)
            if anon_args is not None:
                ## Anonymous block: insert the merged sequence just as if it had
                ## been named and immediately inserted here.
                shift, repeat, scale = anon_args
                self._appendInsertedFrames(frames, shift, repeat, scale, "anonymous merge")
            else:
                self.sequences[name] = frames
                logger.debug(f"Merged {len(parts)} sequences into '{name}'")
        else:
            raise FileAnimationError(f"Unknown merge subcommand '{subcmd}'")

    def _parseMergeLine(self, args: Sequence[str]):
        """Parse one '|name[|shift=N][|pad=N]' line inside a merge block."""
        spec = ArgSpec(1, 0, named={'shift': '0', 'pad': '0'})
        positional, named = self._bindArgs('merge line', spec, args)
        name = positional[0]
        if name not in self.sequences:
            raise FileAnimationError(f"Sequence '{name}' doesn't exist")
        shift = self._intArg('merge shift', named['shift'])
        pad = self._intArg('merge pad', named['pad'])
        if pad < 0:
            raise FileAnimationError("merge pad must be a non-negative integer")
        self._merge[1].append((self.sequences[name], shift, pad))

    def _mergeSequences(self, parts) -> List[TimeFullFrame]:
        """Merge several sequences into one by overlaying frames step by step.

        Each part is a ``(frames, shift, pad)`` triple. ``shift`` slides that
        sequence along the tube axis before merging (like sequence|insert's
        shift). ``pad`` prepends that many blank frames to the start of the
        sequence; the blank frames' delays are inferred from the sequences
        processed before it. Processing runs in increasing ``pad`` order, so
        less-padded sequences (there must be at least one with ``pad=0``) define
        the delays of the padded steps, and a sequence may not be padded past
        the end of every already-processed sequence.

        Once aligned, sequences with the same shape — the frames at a given step
        share a delay — are overlaid per tube (exactly like flatten); shorter
        sequences are blank-padded at the end out to the longest length.
        """
        ## Apply each part's shift up front so the rest works on aligned frames
        shifted = []
        for frames, shift, pad in parts:
            if shift:
                frames = [(t, self._shiftFullFrame(f, shift)) for t, f in frames]
            shifted.append((frames, pad))

        if shifted and not any(pad == 0 for _, pad in shifted):
            raise FileAnimationError("A merge block must include at least one sequence with no pad")

        ## step_delays[i] is the delay of step i, defined as already-processed
        ## (less-padded) sequences contribute their real frames. _frontPad reads
        ## it to fill the inferred blank delays and extends it as it goes.
        step_delays: List[float] = []
        padded = [self._frontPad(frames, pad, step_delays)
                  for frames, pad in sorted(shifted, key=lambda fp: fp[1])]

        return self._overlaySequences(padded, len(step_delays))

    def _frontPad(self, frames, pad, step_delays: List[float]) -> List[TimeFullFrame]:
        """Prepend ``pad`` blank frames to a sequence, inferring their delays
        from ``step_delays`` (built by earlier, less-padded sequences), then
        extend ``step_delays`` with this sequence's own frame delays."""
        if pad > len(step_delays):
            raise FileAnimationError(
                f"Cannot pad a sequence by {pad}: only {len(step_delays)} step(s) "
                "are defined by earlier (less-padded) sequences"
            )
        full = [(step_delays[j], FullFrame()) for j in range(pad)] + list(frames)
        ## Everything past the known region is a real frame extending the timeline
        step_delays.extend(delay for delay, _ in full[len(step_delays):])
        return full

    def _overlaySequences(self, sequences, max_len) -> List[TimeFullFrame]:
        """Overlay length-aligned sequences step by step (per tube, like flatten)."""
        result: List[TimeFullFrame] = []
        for i in range(max_len):
            ## Sequences shorter than this step are absent (blank-padded) and so
            ## don't constrain the delay; the rest must all agree on it.
            present = [seq[i] for seq in sequences if i < len(seq)]
            delays = {delay for delay, _ in present}
            if len(delays) > 1:
                raise FileAnimationError(
                    f"Cannot merge sequences: frames at step {i} have differing delays {sorted(delays)}"
                )
            segments = [full_frame.getFrames() for _, full_frame in present]
            result.append((delays.pop(), FullFrame(self._flattenSegments(segments))))

        return result

    def _parseImport(self, scale_or_path, filepath=None):
        if filepath is None:
            import_scale = 1.0
            filepath = scale_or_path
        else:
            try:
                import_scale = float(scale_or_path)
            except ValueError:
                raise FileAnimationError(f"import scale must be a float, not '{scale_or_path}'")

        base_dir = os.path.dirname(os.path.abspath(self.path))
        full_path = os.path.normpath(os.path.join(base_dir, filepath.strip()))
        filename = os.path.basename(full_path)

        ## A library is parsed at most once, but its symbols are merged into
        ## every file that imports it so the result no longer depends on import
        ## order. A None cache entry means the file is mid-parse (a circular
        ## import), which we break by skipping rather than recursing.
        if filename in self._imported:
            lib = self._imported[filename]
            if lib is None:
                logger.debug(f"Skipping circular import of library '{filename}'")
                return
            logger.debug(f"Re-using already-parsed library '{filename}'")
            self._mergeLibrary(lib, import_scale)
            return

        self._imported[filename] = None  ## mark in-progress to break import cycles
        logger.debug(f"Importing library '{full_path}' with scale={import_scale}")
        lib = FileAnimation._load_as_library(full_path, self._imported, size=self.size)
        self._imported[filename] = lib
        self._mergeLibrary(lib, import_scale)

    def _mergeLibrary(self, lib, import_scale):
        """Merge a parsed library's sprites, segments, and sequences into this file."""
        self.sprites.update(lib.sprites)
        self.segments.update(lib.segments)
        for name, frames in lib.sequences.items():
            self.sequences[name] = [(t * import_scale, f) for t, f in frames]
            logger.debug(f"Imported sequence '{name}'")

    def _parseSandbox(self, subcmd):
        if subcmd == 'start':
            if self._sandbox is not None:
                raise FileAnimationError("Cannot nest sandbox blocks")
            if self.sequence is not None or self._repeat is not None or self._flatten is not None:
                raise FileAnimationError("Cannot start a sandbox block inside another block")

            ## Imported lazily to avoid a circular import with animation_sandbox
            from pyxielib.animation_sandbox import SandboxParser
            self._sandbox = SandboxParser(scale=self.scale, size=self.size)
            logger.debug("Starting sandbox block")
        elif subcmd == 'end':
            self._endSandbox()
        else:
            raise FileAnimationError(f"Unknown sandbox subcommand '{subcmd}'")

    def _endSandbox(self):
        if self._sandbox is None:
            raise FileAnimationError("No sandbox block to end")

        sandbox = self._sandbox
        self._sandbox = None
        self.active.extend(sandbox.fullFrames())
        logger.debug(f"Ended sandbox block ({len(sandbox.printed)} printed animations)")

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
